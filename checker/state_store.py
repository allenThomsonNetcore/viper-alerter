"""Persistent alert state stores."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import requests


class StateStore(Protocol):
    """Read and write alert state records."""
    def load(self) -> dict[str, Any]: ...
    def save(self, records: dict[str, Any]) -> None: ...


class FileStateStore:
    """Local JSON state store, suitable for development only."""
    def __init__(self, path: str) -> None:
        """Set the state file path."""
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        """Read state, returning an empty mapping when absent."""
        try: return json.loads(self.path.read_text())
        except FileNotFoundError: return {}

    def save(self, records: dict[str, Any]) -> None:
        """Atomically replace the state file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as handle: json.dump(records, handle)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)


class ConfigMapStateStore:
    """Kubernetes ConfigMap state store using resourceVersion compare-and-swap."""
    def __init__(self, name: str, namespace: str, retries: int = 5) -> None:
        """Set ConfigMap identity and configure in-cluster API access."""
        self.name, self.namespace, self.retries = name, namespace, retries
        self.base_url = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.getenv('KUBERNETES_SERVICE_PORT_HTTPS', '443')}"
        token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        self.token = token_path.read_text().strip()
        self.ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        self.url = f"{self.base_url}/api/v1/namespaces/{namespace}/configmaps/{name}"

    def _get(self) -> dict[str, Any]:
        response = requests.get(self.url, headers={"Authorization": f"Bearer {self.token}"}, verify=self.ca_path, timeout=15)
        response.raise_for_status()
        return response.json()

    def load(self) -> dict[str, Any]:
        """Read records from ConfigMap data field."""
        obj = self._get()
        return json.loads(obj.get("data", {}).get("records.json", "{}"))

    def save(self, records: dict[str, Any]) -> None:
        """Update ConfigMap with resourceVersion optimistic concurrency."""
        for _ in range(self.retries):
            obj = self._get()
            obj.setdefault("data", {})["records.json"] = json.dumps(records, separators=(",", ":"))
            response = requests.put(self.url, json=obj, headers={"Authorization": f"Bearer {self.token}"}, verify=self.ca_path, timeout=15)
            if response.status_code == 200: return
            if response.status_code != 409: response.raise_for_status()
        raise RuntimeError("ConfigMap state update conflict retry limit exceeded")


def prune(records: dict[str, Any], now: datetime, days: int = 7) -> dict[str, Any]:
    """Drop records whose scheduled timestamp is older than retention."""
    cutoff = now.astimezone(timezone.utc) - timedelta(days=days)
    result = {}
    for key, value in records.items():
        try: stamp = datetime.fromisoformat(value.get("scheduled_time", "").replace("Z", "+00:00"))
        except (ValueError, AttributeError): continue
        if stamp >= cutoff: result[key] = value
    return result
