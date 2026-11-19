"""Persistence for checker results and alert delivery records in Supabase."""
from __future__ import annotations

from typing import Any

import requests


class SupabaseStore:
    """Small PostgREST client using the server-side service role key."""

    def __init__(self, url: str, service_role_key: str, timeout: float = 15) -> None:
        """Create an authenticated client for the Supabase REST API."""
        if not url or not service_role_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        self.base_url = f"{url.rstrip('/')}/rest/v1"
        self.timeout = timeout
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, table: str, **kwargs: Any) -> Any:
        """Send one PostgREST request and return decoded JSON when present."""
        response = requests.request(method, f"{self.base_url}/{table}", headers=self.headers,
                                    timeout=self.timeout, **kwargs)
        response.raise_for_status()
        if response.content:
            return response.json()
        return None

    def upsert_run(self, record: dict[str, Any]) -> None:
        """Insert or update the latest result for a scheduled site run."""
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        requests.post(f"{self.base_url}/site_runs", params={"on_conflict": "region,site_key,scheduled_time"},
                      json=record, headers=headers, timeout=self.timeout).raise_for_status()

    def get_delivery(self, dedupe_key: str) -> dict[str, Any] | None:
        """Return a previously recorded delivery for a deduplication key."""
        rows = self._request("GET", "alert_deliveries", params={
            "select": "*", "dedupe_key": f"eq.{dedupe_key}", "limit": "1"})
        return rows[0] if rows else None

    def get_run(self, region: str, site_key: str, scheduled_time: str) -> dict[str, Any] | None:
        """Read the prior observation for one scheduled site run."""
        rows = self._request("GET", "site_runs", params={
            "select": "*", "region": f"eq.{region}", "site_key": f"eq.{site_key}",
            "scheduled_time": f"eq.{scheduled_time}", "limit": "1"})
        return rows[0] if rows else None

    def insert_delivery(self, record: dict[str, Any]) -> None:
        """Record a notification after the provider confirms it was sent."""
        headers = {**self.headers, "Prefer": "return=minimal"}
        requests.post(f"{self.base_url}/alert_deliveries", json=record, headers=headers,
                      timeout=self.timeout).raise_for_status()

    def deliveries_for_run(self, region: str, site_key: str, scheduled_time: str) -> list[dict[str, Any]]:
        """Read alert history for one scheduled site run."""
        return self._request("GET", "alert_deliveries", params={
            "select": "*", "region": f"eq.{region}", "site_key": f"eq.{site_key}",
            "scheduled_time": f"eq.{scheduled_time}", "order": "sent_at.asc"}) or []

    def latest_runs(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Read recent site-run rows for the status dashboard."""
        return self._request("GET", "site_runs", params={
            "select": "*", "order": "checked_at.desc", "limit": str(limit)}) or []

    def site_history(self, site_key: str, region: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Read recent run history for a site key."""
        params: dict[str, str] = {
            "select": "*", "site_key": f"eq.{site_key}", "order": "checked_at.desc,scheduled_time.desc",
            "limit": str(limit)}
        if region:
            params["region"] = f"eq.{region}"
        return self._request("GET", "site_runs", params=params) or []

    def prune(self, cutoff_iso: str) -> None:
        """Delete status and alert records older than the retention cutoff."""
        requests.delete(f"{self.base_url}/site_runs", params={"checked_at": f"lt.{cutoff_iso}"},
                        headers=self.headers, timeout=self.timeout).raise_for_status()
        requests.delete(f"{self.base_url}/alert_deliveries", params={"sent_at": f"lt.{cutoff_iso}"},
                        headers=self.headers, timeout=self.timeout).raise_for_status()
