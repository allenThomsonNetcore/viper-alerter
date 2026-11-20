"""Run scheduled status checks, alerts and Supabase persistence."""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any

from checker.config import Config
from checker.csv_loader import LocalLoader, ObjectStorageLoader, GoogleSheetLoader, merge_site_configs, parse_sites
from checker.emailjs_notifier import EmailJSNotifier
from checker.evaluator import evaluate, schedule_times
from checker.notifiers import (SlackWebhookNotifier, SlackWebAPINotifier, alert_text, expected_cause,
                               format_epoch_ms_ist, format_ist)
from checker.status_client import StatusClient
from checker.supabase_store import SupabaseStore

LOGGER = logging.getLogger("viper.service")
ALERT_STATES = {"failed", "rejected", "stuck", "missing", "unknown"}


def _digest(parts: list[str]) -> str:
    """Build a stable opaque key for one alert event."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _loader(cfg: Config):
    """Select a CSV source loader from configured source type."""
    if cfg.csv_type == "sheets":
        return GoogleSheetLoader()
    if cfg.csv_type in {"s3", "gcs", "object_storage"}:
        return ObjectStorageLoader()
    return LocalLoader()


class PartialDeliveryError(RuntimeError):
    """One or more alert channels failed after another channel may have sent."""

    def __init__(self, message: str, delivered: bool) -> None:
        self.delivered = delivered
        super().__init__(message)


def _notifiers(cfg: Config) -> dict[str, Any]:
    """Build the alert channels selected by ALERT_CHANNEL."""
    channels: dict[str, Any] = {}
    if cfg.alert_channel in {"email", "both"}:
        channels["emailjs"] = EmailJSNotifier(cfg)
    if cfg.alert_channel in {"slack", "both"}:
        if cfg.slack_bot_token and cfg.slack_recipient_ids:
            channels["slack"] = SlackWebAPINotifier(cfg.slack_bot_token, cfg.slack_recipient_ids)
        elif cfg.slack_webhook_url:
            channels["slack"] = SlackWebhookNotifier(cfg.slack_webhook_url)
        else:
            raise ValueError("Set SLACK_BOT_TOKEN and SLACK_RECIPIENT_IDS when ALERT_CHANNEL is slack or both")
    return channels


def _deliver(store: SupabaseStore, notifiers: dict[str, Any], *, key: str,
             record: dict[str, Any], subject: str, body: str,
             details: dict[str, Any] | None = None, dry_run: bool = False) -> bool:
    """Send once per channel and persist channel and aggregate delivery records."""
    if dry_run:
        LOGGER.info("DRY_RUN alert subject=%s body=%s", subject, body)
        return False
    if not notifiers:
        raise RuntimeError("No alert channels configured")
    aggregate_record = store.get_delivery(key)
    # Before per-channel keys were introduced, the aggregate key represented a
    # successful EmailJS delivery. Treat that legacy record as email-only so
    # newly enabled Slack can still receive the existing alert episode.
    legacy_email_delivery = bool(aggregate_record and ":" not in aggregate_record.get("kind", ""))
    previously_delivered = False
    delivered_now = False
    failures: list[str] = []
    for channel, notifier in notifiers.items():
        channel_key = _digest([key, "channel", channel])
        if store.get_delivery(channel_key):
            previously_delivered = True
            continue
        if legacy_email_delivery and channel == "emailjs":
            previously_delivered = True
            continue
        try:
            notifier.send(subject, body, details)
        except Exception as exc:
            failures.append(f"{channel}: {exc}")
            continue
        store.insert_delivery({**record, "dedupe_key": channel_key,
                               "kind": f"{record['kind']}:{channel}",
                               "subject": subject, "message": body,
                               "sent_at": datetime.now(timezone.utc).isoformat()})
        delivered_now = True
    if failures:
        raise PartialDeliveryError("; ".join(failures), previously_delivered or delivered_now)
    if not aggregate_record:
        store.insert_delivery({**record, "dedupe_key": key,
                               "kind": f"{record['kind']}:all",
                               "subject": subject, "message": body,
                               "sent_at": datetime.now(timezone.utc).isoformat()})
    return previously_delivered or delivered_now


def run_checks(cfg: Config, *, now: datetime | None = None) -> dict[str, Any]:
    """Fetch, evaluate and persist the previous and latest run per site."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not cfg.supabase_url or not cfg.supabase_service_role_key:
        raise ValueError("Supabase persistence is required (set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)")
    store = SupabaseStore(cfg.supabase_url, cfg.supabase_service_role_key, cfg.request_timeout)
    store.prune((checked_at - timedelta(days=7)).isoformat())
    notifiers = _notifiers(cfg)
    csv_path = cfg.site_csv_path
    try:
        content = _loader(cfg).load(csv_path)
    except Exception:
        LOGGER.exception("could not load configured site source")
        sites, config_errors = [], ["could not load configured site source"]
    else:
        sites, config_errors = parse_sites(content, cfg.region_hosts)
    sites, managed_errors = merge_site_configs(sites, store.site_configs(), cfg.region_hosts)
    config_errors.extend(managed_errors)
    counts: dict[str, Any] = {"sites": len(sites), "ok": 0, "pending": 0,
                              "alerted": 0, "checker_errors": 0,
                              "config_errors": len(config_errors), "delivery_errors": 0}

    for error in config_errors:
        LOGGER.error("config_error %s", error)
    if config_errors:
        fingerprint = _digest(sorted(config_errors))
        key = _digest(["config_error", fingerprint])
        _deliver(store, notifiers, key=key,
                 record={"kind": "config_error", "region": "all", "site_key": "", "scheduled_time": None},
                 subject="Indexing alert: config_error",
                 body="Invalid site CSV rows:\n" + "\n".join(config_errors), dry_run=cfg.dry_run)

    client = StatusClient(cfg.request_timeout)
    def fetch(site):
        return site, client.fetch(cfg.region_hosts[site.region], site.site_key)

    results: list[tuple[Any, list[dict[str, Any]] | Exception]] = []
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = {pool.submit(fetch, site): site for site in sites}
        for future in as_completed(futures):
            site = futures[future]
            try:
                _, history = future.result()
                results.append((site, history))
            except Exception as exc:
                results.append((site, exc))

    checker_errors: dict[str, list[tuple[Any, Exception]]] = {}
    for site, result_or_error in results:
        if isinstance(result_or_error, Exception):
            counts["checker_errors"] += 1
            checker_errors.setdefault(site.region, []).append((site, result_or_error))
            _, latest, _ = schedule_times(site, checked_at)
            store.upsert_run({
                "region": site.region, "site_key": site.site_key,
                "version": site.version, "app": site.app,
                "scheduled_time": latest.isoformat(), "checked_at": checked_at.isoformat(),
                "state": "checker_error", "status_url": f"https://{cfg.region_hosts[site.region]}/api/{site.site_key}/catalog/status",
                "latest_entry": None, "alert_sent": False, "alert_episode_key": None,
                "error_message": str(result_or_error),
            })
            LOGGER.error("site=%s region=%s state=checker_error error=%s", site.site_key, site.region, result_or_error)
            continue

        history = result_or_error
        previous, latest, following = schedule_times(site, checked_at)
        status_url = f"https://{cfg.region_hosts[site.region]}/api/{site.site_key}/catalog/status"
        for scheduled, boundary in ((previous, latest), (latest, following)):
            result = evaluate(site, history, checked_at, scheduled, boundary,
                              cfg.buffer_minutes, cfg.timestamp_semantics)
            state = result["state"]
            entry = result.get("latest_entry") or {}
            scheduled_iso = scheduled.isoformat()
            scheduled_ist = format_ist(scheduled)
            prior_run = store.get_run(site.region, site.site_key, scheduled_iso)
            prior_state = prior_run.get("state") if prior_run else None
            prior_episode = prior_run.get("alert_episode_key") if prior_run else None
            delivery_record = {
                "region": site.region, "site_key": site.site_key,
                "scheduled_time": scheduled_iso, "kind": "alert",
            }
            run_record = {
                "region": site.region, "site_key": site.site_key,
                "version": site.version, "app": site.app,
                "scheduled_time": scheduled_iso, "checked_at": checked_at.isoformat(),
                "state": state, "status_url": status_url,
                "latest_entry": entry if entry else None,
                "alert_sent": False, "alert_episode_key": None, "error_message": None,
            }
            sent = False
            if state in ALERT_STATES:
                if prior_state == state and prior_episode:
                    episode_key = prior_episode
                else:
                    episode_key = _digest([site.region, site.site_key, scheduled_iso,
                                           state, checked_at.isoformat()])
                run_record["alert_episode_key"] = episode_key
                subject, body = alert_text(site, result, cfg.region_hosts[site.region], checked_at)
                try:
                    api_timestamp_ist = format_epoch_ms_ist(entry["timeStamp"])
                except (KeyError, TypeError, ValueError, OverflowError, OSError):
                    api_timestamp_ist = "none"
                try:
                    sent = _deliver(store, notifiers, key=episode_key, record=delivery_record,
                                    subject=subject, body=body,
                                    details={"site_key": site.site_key, "region": site.region,
                                             "version": site.version, "app": site.app,
                                             "state": state, "scheduled_time": scheduled_ist,
                                             "checked_at": format_ist(checked_at),
                                             "api_timestamp": api_timestamp_ist,
                                             "expected_cause": expected_cause(state),
                                             "status_url": status_url}, dry_run=cfg.dry_run)
                except Exception as exc:
                    counts["delivery_errors"] += 1
                    sent = bool(getattr(exc, "delivered", False))
                    run_record["error_message"] = f"alert delivery failed: {exc}"
                    LOGGER.exception("alert delivery failed for %s [%s]", site.site_key, site.region)
                if sent:
                    counts["alerted"] += 1
                run_record["alert_sent"] = bool(sent or store.get_delivery(episode_key))
            elif state == "ok":
                # Keep the episode key until its recovery message is confirmed;
                # failed recovery delivery will then retry on the next poll.
                recovery_episode = prior_episode if prior_state in ALERT_STATES else None
                if prior_state == "ok":
                    recovery_episode = prior_episode
                if recovery_episode:
                    recovery_key = _digest([recovery_episode, "recovered"])
                    run_record["alert_episode_key"] = recovery_episode
                    try:
                        recovery_body = f"Run scheduled {scheduled_ist} recovered (IST).\nStatus URL: {status_url}"
                        recovery_sent = _deliver(store, notifiers, key=recovery_key,
                                        record={**delivery_record, "kind": "recovered"},
                                        subject=f"Indexing alert: recovered - {site.site_key} [{site.region}]",
                                        body=recovery_body,
                                        details={"site_key": site.site_key, "region": site.region,
                                                 "version": site.version, "app": site.app,
                                                 "state": "recovered", "scheduled_time": scheduled_ist,
                                                 "checked_at": format_ist(checked_at),
                                                 "status_url": status_url}, dry_run=cfg.dry_run)
                        if recovery_sent or store.get_delivery(recovery_key):
                            run_record["alert_episode_key"] = None
                    except Exception as exc:
                        counts["delivery_errors"] += 1
                        run_record["error_message"] = f"recovery delivery failed: {exc}"
                        LOGGER.exception("recovery delivery failed for %s [%s]", site.site_key, site.region)
            if state == "pending":
                counts["pending"] += 1
            elif state == "ok":
                counts["ok"] += 1
            store.upsert_run(run_record)
            LOGGER.info("site=%s region=%s scheduled_time=%s state=%s alert_sent=%s",
                        site.site_key, site.region, scheduled.isoformat(), state, run_record["alert_sent"])

    # One checker_error notification per region and half-hour check bucket.
    bucket = checked_at.replace(minute=(checked_at.minute // 30) * 30, second=0, microsecond=0).isoformat()
    for region, errors in checker_errors.items():
        key = _digest([region, "checker_error", bucket])
        body = "Status API checks failed after retries; these are checker errors, not indexing failures:\n"
        body += "\n".join(f"{site.site_key}: {error}" for site, error in errors)
        try:
            sent = _deliver(store, notifiers, key=key,
                            record={"kind": "checker_error", "region": region, "site_key": "", "scheduled_time": bucket},
                            subject=f"Indexing alert: checker_error [{region}]", body=body,
                            details={"region": region, "state": "checker_error", "scheduled_time": bucket})
            if sent:
                counts["alerted"] += 1
                for site, error in errors:
                    _, latest, _ = schedule_times(site, checked_at)
                    store.upsert_run({
                        "region": site.region, "site_key": site.site_key,
                        "version": site.version, "app": site.app,
                        "scheduled_time": latest.isoformat(), "checked_at": checked_at.isoformat(),
                        "state": "checker_error",
                        "status_url": f"https://{cfg.region_hosts[site.region]}/api/{site.site_key}/catalog/status",
                        "latest_entry": None, "alert_sent": True, "alert_episode_key": None,
                        "error_message": str(error),
                    })
        except Exception:
            counts["delivery_errors"] += 1
            LOGGER.exception("checker-error summary delivery failed for region %s", region)

    return {"checked_at": checked_at.isoformat(), "counts": counts}
