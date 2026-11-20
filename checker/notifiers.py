"""Plain Slack and SMTP notification delivery."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from checker.config import Config

IST = ZoneInfo("Asia/Kolkata")


def expected_cause(state: str) -> str:
    """Return the operational expectation associated with a missing status."""
    if state == "missing":
        return "Likely feed pull pipeline failure (verify pipeline logs)"
    return "—"


def format_ist(value: datetime) -> str:
    """Format a timezone-aware datetime for alert emails in Indian time."""
    return value.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")


def format_epoch_ms_ist(value: Any) -> str:
    """Convert an API millisecond epoch timestamp to an IST email value."""
    try:
        return format_ist(datetime.fromtimestamp(float(value) / 1000, timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return "none"


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


class SlackWebhookNotifier:
    """Post alert messages to the Slack channel configured by an incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, subject: str, body: str, details: dict[str, Any] | None = None) -> None:
        try:
            response = requests.post(self.webhook_url, json={"text": f"*{subject}*\n{body}"}, timeout=15)
        except requests.RequestException as exc:
            # Avoid including the webhook URL, which contains its secret, in logs.
            raise RuntimeError(f"Slack webhook request failed ({type(exc).__name__})") from None
        if not response.ok:
            raise RuntimeError(f"Slack webhook rejected the message (HTTP {response.status_code})")


def alert_text(site: Any, result: dict[str, Any], host: str,
               checked_at: datetime | None = None) -> tuple[str, str]:
    """Format an indexing alert subject and plain-text body."""
    state = result["state"]
    scheduled = format_ist(result["scheduled_time"])
    entry = result.get("latest_entry") or {}
    entry_timestamp_text = format_epoch_ms_ist(entry.get("timeStamp"))
    checked_text = format_ist(checked_at) if checked_at else "not provided"
    expected = expected_cause(state)
    suggestions = {"missing": "Check the feed pull pipeline; the status endpoint cannot show the root cause.",
                   "stuck": "Check the indexing job and its worker logs.", "failed": "Inspect the failure message and indexing logs.",
                   "rejected": "Check feed validation and rejection details.", "unknown": "Review the raw status and code with the platform team."}
    subject = f"Indexing alert: {state} - {site.site_key} [{site.region}]"
    body = (f"Region: {site.region}\nSite: {site.site_key}\nVersion: {site.version}\nApp: {site.app}\n"
            f"Scheduled time (IST): {scheduled}\nChecked at (IST): {checked_text}\nState: {state}\n"
            f"Expected cause: {expected}\n"
            f"How long overdue: {result['overdue_minutes']} minutes\nLatest history entry: status={entry.get('status', 'none')}, "
            f"code={entry.get('code', 'none')}, API timestamp (IST): {entry_timestamp_text}, "
            f"message={entry.get('message', 'none')}\n"
            f"Status URL: https://{host}/api/{site.site_key}/catalog/status\nSuggested next check: {suggestions.get(state, 'Review the run status.')}")
    return subject, body
