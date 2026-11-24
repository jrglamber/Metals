"""Persistent, reporting-only peak of *combined* live Metals broker open P&L.

A combined peak cannot be reconstructed by adding XAU and XAG peaks taken at
different times. This begins a new observed live XAU+XAG SHORT series when the
first broker-verified combined snapshot is seen, and persists its peak across
deployments using an isolated table. Never use this for execution or the XAU
harvest/stacking high-water controls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

VERSION = "metals_combined_observed_hwm_v2_2026_11_24"
_TABLE = "metals_combined_live_pnl_observed_hwm"


def _timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing broker snapshot timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Naive broker snapshot timestamp")
    return parsed.astimezone(timezone.utc).isoformat()


def _finite(value):
    try:
        number = float(value)
        if not isfinite(number):
            raise ValueError("Nonfinite combined Metals P&L")
        return number
    except (TypeError, ValueError) as exc:
        raise ValueError("Verified combined Metals P&L unavailable") from exc


def observe(core, proof):
    """Record a single validated combined snapshot; never alter trading state."""
    if not isinstance(proof, dict) or proof.get("ok") is not True:
        raise ValueError("Unverified combined Metals snapshot; high-water not updated")
    if proof.get("scope") != "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT":
        raise ValueError("Incorrect combined high-water scope")
    pnl = _finite(proof.get("open_pnl_gbp"))
    observed_at = proof.get("observed_at_utc")
    observed_at = _timestamp(observed_at)
    start_hwm = max(0.0, pnl)
    with core.get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS metals_combined_live_pnl_observed_hwm (
                 id INTEGER PRIMARY KEY,
                 started_at_utc TEXT NOT NULL,
                 high_water_gbp REAL NOT NULL,
                 high_water_at_utc TEXT NOT NULL
               )"""
        )
        # This table is reporting-only and is intentionally NOT tied to the
        # existing XAU basket/harvest cycle, which has independent reset rules.
        conn.execute(
            """INSERT INTO metals_combined_live_pnl_observed_hwm
               (id, started_at_utc, high_water_gbp, high_water_at_utc)
               VALUES (1, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
            (observed_at, start_hwm, observed_at),
        )
        # Predicate ensures the peak is monotone even with concurrent readers.
        conn.execute(
            """UPDATE metals_combined_live_pnl_observed_hwm
               SET high_water_gbp=?, high_water_at_utc=?
               WHERE id=1 AND high_water_gbp < ?
                 AND high_water_at_utc <= ?""",
            (pnl, observed_at, pnl, observed_at),
        )
        row = conn.execute(
            """SELECT started_at_utc, high_water_gbp, high_water_at_utc
               FROM metals_combined_live_pnl_observed_hwm WHERE id=1"""
        ).fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("Combined Metals high-water record unavailable")
    # Production DB rows can be sqlite Row or dict-like pg adapter rows.
    start = str(row["started_at_utc"])
    peak = _finite(row["high_water_gbp"])
    peak_at = str(row["high_water_at_utc"])
    if peak < 0 or peak + 0.00001 < pnl:
        raise ValueError("Persisted combined Metals high-water inconsistent")
    giveback = max(0.0, peak - pnl)
    return {
        "high_water_status": "OBSERVED_COMBINED_LIVE",
        "high_water_gbp": round(peak, 4),
        "high_water_at_utc": peak_at,
        "high_water_tracking_started_at_utc": start,
        "giveback_gbp": round(giveback, 4),
        "giveback_pct": round(100.0 * giveback / peak, 2) if peak > 0 else None,
        "high_water_scope": "LIVE_XAU_PLUS_XAG_SHORT_OBSERVED_SINCE_START",
        "high_water_version": VERSION,
        "historical_backfill_complete": False,
        "affects_trading_or_xau_protection": False,
    }
