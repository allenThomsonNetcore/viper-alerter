"""Cron entry point for catalog indexing alerts."""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from checker.config import load_config
from checker.csv_loader import LocalLoader, ObjectStorageLoader, GoogleSheetLoader, parse_sites
from checker.evaluator import evaluate, schedule_times
from checker.notifiers import Notifier, alert_text
from checker.state_store import ConfigMapStateStore, FileStateStore, prune
from checker.status_client import StatusClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("checker")


def emit(event: dict[str, Any]) -> None:
    """Write one structured JSON log record to stdout."""
    print(json.dumps(event, default=str), flush=True)


def run() -> None:
    """Run CSV validation, concurrent checks, notifications and summary."""
    cfg = load_config()
    loader = GoogleSheetLoader() if cfg.csv_type == "sheets" else ObjectStorageLoader() if cfg.csv_type in {"s3", "gcs", "object_storage"} else LocalLoader()
    sites, errors = parse_sites(loader.load(cfg.csv_source), cfg.region_hosts)
    store = ConfigMapStateStore(cfg.configmap_name, cfg.namespace) if cfg.state_backend == "kubernetes" else FileStateStore(cfg.state_file)
    records = prune(store.load(), datetime.now(timezone.utc))
    notifier, client = Notifier(cfg), StatusClient(cfg.request_timeout)
    if errors:
        message = "Invalid CSV configuration rows:\n" + "\n".join(errors)
        if not cfg.dry_run: notifier.send("Indexing alert: config_error", message)
        emit({"event": "config_error", "errors": errors})
    counts = {"ok": 0, "pending": 0, "alerted": 0, "errors": len(errors)}
    now = datetime.now(timezone.utc)
    def fetch(site):
        return site, client.fetch(cfg.region_hosts[site.region], site.site_key)
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = {pool.submit(fetch, site): site for site in sites}
        for future in as_completed(futures):
            site = futures[future]
            try:
                _, history = future.result()
            except Exception as exc:
                counts["errors"] += 1
                key = json.dumps([site.region, site.site_key, "checker_error"], separators=(",", ":"))
                if key not in records:
                    body = f"Status check failed for {site.site_key} [{site.region}] after retries: {exc}"
                    if not cfg.dry_run:
                        notifier.send(f"Indexing alert: checker_error [{site.region}]", body, site.region)
                        records[key] = {"scheduled_time": now.isoformat(), "alert_time": now.isoformat(), "state": "checker_error"}
                emit({"site_key": site.site_key, "region": site.region, "state": "checker_error"})
                continue
            previous, latest, following = schedule_times(site, now)
            for scheduled, boundary in ((previous, latest), (latest, following)):
                result = evaluate(site, history, now, scheduled, boundary, cfg.buffer_minutes, cfg.timestamp_semantics)
                state = result["state"]
                emit({"site_key": site.site_key, "region": site.region, "scheduled_time": scheduled.isoformat(), "state": state})
                counts[state] = counts.get(state, 0) + 1
                run_prefix = json.dumps([site.region, site.site_key, scheduled.isoformat()], separators=(",", ":"))
                if state == "ok":
                    for key, record in list(records.items()):
                        if key.startswith(run_prefix) and record.get("state") not in {"ok", "recovered"}:
                            recovered_key = f"{run_prefix},\"recovered\"]"
                            if recovered_key not in records and not cfg.dry_run:
                                notifier.send(f"Indexing alert: recovered - {site.site_key} [{site.region}]", f"Run scheduled {scheduled.isoformat()} UTC recovered.", site.region)
                                records[recovered_key] = {"scheduled_time": scheduled.isoformat(), "alert_time": now.isoformat(), "state": "recovered"}
                    continue
                if state in {"pending", "ok"}: continue
                key = f"{run_prefix[:-1]},\"{state}\"]"
                if key in records: continue
                subject, body = alert_text(site, result, cfg.region_hosts[site.region])
                if cfg.dry_run:
                    emit({"event": "dry_run_alert", "subject": subject, "body": body})
                else:
                    notifier.send(subject, body, site.region)
                    records[key] = {"scheduled_time": scheduled.isoformat(), "alert_time": now.isoformat(), "state": state}
                counts["alerted"] += 1
    if not cfg.dry_run: store.save(prune(records, now))
    emit({"event": "summary", "counts": counts})


if __name__ == "__main__":
    try: run()
    except Exception as exc:
        emit({"event": "crash", "error": str(exc)})
        raise
