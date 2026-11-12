"""Read-only recent XAG SHORT execution chain probe.

This is a diagnostic; it never replays orders or changes strategy state.
A core startup probe reports the exact upstream reason (candidate/audit/demo
link/live audit) for the latest XAG signals.
"""
from __future__ import annotations

from typing import Any, Dict

VERSION = "metals_xag_short_chain_diag_v1_2026_11_12"


def snapshot(core: Any, limit: int = 20) -> Dict[str, Any]:
    result = {"ok": True, "version": VERSION, "signals": [],
              "execution_authority": False, "broker_writes": False}
    try:
        core.init_db()
        with core.get_conn() as conn:
            signals = conn.execute(
                "SELECT id, pair, timestamp_readable, received_at_utc "
                "FROM raw_signals WHERE UPPER(pair) IN ('XAGUSD','XAG') "
                "ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 50)),)
            ).fetchall()
            for sr in signals:
                s = dict(sr)
                rid = int(s["id"])
                audits = [
                    dict(x) for x in conn.execute(
                        "SELECT action, status, message, created_at_utc "
                        "FROM metals_demo_execution_audit "
                        "WHERE raw_signal_id=? AND asset='XAGUSD' "
                        "ORDER BY id DESC LIMIT 3", (rid,)
                    ).fetchall()
                ]
                live_audits = [
                    dict(x) for x in conn.execute(
                        "SELECT action, status, reason, created_at_utc "
                        "FROM metals_xag_live_execution_audit "
                        "WHERE raw_signal_id=? ORDER BY id DESC LIMIT 3", (rid,)
                    ).fetchall()
                ]
                demo = conn.execute(
                    "SELECT status, side FROM metals_demo_trade_links "
                    "WHERE raw_signal_id=? LIMIT 1", (rid,)
                ).fetchone()
                live = conn.execute(
                    "SELECT status, side FROM metals_xag_live_trade_links "
                    "WHERE raw_signal_id=? LIMIT 1", (rid,)
                ).fetchone()
                s["practice_link"] = dict(demo) if demo else None
                s["live_link"] = dict(live) if live else None
                s["practice_audits"] = audits
                s["live_audits"] = live_audits
                result["signals"].append(s)
            result["live_xag_short_link_count"] = conn.execute(
                "SELECT COUNT(*) AS n FROM metals_xag_live_trade_links"
            ).fetchone()["n"]
        return result
    except Exception as exc:
        return {"ok": False, "version": VERSION,
                "error": f"{type(exc).__name__}: {exc}",
                "execution_authority": False, "broker_writes": False}
