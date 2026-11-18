"""Evaluator behavior tests."""
from datetime import datetime, timezone

import pytest

from checker.csv_loader import Site
from checker.evaluator import evaluate

UTC = timezone.utc
T = datetime(2026, 10, 23, 6, 0, tzinfo=UTC)
SITE = Site("test", "apac", "0 */3 * * *", 120)


@pytest.fixture
def sample():
    """Realistic endpoint sample entry."""
    return [{"fileName": "n/a", "uploadId": "8b373a7b", "status": "INDEXED", "timeStamp": 1792736390000,
             "message": "success time-taken: [ 6 minutes 16 seconds ]", "code": 200,
             "productCount": 15249, "date": "2026-10-23 06:19:50", "uploadIp": "N/A"}]


def ev(history, now, boundary=None):
    """Evaluate a fixture against the current three-hour slot."""
    return evaluate(SITE, history, now, T, boundary or datetime(2026, 10, 23, 9, 0, tzinfo=UTC))


def test_ok(sample):
    """Success history yields ok."""
    assert ev(sample, datetime(2026, 10, 23, 7, 0, tzinfo=UTC))["state"] == "ok"


@pytest.mark.parametrize("minute", [30, 60, 90])
def test_pending_during_grace(minute):
    """No history remains pending before deadline."""
    assert ev([], T.replace(minute=minute), datetime(2026, 10, 23, 9, 0, tzinfo=UTC))["state"] == "pending"


@pytest.mark.parametrize("status,expected", [("FAILED", "failed"), ("REJECTED", "rejected"), ("INDEXING", "pending"), ("WAT", "unknown")])
def test_status_classes(status, expected):
    """Terminal and in-flight status values are classified."""
    hist = [{"status": status, "code": 500 if status == "FAILED" else 200, "timeStamp": int(T.timestamp() * 1000)}]
    assert ev(hist, T.replace(minute=30))["state"] == expected


def test_stuck_and_missing_after_deadline():
    """In progress becomes stuck and absent history becomes missing at deadline."""
    now = datetime(2026, 10, 23, 8, 16, tzinfo=UTC)
    hist = [{"status": "INDEXING", "code": 200, "timeStamp": int(T.timestamp() * 1000)}]
    assert ev(hist, now)["state"] == "stuck"
    assert ev([], now)["state"] == "missing"


def test_late_entry_excluded_at_next_boundary():
    """Late completion beyond next cron time is not attributed to old run."""
    entry = [{"status": "INDEXED", "code": 200, "timeStamp": int(datetime(2026, 10, 23, 9, 1, tzinfo=UTC).timestamp() * 1000)}]
    assert ev(entry, datetime(2026, 10, 23, 10, 0, tzinfo=UTC))["state"] == "missing"


def test_previous_run_and_utc():
    """UTC-aware scheduled timestamps remain stable."""
    old = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)
    assert evaluate(SITE, [], datetime(2026, 10, 23, 6, 0, tzinfo=UTC), old, T)["state"] == "missing"
