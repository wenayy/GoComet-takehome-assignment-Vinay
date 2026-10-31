"""report: turn what the store holds into report.json for one as_of date and mode."""
from __future__ import annotations

from .store import Store


def build_report(as_of: str, mode: str, state_dir: str) -> dict:
    Store(state_dir)
    return {"schema_version": "1.0", "as_of": as_of, "mode": mode, "findings": [],
            "portfolio": {"cashback": [], "expiry": [], "late_fee_risk": []}}
