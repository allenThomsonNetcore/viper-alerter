"""Environment and YAML configuration for the checker."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    """Runtime configuration loaded from environment and optional YAML."""
    csv_source: str = "sites.example.csv"
    csv_type: str = "local"
    region_hosts: dict[str, str] = field(default_factory=lambda: {"apac": "feed-apac.unbxd.io"})
    region_webhooks: dict[str, str] = field(default_factory=dict)
    buffer_minutes: int = 15
    max_workers: int = 10
    request_timeout: float = 15
    timestamp_semantics: str = "finish"
    dry_run: bool = False
    reminder_hours: float | None = None
    state_backend: str = "file"
    state_file: str = "/tmp/indexing-alert-state.json"
    configmap_name: str = "indexing-alert-state"
    namespace: str = "default"
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_recipients: list[str] = field(default_factory=list)
    yaml_path: str = ""


def load_config() -> Config:
    """Load supported settings from environment and optional YAML file."""
    cfg = Config()
    yaml_path = os.getenv("CONFIG_YAML", "")
    if yaml_path:
        data: dict[str, Any] = yaml.safe_load(Path(yaml_path).read_text()) or {}
        cfg.region_hosts.update(data.get("region_hosts", {}))
        cfg.region_webhooks.update(data.get("region_webhooks", {}))
    cfg.csv_source = os.getenv("CSV_SOURCE", cfg.csv_source)
    cfg.csv_type = os.getenv("CSV_SOURCE_TYPE", "local")
    cfg.buffer_minutes = int(os.getenv("BUFFER_MINUTES", cfg.buffer_minutes))
    cfg.max_workers = int(os.getenv("MAX_WORKERS", cfg.max_workers))
    cfg.request_timeout = float(os.getenv("REQUEST_TIMEOUT_SECONDS", cfg.request_timeout))
    cfg.timestamp_semantics = os.getenv("TIMESTAMP_SEMANTICS", cfg.timestamp_semantics).lower()
    if cfg.timestamp_semantics not in {"start", "finish"}:
        raise ValueError("TIMESTAMP_SEMANTICS must be 'start' or 'finish'")
    cfg.dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    cfg.reminder_hours = float(os.environ["REMINDER_HOURS"]) if os.getenv("REMINDER_HOURS") else None
    cfg.state_backend = os.getenv("STATE_BACKEND", "kubernetes").lower()
    cfg.state_file = os.getenv("STATE_FILE", cfg.state_file)
    cfg.configmap_name = os.getenv("STATE_CONFIGMAP", cfg.configmap_name)
    cfg.namespace = os.getenv("POD_NAMESPACE", os.getenv("NAMESPACE", cfg.namespace))
    cfg.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    cfg.smtp_host = os.getenv("SMTP_HOST", "")
    cfg.smtp_port = int(os.getenv("SMTP_PORT", "587"))
    cfg.smtp_user = os.getenv("SMTP_USER", "")
    cfg.smtp_password = os.getenv("SMTP_PASSWORD", "")
    cfg.email_from = os.getenv("EMAIL_FROM", "")
    cfg.email_recipients = [x.strip() for x in os.getenv("EMAIL_RECIPIENTS", "").split(",") if x.strip()]
    return cfg
