"""Flask dashboard and protected cron trigger for the indexing checker."""
from __future__ import annotations

import hmac
import logging
from threading import Lock
from typing import Any

from flask import Flask, abort, jsonify, render_template, request

from checker.config import load_config
from checker.service import run_checks
from checker.supabase_store import SupabaseStore

LOGGER = logging.getLogger("viper.web")
app = Flask(__name__, template_folder="templates")
_run_lock = Lock()


def _store() -> SupabaseStore:
    """Create a request-scoped Supabase REST client."""
    cfg = load_config()
    return SupabaseStore(cfg.supabase_url, cfg.supabase_service_role_key, cfg.request_timeout)


@app.before_request
def protect_routes() -> Any:
    """Require a shared secret for cron trigger and basic auth for dashboard."""
    cfg = load_config()
    if request.path == "/healthz":
        return None
    if request.path == "/internal/run-checks":
        token = request.headers.get("Authorization", "")
        expected = f"Bearer {cfg.run_trigger_token}" if cfg.run_trigger_token else ""
        if not expected or not hmac.compare_digest(token, expected):
            abort(401)
        if request.method != "POST":
            abort(405)
        return None
    if not cfg.dashboard_user or not cfg.dashboard_password:
        abort(503, description="Dashboard authentication is not configured")
    auth = request.authorization
    if (auth is None or not hmac.compare_digest(auth.username or "", cfg.dashboard_user)
            or not hmac.compare_digest(auth.password or "", cfg.dashboard_password)):
        return ("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="Viper status"'})
    return None


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
    return render_template("index.html", rows=rows)


@app.get("/site/<site_key>")
def site_detail(site_key: str):
    """Render recent scheduled runs for a selected site."""
    region = request.args.get("region", "")
    history = _store().site_history(site_key, region)
    if not history:
        abort(404)
    return render_template("site.html", site_key=site_key, region=region or history[0].get("region"), rows=history)


@app.get("/api/sites")
def sites_api():
    """Return latest status record per site as JSON."""
    return jsonify(_latest_by_site(_store().latest_runs()))


@app.get("/api/sites/<site_key>")
def site_api(site_key: str):
    """Return a site's recent run history as JSON."""
    return jsonify(_store().site_history(site_key, request.args.get("region", "")))
