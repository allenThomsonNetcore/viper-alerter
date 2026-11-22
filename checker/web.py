"""Flask dashboard and protected cron trigger for the indexing checker."""
from __future__ import annotations

import hmac
import io
import logging
import secrets
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

from checker.config import load_config
from checker.csv_loader import (GoogleSheetLoader, LocalLoader, ObjectStorageLoader, Site,
                                describe_cron, merge_site_configs, parse_sites)
from checker.service import run_checks
from checker.status_client import StatusClient
from checker.supabase_store import SupabaseAPIError, SupabaseStore

LOGGER = logging.getLogger("viper.web")
app = Flask(__name__, template_folder="templates")
_initial_config = load_config()
app.secret_key = _initial_config.flask_secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_initial_config.cookie_secure,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
_run_lock = Lock()


def _store() -> SupabaseStore:
    """Create a request-scoped Supabase REST client."""
    cfg = load_config()
    return SupabaseStore(cfg.supabase_url, cfg.supabase_service_role_key, cfg.request_timeout)


@app.before_request
def protect_routes() -> Any:
    """Require a bearer token for cron and a session for dashboard routes."""
    cfg = load_config()
    if request.path in {"/healthz", "/login", "/favicon.ico"} or request.path.startswith("/static/"):
        return None
    if request.path == "/internal/run-checks":
        token = request.headers.get("Authorization", "")
        expected = f"Bearer {cfg.run_trigger_token}" if cfg.run_trigger_token else ""
        if not expected or not hmac.compare_digest(token, expected):
            abort(401)
        if request.method != "POST":
            abort(405)
        return None
    if not cfg.dashboard_user or not cfg.dashboard_password or not cfg.flask_secret_key:
        abort(503, description="Dashboard login is not configured")
    if not session.get("viper_authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication_required", "login_url": url_for("login")}), 401
        return redirect(url_for("login", next=request.full_path.rstrip("?")))
    return None


def _safe_next_path(value: str | None) -> str:
    """Accept only same-site relative destinations after login."""
    if not value:
        return url_for("dashboard")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return url_for("dashboard")
    return value


def _csrf_token() -> str:
    """Get or create a session-bound CSRF token."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _latest_by_site(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce history to the newest scheduled run per region and site."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item.get("scheduled_time", ""), item.get("checked_at", "")),
                      reverse=True):
        key = (row.get("region", ""), row.get("site_key", ""))
        if key not in latest:
            latest[key] = row
    return sorted(latest.values(), key=lambda row: (row.get("region", ""), row.get("site_key", "")))


def _configured_sites(cfg, store: SupabaseStore | None = None) -> list[Site]:
    """Load and validate the site's CSV configuration."""
    if cfg.csv_type == "sheets":
        loader = GoogleSheetLoader()
    elif cfg.csv_type in {"s3", "gcs", "object_storage"}:
        loader = ObjectStorageLoader()
    else:
        loader = LocalLoader()
    try:
        content = loader.load(cfg.site_csv_path)
    except Exception:
        LOGGER.exception("could not load configured site CSV")
        sites, errors = [], ["could not load configured site source"]
    else:
        sites, errors = parse_sites(content, cfg.region_hosts)
    for error in errors:
        LOGGER.warning("live status CSV row skipped: %s", error)
    if store is not None:
        try:
            sites, managed_errors = merge_site_configs(sites, store.site_configs(), cfg.region_hosts)
            for error in managed_errors:
                LOGGER.warning("dashboard site configuration skipped: %s", error)
        except SupabaseAPIError:
            raise
    return sites


def _live_sites(cfg, store: SupabaseStore | None = None) -> list[dict[str, Any]]:
    """Read configured sites and fetch their current status API snapshots."""
    sites = _configured_sites(cfg, store)

    client = StatusClient(cfg.request_timeout, retries=1)
    def fetch(site):
        url = f"https://{cfg.region_hosts[site.region]}/api/{site.site_key}/catalog/status"
        try:
            history = client.fetch(cfg.region_hosts[site.region], site.site_key)
            latest = max(history, key=lambda entry: _timestamp_value(entry.get("timeStamp")), default=None)
            raw_status = str((latest or {}).get("status", "")).upper()
            code = (latest or {}).get("code")
            if raw_status in {"INDEXING", "PROCESSING", "IN_PROGRESS", "ACCEPTED", "QUEUED"}:
                state = "indexing"
            elif raw_status == "REJECTED":
                state = "rejected"
            elif raw_status in {"INDEXED", "SUCCESS", "SUCCEEDED"} and code in (None, 200):
                state = "success"
            elif raw_status in {"FAILED", "ERROR"} or (code is not None and code != 200 and raw_status):
                state = "failure"
            else:
                state = "no_status"
            return {
                "site_key": site.site_key, "region": site.region, "version": site.version, "app": site.app,
                "state": state, "api_status": (latest or {}).get("status", ""),
                "code": code, "timestamp_ms": (latest or {}).get("timeStamp"),
                "status_url": url, "error": "",
                "detail": "API returned an empty history" if not history else ("Latest entry has no status field" if not raw_status else ""),
            }
        except Exception as exc:
            return {"site_key": site.site_key, "region": site.region, "version": site.version, "app": site.app,
                    "state": "no_status", "api_status": "", "code": None, "timestamp_ms": None,
                    "status_url": url, "error": str(exc), "detail": "Could not fetch the status API"}

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, min(cfg.max_workers, 10))) as pool:
        futures = [pool.submit(fetch, site) for site in sites]
        for future in as_completed(futures):
            rows.append(future.result())
    return sorted(rows, key=lambda row: (row["region"], row["site_key"]))


def _timestamp_value(value: Any) -> float:
    """Convert the feed API millisecond timestamp to a sortable number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


@app.get("/healthz")
def healthz():
    """Return a lightweight health response for Render and uptime monitors."""
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login():
    """Show a dedicated sign-in page and establish an authenticated session."""
    cfg = load_config()
    if not cfg.dashboard_user or not cfg.dashboard_password or not cfg.flask_secret_key:
        abort(503, description="Set DASHBOARD_USER, DASHBOARD_PASSWORD, and FLASK_SECRET_KEY")
    destination = _safe_next_path(request.args.get("next") or request.form.get("next"))
    if session.get("viper_authenticated"):
        return redirect(destination)
    error = ""
    if request.method == "POST":
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted, expected):
            abort(400, description="The login form expired. Please try again.")
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (hmac.compare_digest(username, cfg.dashboard_user)
                and hmac.compare_digest(password, cfg.dashboard_password)):
            session.clear()
            session.permanent = True
            session["viper_authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(destination)
        error = "That username and password did not match."
    return render_template("login.html", csrf_token=_csrf_token(), next_path=destination, error=error)


@app.post("/logout")
def logout():
    """End the dashboard session after a CSRF-protected form submission."""
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(submitted, expected):
        abort(400, description="The logout form expired. Please try again.")
    session.clear()
    return redirect(url_for("login"))


@app.get("/favicon.ico")
def favicon():
    """Avoid logging a missing optional browser favicon as an error."""
    return "", 204


@app.errorhandler(SupabaseAPIError)
def handle_supabase_error(error: SupabaseAPIError):
    """Show a safe, actionable data-connection error instead of a raw 500."""
    LOGGER.error("Supabase request failed with HTTP %s", error.status_code)
    if request.path.startswith("/api/"):
        return jsonify({"error": "supabase_unavailable", "message": error.user_message}), 503
    return render_template("error.html", message=error.user_message, status_code=error.status_code,
                           csrf_token=_csrf_token()), 503


@app.errorhandler(404)
def handle_not_found(error):
    """Render the shared not-found page."""
    return render_template("error.html", message="We couldn't find that page.", status_code=404,
                           csrf_token=session.get("csrf_token", "")), 404


@app.errorhandler(503)
def handle_unavailable(error):
    """Render setup guidance for missing dashboard configuration."""
    message = getattr(error, "description", "The service is temporarily unavailable.")
    return render_template("error.html", message=message, status_code=503,
                           csrf_token=session.get("csrf_token", "")), 503


@app.post("/internal/run-checks")
def trigger_checks():
    """Run one checker cycle when called by the external scheduler."""
    if not _run_lock.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 409
    try:
        summary = run_checks(load_config())
        if summary["counts"].get("delivery_errors", 0):
            return jsonify({"status": "delivery_error", **summary}), 502
        return jsonify({"status": "completed", **summary})
    except Exception as exc:
        LOGGER.exception("scheduled checker run failed")
        return jsonify({"status": "error", "message": str(exc)}), 500
    finally:
        _run_lock.release()


@app.get("/")
def dashboard():
    """Render current site status table from Supabase."""
    cfg = load_config()
    store = _store()
    recorded = {(row.get("region", ""), row.get("site_key", "")): row
                for row in _latest_by_site(store.latest_runs())}
    rows = []
    for site in _configured_sites(cfg, store):
        key = (site.region, site.site_key)
        row = dict(recorded.get(key, {}))
        row.update({"site_key": site.site_key, "region": site.region, "version": site.version,
                    "app": site.app, "cron": site.cron, "cron_description": describe_cron(site.cron)})
        row.setdefault("state", "no_data")
        row.setdefault("scheduled_time", "")
        row.setdefault("checked_at", "")
        row.setdefault("latest_entry", None)
        row.setdefault("alert_sent", False)
        row.setdefault("status_url", f"https://{cfg.region_hosts[site.region]}/api/{site.site_key}/catalog/status")
        rows.append(row)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("state", "unknown")] = counts.get(row.get("state", "unknown"), 0) + 1
    return render_template("index.html", rows=rows, counts=counts,
                           csrf_token=_csrf_token(), region_options=sorted(cfg.region_hosts.items()),
                           last_checked=max((r.get("checked_at", "") for r in rows), default=""))


@app.post("/sites")
def add_site():
    """Validate and add a site configuration from the dashboard form."""
    submitted_csrf = request.form.get("csrf_token", "")
    expected_csrf = session.get("csrf_token", "")
    if not expected_csrf or not hmac.compare_digest(submitted_csrf, expected_csrf):
        abort(400, description="The form expired. Refresh the dashboard and try again.")

    fields = ["site_key", "region", "cron", "max_duration_minutes", "version", "app"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerow({field: request.form.get(field, "").strip() for field in fields})
    cfg = load_config()
    sites, errors = parse_sites(output.getvalue(), cfg.region_hosts)
    if errors or not sites:
        flash("Could not add site: " + "; ".join(errors or ["enter valid site details"]), "error")
        return redirect(url_for("dashboard"))

    site = sites[0]
    store = _store()
    if any(item.region == site.region and item.site_key == site.site_key
           for item in _configured_sites(cfg, store)):
        flash(f"{site.site_key} is already configured for region {site.region}.", "error")
        return redirect(url_for("dashboard"))
    try:
        store.upsert_site_config({
            "site_key": site.site_key, "region": site.region, "cron": site.cron,
            "max_duration_minutes": site.max_duration_minutes,
            "version": site.version, "app": site.app, "is_enabled": True,
        })
    except SupabaseAPIError as exc:
        if exc.status_code == 409:
            flash(f"{site.site_key} is already configured for region {site.region}.", "error")
        else:
            flash(f"Could not save site configuration: {exc.user_message}", "error")
        return redirect(url_for("dashboard"))
    flash(f"Added {site.site_key} ({site.region}). It will be checked on the next scheduled run.", "success")
    return redirect(url_for("dashboard"))


@app.post("/sites/<region>/<site_key>/delete")
def delete_site(region: str, site_key: str):
    """Stop monitoring a site while preserving its run history."""
    submitted_csrf = request.form.get("csrf_token", "")
    expected_csrf = session.get("csrf_token", "")
    if not expected_csrf or not hmac.compare_digest(submitted_csrf, expected_csrf):
        abort(400, description="The form expired. Refresh the dashboard and try again.")

    cfg = load_config()
    store = _store()
    site = next((item for item in _configured_sites(cfg, store)
                 if item.region == region and item.site_key == site_key), None)
    if site is None:
        flash(f"{site_key} is not currently being monitored in {region}.", "error")
        return redirect(url_for("dashboard"))
    try:
        store.upsert_site_config({
            "site_key": site.site_key, "region": site.region, "cron": site.cron,
            "max_duration_minutes": site.max_duration_minutes,
            "version": site.version, "app": site.app, "is_enabled": False,
        })
    except SupabaseAPIError as exc:
        flash(f"Could not remove site: {exc.user_message}", "error")
        return redirect(url_for("dashboard"))
    flash(f"Removed {site_key} ({region}) from monitoring. Existing run history was kept.", "success")
    return redirect(url_for("dashboard"))


@app.get("/live-status")
def live_status():
    """Fetch and display the latest status API entry for every CSV site."""
    rows = _live_sites(load_config(), _store())
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return render_template("live_status.html", rows=rows, counts=counts,
                           refreshed_at=datetime.now(timezone.utc).isoformat(),
                           csrf_token=_csrf_token())


@app.get("/site/<site_key>")
def site_detail(site_key: str):
    """Render recent runs, including a helpful page before the first check."""
    region = request.args.get("region", "")
    cfg = load_config()
    store = _store()
    history = store.site_history(site_key, region)
    configured_site = next((site for site in _configured_sites(cfg, store)
                            if site.site_key == site_key and (not region or site.region == region)), None)
    if not history:
        if not configured_site:
            abort(404)
        status_url = f"https://{cfg.region_hosts[configured_site.region]}/api/{site_key}/catalog/status"
        history = [{"site_key": site_key, "region": configured_site.region,
                    "version": configured_site.version, "app": configured_site.app,
                    "cron": configured_site.cron, "cron_description": describe_cron(configured_site.cron),
                    "state": "no_data", "scheduled_time": "", "checked_at": "",
                    "latest_entry": None, "alert_sent": False, "status_url": status_url}]
    elif configured_site:
        for row in history:
            row["cron"] = configured_site.cron
            row["cron_description"] = describe_cron(configured_site.cron)
    return render_template("site.html", site_key=site_key, region=region or history[0].get("region"),
                           rows=history, csrf_token=_csrf_token())


@app.get("/api/sites")
def sites_api():
    """Return latest status record per site as JSON."""
    return jsonify(_latest_by_site(_store().latest_runs()))


@app.get("/api/sites/<site_key>")
def site_api(site_key: str):
    """Return a site's recent run history as JSON."""
    return jsonify(_store().site_history(site_key, request.args.get("region", "")))
