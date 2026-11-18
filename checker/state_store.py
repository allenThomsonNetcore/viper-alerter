"""Persistent alert state stores."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


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
    """Kubernetes ConfigMap state store using kubectl resourceVersion CAS."""
    def __init__(self, name: str, namespace: str, retries: int = 5) -> None:
        """Set ConfigMap identity and conflict retry count."""
        self.name, self.namespace, self.retries = name, namespace, retries

    def _get(self) -> dict[str, Any]:
        result = subprocess.run(["kubectl", "get", "configmap", self.name, "-n", self.namespace, "-o", "json"], check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def load(self) -> dict[str, Any]:
        """Read records from ConfigMap data field."""
        obj = self._get()
        return json.loads(obj.get("data", {}).get("records.json", "{}"))

    def save(self, records: dict[str, Any]) -> None:
        """Update ConfigMap with resourceVersion optimistic concurrency."""
        for _ in range(self.retries):
            obj = self._get()
            obj.setdefault("data", {})["records.json"] = json.dumps(records, separators=(",", ":"))
            proc = subprocess.run(["kubectl", "replace", "-f", "-"], input=json.dumps(obj), capture_output=True, text=True)
            if proc.returncode == 0: return
            if "Conflict" not in proc.stderr and "conflict" not in proc.stderr: raise RuntimeError(proc.stderr)
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
