"""CSV source loaders and row validation."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Protocol

from croniter import croniter


class Loader(Protocol):
    """A source capable of returning CSV text."""
    def load(self, source: str) -> str: ...


class LocalLoader:
    """Load CSV text from a local file."""
    def load(self, source: str) -> str:
        """Read and return a local UTF-8 CSV file."""
        with open(source, encoding="utf-8-sig") as handle:
            return handle.read()


class ObjectStorageLoader:
    """Optional S3/GCS CSV loader; cloud SDK imports are deferred."""
    def load(self, source: str) -> str:
        """Load an s3:// or gs:// URL using its optional provider SDK."""
        if source.startswith("s3://"):
            import boto3  # type: ignore[import-not-found]
            bucket, key = source[5:].split("/", 1)
            return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
        if source.startswith("gs://"):
            from google.cloud import storage  # type: ignore[import-not-found]
            bucket, key = source[5:].split("/", 1)
            return storage.Client().bucket(bucket).blob(key).download_as_text(encoding="utf-8")
        raise ValueError("Object storage source must start with s3:// or gs://")


class GoogleSheetLoader:
    """Optional Google Sheets loader using a service account."""
    def load(self, source: str) -> str:
        """Export a Google Sheet URL/ID as CSV using service account credentials."""
        import gspread  # type: ignore[import-not-found]
        client = gspread.service_account()
        sheet = client.open_by_key(source.rsplit("/", 1)[-1].split("?", 1)[0]).sheet1
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerows(sheet.get_all_values())
        return out.getvalue()


@dataclass(frozen=True)
class Site:
    """Validated configuration for one site."""
    site_key: str
    region: str
    cron: str
    max_duration_minutes: int
    version: str = "v1"
    app: str = "shopify"


def parse_sites(content: str, region_hosts: dict[str, str]) -> tuple[list[Site], list[str]]:
    """Validate CSV rows, returning usable sites and descriptive errors."""
    reader = csv.DictReader(io.StringIO(content))
    required = {"site_key", "region", "cron", "max_duration_minutes", "version", "app"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        return [], ["CSV header must include site_key,region,cron,max_duration_minutes,version,app"]
    sites: list[Site] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line, raw in enumerate(reader, start=2):
        row = {k: (v or "").strip() for k, v in raw.items() if k is not None}
        missing = [k for k in ("site_key", "region", "cron", "version", "app") if not row.get(k)]
        if missing:
            errors.append(f"line {line}: missing fields {','.join(missing)}")
            continue
        region, site_key, expr = row["region"], row["site_key"], row["cron"]
        if region not in region_hosts:
            errors.append(f"line {line}: unknown region {region}")
            continue
        version, app = row["version"].lower(), row["app"].lower()
        if version not in {"v1", "v2"}:
            errors.append(f"line {line}: version must be v1 or v2")
            continue
        if app not in {"shopify", "bigcommerce"}:
            errors.append(f"line {line}: app must be shopify or bigcommerce")
            continue
        if not croniter.is_valid(expr):
            errors.append(f"line {line}: invalid cron {expr}")
            continue
        key = (region, site_key)
        if key in seen:
            errors.append(f"line {line}: duplicate (region, site_key) {key}")
            continue
        try:
            duration = int(row.get("max_duration_minutes") or "120")
            if duration < 1:
                raise ValueError
        except ValueError:
            errors.append(f"line {line}: max_duration_minutes must be a positive integer")
            continue
        seen.add(key)
        sites.append(Site(site_key, region, expr, duration, version, app))
    return sites, errors


def describe_cron(expression: str) -> str:
    """Describe common five-field cron schedules in plain English (UTC)."""
    fields = expression.split()
    if len(fields) != 5:
        return f"Custom cron schedule (UTC): {expression}"
    minute, hour, day, month, weekday = fields
    if not croniter.is_valid(expression):
        return f"Invalid cron schedule: {expression}"

    if (hour, day, month, weekday) == ("*", "*", "*", "*") and minute.startswith("*/"):
        try:
            interval = int(minute[2:])
        except ValueError:
            interval = 0
        if interval == 40:
            return "At :00 and :40 every hour (the gaps alternate 40 and 20 minutes)"
        if interval == 1:
            return "Every minute"
        if interval > 0:
            return f"Every {interval} minutes"

    if (day, month, weekday) == ("*", "*", "*") and hour.startswith("*/") and minute.isdigit():
        try:
            interval = int(hour[2:])
            minute_value = int(minute)
        except ValueError:
            interval = minute_value = 0
        if interval == 1:
            return f"Every hour at minute {minute_value:02d} UTC"
        if interval > 1 and 0 <= minute_value <= 59:
            return f"Every {interval} hours at minute {minute_value:02d} UTC"

    if minute.isdigit() and hour.isdigit():
        try:
            minute_value, hour_value = int(minute), int(hour)
            if not (0 <= minute_value <= 59 and 0 <= hour_value <= 23):
                raise ValueError
        except ValueError:
            return f"Custom cron schedule (UTC): {expression}"
        time_text = f"{hour_value:02d}:{minute_value:02d} UTC"
        if day == month == weekday == "*":
            return f"Daily at {time_text}"
        if day.isdigit() and month == weekday == "*":
            return f"Monthly on day {int(day)} at {time_text}"
        if day == month == "*" and weekday != "*":
            names = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}
            weekdays = [names.get(item, item) for item in weekday.split(",")]
            return f"Weekly on {', '.join(weekdays)} at {time_text}"

    return f"Custom cron schedule (UTC): {expression}"
