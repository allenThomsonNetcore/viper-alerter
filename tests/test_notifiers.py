"""Alert notification text formatting tests."""
from datetime import datetime, timezone

from checker.csv_loader import Site
from checker.notifiers import alert_text


def test_alert_timestamps_are_explicitly_ist():
    site = Site("demo", "sg", "0 */3 * * *", 120)
    scheduled = datetime(2026, 11, 19, 14, 30, tzinfo=timezone.utc)
    checked = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    entry = {"status": "FAILED", "code": 500, "timeStamp": 1795098600000,
             "date": "2026-11-19 20:00:00", "message": "failed"}
    _, body = alert_text(site, {"state": "failed", "scheduled_time": scheduled,
                                "overdue_minutes": 15, "latest_entry": entry},
                         "feed-apac.unbxd.io", checked)
    assert "Scheduled time (IST): 19 Nov 2026, 08:00:00 PM IST" in body
    assert "Checked at (IST): 19 Nov 2026, 08:30:00 PM IST" in body
    assert "API timestamp (IST):" in body
    assert "2026-11-19 20:00:00" not in body
