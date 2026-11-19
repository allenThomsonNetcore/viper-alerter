"""EmailJS notification adapter using its HTTPS email API."""
from __future__ import annotations

from typing import Any

import requests

from checker.config import Config

EMAILJS_SEND_URL = "https://api.emailjs.com/api/v1.0/email/send"


class EmailJSNotifier:
    """Send plain text alert templates through EmailJS."""

    def __init__(self, config: Config) -> None:
        """Capture EmailJS identifiers and credentials."""
        self.config = config

    def send(self, subject: str, body: str, details: dict[str, Any] | None = None) -> None:
        """Send one EmailJS template message and raise on provider errors."""
        cfg = self.config
        missing = [name for name, value in (
            ("EMAILJS_SERVICE_ID", cfg.emailjs_service_id),
            ("EMAILJS_TEMPLATE_ID", cfg.emailjs_template_id),
            ("EMAILJS_PUBLIC_KEY", cfg.emailjs_public_key),
            ("EMAILJS_TO_EMAIL", cfg.emailjs_to_email),
        ) if not value]
        if missing:
            raise RuntimeError(f"EmailJS configuration missing: {', '.join(missing)}")
        params = {
            "to_email": cfg.emailjs_to_email,
            "cc_emails": ", ".join(cfg.emailjs_cc_emails),
            "bcc_emails": ", ".join(cfg.emailjs_bcc_emails),
            "subject": subject,
            "message": body,
        }
        if details:
            params.update({k: str(v) if v is not None else "" for k, v in details.items()})
        payload: dict[str, Any] = {
            "service_id": cfg.emailjs_service_id,
            "template_id": cfg.emailjs_template_id,
            "user_id": cfg.emailjs_public_key,
            "template_params": params,
        }
        if cfg.emailjs_private_key:
            payload["accessToken"] = cfg.emailjs_private_key
        response = requests.post(EMAILJS_SEND_URL, json=payload, timeout=20)
        if not response.ok:
            # EmailJS returns a short explanation in the response body (for
            # example, an invalid public key or blocked service). Preserve it
            # in the run record so a 403 is diagnosable from the dashboard.
            detail = " ".join(response.text.split())[:500] or response.reason
            raise RuntimeError(f"EmailJS rejected the request (HTTP {response.status_code}): {detail}")
