"""EmailJS provider behavior tests."""
from checker.config import Config
from checker.emailjs_notifier import EMAILJS_SEND_URL, EmailJSNotifier


def test_emailjs_sends_template_payload(monkeypatch):
    """Provider receives configured template fields over HTTPS."""
    captured = {}

    class Response:
        ok = True
        def raise_for_status(self):
            return None

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("checker.emailjs_notifier.requests.post", post)
    cfg = Config(emailjs_service_id="service", emailjs_template_id="template",
                 emailjs_public_key="public", emailjs_private_key="private",
                 emailjs_to_email="alerts@example.com",
                 emailjs_cc_emails=["ops@example.com", "owner@example.com"],
                 emailjs_bcc_emails=["audit@example.com"])
    EmailJSNotifier(cfg).send("Subject", "Body", {"state": "failed"})
    assert captured["url"] == EMAILJS_SEND_URL
    payload = captured["json"]
    assert payload["user_id"] == "public"
    assert payload["accessToken"] == "private"
    assert payload["template_params"] == {
        "to_email": "alerts@example.com", "cc_emails": "ops@example.com, owner@example.com",
        "bcc_emails": "audit@example.com", "subject": "Subject", "message": "Body", "state": "failed"}


def test_emailjs_requires_configuration():
    """Missing provider credentials fail clearly rather than dropping alerts."""
    try:
        EmailJSNotifier(Config()).send("Subject", "Body")
    except RuntimeError as exc:
        assert "EMAILJS_SERVICE_ID" in str(exc)
    else:
        raise AssertionError("Expected missing EmailJS settings to fail")


def test_emailjs_rejection_includes_provider_detail(monkeypatch):
    """A provider 403 includes its safe response text for alert diagnostics."""
    class Response:
        ok = False
        status_code = 403
        reason = "Forbidden"
        text = "The public key is invalid"

    monkeypatch.setattr("checker.emailjs_notifier.requests.post", lambda *args, **kwargs: Response())
    cfg = Config(emailjs_service_id="service", emailjs_template_id="template",
                 emailjs_public_key="public", emailjs_to_email="alerts@example.com")
    try:
        EmailJSNotifier(cfg).send("Subject", "Body")
    except RuntimeError as exc:
        assert "HTTP 403" in str(exc)
        assert "public key is invalid" in str(exc)
    else:
        raise AssertionError("Expected EmailJS rejection to be reported")
