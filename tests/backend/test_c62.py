"""C62 — Sträcka (km), Vehicle och Antal passagerare (Bezalas ConditionalField)."""

from __future__ import annotations

import os

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.services import c62_conditional as c62  # noqa: E402


class _Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text
        self.headers = {"content-type": "application/json" if data is not None else "text/html"}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class ProbeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_raw(self, path):
        self.calls.append(path)
        return self.routes.get(path, _Resp(404, text="Not found"))

    def close(self):
        pass


def test_probe_finds_conditional_values_and_only_reads():
    cv = [{"id": 266932, "field_id": 3, "value": "1"}, {"id": 1, "field_id": 1, "value": "Train"}]
    client = ProbeClient({
        "/transactions/5594772?include=conditional_values": _Resp(200, {"id": 5594772}),
        "/receipts/5594772": _Resp(200, {"id": 5594772, "conditional_values": cv}),
    })
    out = c62.probe_reads("5594772", client=client)
    assert out["found_in"] == ["/receipts/5594772"]
    assert len(client.calls) == len(c62.PROBE_PATHS)
    first = out["results"][0]
    assert first["status"] == 200 and "conditional" not in first


def test_get_raw_rejects_odd_paths():
    import pytest

    from app.services.bezala_client import BezalaClient, BezalaError

    c = BezalaClient.__new__(BezalaClient)
    with pytest.raises(BezalaError):
        c.get_raw("https://evil.example/x")
    with pytest.raises(BezalaError):
        c.get_raw("/../x")
