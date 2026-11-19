"""Plain Slack and SMTP notification delivery."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import requests

from checker.config import Config


class Notifier:
    """Send alerts using configured Slack and/or email destinations."""
    def __init__(self, config: Config) -> None:
        """Capture notifier configuration."""
        self.config = config

    def send(self, subject: str, body: str, region: str = "") -> None:
        """Send a plain-text notification; fail if no channel is configured."""
        sent = False
        hook = self.config.region_webhooks.get(region, self.config.slack_webhook_url)
        if hook:
            response = requests.post(hook, json={"text": f"{subject}\n\n{body}"}, timeout=15)
            response.raise_for_status()
            sent = True
        if self.config.smtp_host and self.config.email_recipients:
            msg = EmailMessage()
            msg["Subject"], msg["From"] = subject, self.config.email_from
            msg["To"] = ", ".join(self.config.email_recipients)
            msg.set_content(body)
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=15) as server:
                server.starttls()
                if self.config.smtp_user: server.login(self.config.smtp_user, self.config.smtp_password)
                server.send_message(msg)
            sent = True
        if not sent:
            raise RuntimeError("No Slack or email notifier configured")


def alert_text(site: Any, result: dict[str, Any], host: str) -> tuple[str, str]:
    """Format an indexing alert subject and plain-text body."""
    state = result["state"]
    scheduled = result["scheduled_time"].astimezone().astimezone(__import__("datetime").timezone.utc).isoformat()
    entry = result.get("latest_entry") or {}
    suggestions = {"missing": "Check the feed pull pipeline; the status endpoint cannot show the root cause.",
                   "stuck": "Check the indexing job and its worker logs.", "failed": "Inspect the failure message and indexing logs.",
                   "rejected": "Check feed validation and rejection details.", "unknown": "Review the raw status and code with the platform team."}
    subject = f"Indexing alert: {state} - {site.site_key} [{site.region}]"
    body = (f"Region: {site.region}\nSite: {site.site_key}\nScheduled time (UTC): {scheduled}\nState: {state}\n"
            f"How long overdue: {result['overdue_minutes']} minutes\nLatest history entry: status={entry.get('status', 'none')}, "
            f"code={entry.get('code', 'none')}, date={entry.get('date', 'none')}, message={entry.get('message', 'none')}\n"
            f"Status URL: https://{host}/api/{site.site_key}/catalog/status\nSuggested next check: {suggestions.get(state, 'Review the run status.')}")
    return subject, body
