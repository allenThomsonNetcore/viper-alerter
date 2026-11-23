"""Pure schedule and history evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter

from checker.csv_loader import Site

UTC = timezone.utc
SUCCESS = {"INDEXED"}
FAILURE = {"FAILED", "ERROR", "ABORTED"}
REJECTED = {"REJECTED"}
IN_PROGRESS = {"INDEXING", "PROCESSING", "IN_PROGRESS", "ACCEPTED", "QUEUED"}


def schedule_times(site: Site, now: datetime) -> tuple[datetime, datetime, datetime]:
    """Return previous and latest schedule times and next schedule after latest."""
    now = now.astimezone(UTC)
    # croniter's get_prev is exclusive; shifting by a microsecond makes a run
    # scheduled exactly at `now` count as the latest due run.
    itr = croniter(site.cron, now + timedelta(microseconds=1))
    latest = itr.get_prev(datetime).replace(tzinfo=UTC)
    previous = croniter(site.cron, latest).get_prev(datetime).replace(tzinfo=UTC)
    following = croniter(site.cron, latest).get_next(datetime).replace(tzinfo=UTC)
    return previous, latest, following


def evaluate(site: Site, history: list[dict[str, Any]], now: datetime, scheduled_time: datetime,
             next_scheduled_time: datetime, buffer_minutes: int = 15,
             timestamp_semantics: str = "finish") -> dict[str, Any]:
    """Evaluate one scheduled run without performing I/O."""
    now = now.astimezone(UTC)
    deadline = scheduled_time + timedelta(minutes=site.max_duration_minutes + buffer_minutes)
    candidates = []
    for entry in history:
        try:
            ts = datetime.fromtimestamp(float(entry["timeStamp"]) / 1000, UTC)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if timestamp_semantics == "finish":
            belongs = scheduled_time <= ts < next_scheduled_time
        else:
            belongs = scheduled_time <= ts < min(next_scheduled_time, deadline)
        if belongs:
            candidates.append((ts, entry))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    def classify(entry: dict[str, Any]) -> str:
        status = str(entry.get("status", "UNKNOWN")).upper()
        code = entry.get("code")
        if status in REJECTED: return "rejected"
        if status in FAILURE: return "failed"
        if status in IN_PROGRESS: return "in_progress"
        if status not in SUCCESS and code is not None and code != 200: return "failed"
        if status in SUCCESS and code == 200: return "ok"
        return "unknown"
    classes = [(classify(entry), entry) for _, entry in candidates]
    # History is sorted newest-first. The newest known entry is the current
    # state for this scheduled run; an older success must not mask a later fail.
    if classes and classes[0][0] != "in_progress":
        state, latest = classes[0]
    elif classes and all(c == "in_progress" for c, _ in classes):
        state, latest = ("stuck" if now >= deadline else "pending"), classes[0][1]
    elif classes and classes[0][0] == "in_progress":
        state, latest = ("stuck" if now >= deadline else "pending"), classes[0][1]
    elif not classes:
        state, latest = ("missing" if now >= deadline else "pending"), None
    else:
        state, latest = "unknown", classes[0][1]
    return {"state": state, "scheduled_time": scheduled_time, "deadline": deadline,
            "overdue_minutes": max(0, int((now - deadline).total_seconds() // 60)),
            "latest_entry": latest, "entries": [e for _, e in candidates]}
