"""Web endpoint access-control and rendering tests."""
from checker.config import Config
from checker.csv_loader import Site


def test_health_endpoint_is_public(monkeypatch):
    """Health pings do not require dashboard credentials."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config())
    response = web.app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_dashboard_redirects_to_dedicated_login(monkeypatch):
    """Unauthenticated dashboard requests go to the dedicated login page."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret", flask_secret_key="test-key"))
    web.app.secret_key = "test-key"
    response = web.app.test_client().get("/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_login_creates_dashboard_session(monkeypatch):
    """Valid dashboard credentials establish a session without HTTP Basic Auth."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret", flask_secret_key="test-key"))
    web.app.secret_key = "test-key"
    client = web.app.test_client()
    client.get("/login")
    with client.session_transaction() as state:
        csrf_token = state["csrf_token"]
    response = client.post("/login", data={"username": "operator", "password": "secret", "csrf_token": csrf_token, "next": "/"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_dashboard_renders_mobile_table(monkeypatch):
    """Authenticated dashboard returns the responsive site table."""
    from checker import web

    class Store:
        def latest_runs(self):
            return [{"region": "sg", "site_key": "test-site", "version": "v2", "app": "shopify",
                     "state": "ok", "scheduled_time": "2026-11-19T09:00:00+00:00",
                     "checked_at": "2026-11-19T09:30:00+00:00", "latest_entry": {"status": "INDEXED", "code": 200},
                     "alert_sent": False, "status_url": "https://feed-apac.unbxd.io/api/test-site/catalog/status"}]

    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret", flask_secret_key="test-key"))
    monkeypatch.setattr(web, "_store", Store)
    monkeypatch.setattr(web, "_configured_sites", lambda cfg, store=None: [Site("test-site", "sg", "0 */3 * * *", 120, "v2", "shopify")])
    web.app.secret_key = "test-key"
    client = web.app.test_client()
    with client.session_transaction() as state:
        state["viper_authenticated"] = True
        state["csrf_token"] = "csrf"
    response = client.get("/")
    assert response.status_code == 200
    assert b"test-site" in response.data
    assert b"Version / App" in response.data
    assert b"Every 3 hours at minute 00 UTC" in response.data
    assert b"viper.css" in response.data


def test_live_status_page_shows_localized_feed_timestamp_and_filter(monkeypatch):
    """Live page renders API status, status selector and open endpoint."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret", flask_secret_key="test-key"))
    monkeypatch.setattr(web, "_live_sites", lambda cfg: [{
        "site_key": "test-site", "region": "us", "version": "v2", "app": "shopify",
        "state": "indexing", "api_status": "INDEXING", "code": 200,
        "timestamp_ms": 1795110000000, "status_url": "https://feed.unbxd.io/api/test-site/catalog/status", "error": "",
    }])
    web.app.secret_key = "test-key"
    client = web.app.test_client()
    with client.session_transaction() as state:
        state["viper_authenticated"] = True
        state["csrf_token"] = "csrf"
    response = client.get("/live-status")
    assert response.status_code == 200
    assert b"Current feed status" in response.data
    assert b"INDEXING" in response.data
    assert b"value=\"rejected\"" in response.data
    assert b"https://feed.unbxd.io/api/test-site/catalog/status" in response.data


def test_site_history_page_handles_site_before_first_check(monkeypatch):
    """A configured site with no stored runs gets an informative page, not 404."""
    from checker import web

    class Store:
        def site_history(self, site_key, region=""):
            return []

    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret", flask_secret_key="test-key"))
    monkeypatch.setattr(web, "_store", Store)
    monkeypatch.setattr(web, "_configured_sites", lambda cfg, store=None: [Site("test-site", "us", "0 */3 * * *", 120, "v2", "shopify")])
    web.app.secret_key = "test-key"
    client = web.app.test_client()
    with client.session_transaction() as state:
        state["viper_authenticated"] = True
        state["csrf_token"] = "csrf"
    response = client.get("/site/test-site?region=us")
    assert response.status_code == 200
    assert b"Not checked yet" in response.data
    assert b"Every 3 hours at minute 00 UTC" in response.data
    assert b"https://feed.unbxd.io/api/test-site/catalog/status" in response.data


def test_dashboard_check_now_runs_checker(monkeypatch):
    """A signed-in Check now click runs one checker cycle and returns to the overview."""
    from checker import web

    calls = []
    monkeypatch.setattr(web, "load_config", lambda: Config(dashboard_user="operator", dashboard_password="secret", flask_secret_key="test-key"))
    monkeypatch.setattr(web, "run_checks", lambda cfg: calls.append(cfg) or {"counts": {}})
    monkeypatch.setattr(web, "_store", lambda: type("Store", (), {"latest_runs": lambda self: []})())
    monkeypatch.setattr(web, "_configured_sites", lambda cfg, store=None: [])
    web.app.secret_key = "test-key"
    client = web.app.test_client()
    with client.session_transaction() as state:
        state["viper_authenticated"] = True
        state["csrf_token"] = "csrf"
    response = client.post("/run-checks", data={"csrf_token": "csrf"}, follow_redirects=True)
    assert response.status_code == 200
    assert len(calls) == 1
    assert b"Check finished" in response.data
    assert b"Check now" in response.data


def test_run_endpoint_rejects_bad_token(monkeypatch):
    """The external scheduler endpoint rejects an invalid bearer token."""
    from checker import web
    monkeypatch.setattr(web, "load_config", lambda: Config(run_trigger_token="expected"))
    response = web.app.test_client().post("/internal/run-checks", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
