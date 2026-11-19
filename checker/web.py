"""Flask dashboard and protected cron trigger for the indexing checker."""
from __future__ import annotations

import hmac
import logging
import secrets
from datetime import timedelta
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from checker.config import load_config
from checker.service import run_checks
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
    """Reduce recent run records to the newest record per region and site."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("region", ""), row.get("site_key", ""))
        if key not in latest:
            latest[key] = row
    return sorted(latest.values(), key=lambda row: (row.get("region", ""), row.get("site_key", "")))


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
    rows = _latest_by_site(_store().latest_runs())
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("state", "unknown")] = counts.get(row.get("state", "unknown"), 0) + 1
    return render_template("index.html", rows=rows, counts=counts,
                           csrf_token=_csrf_token(), last_checked=max((r.get("checked_at", "") for r in rows), default=""))


@app.get("/site/<site_key>")
def site_detail(site_key: str):
    """Render recent scheduled runs for a selected site."""
    region = request.args.get("region", "")
    history = _store().site_history(site_key, region)
    if not history:
        abort(404)
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
