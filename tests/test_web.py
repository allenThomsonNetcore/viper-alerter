"""Web endpoint access-control and rendering tests."""
from checker.config import Config


def test_health_endpoint_is_public(monkeypatch):
    """Health pings do not require dashboard credentials."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config())
    response = web.app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_dashboard_requires_basic_auth(monkeypatch):
    """Dashboard requests require configured credentials."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret"))
    response = web.app.test_client().get("/")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


def test_run_endpoint_rejects_bad_token(monkeypatch):
    """The external scheduler endpoint rejects an invalid bearer token."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config(run_trigger_token="expected"))
    response = web.app.test_client().post("/internal/run-checks", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
