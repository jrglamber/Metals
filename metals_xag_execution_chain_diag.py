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


def ownership_snapshot(core: Any) -> Dict[str, Any]:
    """Read-only live-vs-practice account and trade-ID overlap verification."""
    import xag_short_live_override as live
    practice_account = str(getattr(core, "METALS_DEMO_OANDA_ACCOUNT_ID", "") or "").strip()
    live_account = str(getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "") or "").strip()
    out = {
        "ok": False,
        "same_oanda_account_id": bool(practice_account and live_account and practice_account == live_account),
        "practice_env": str(getattr(core, "METALS_DEMO_OANDA_ENV", "") or ""),
        "live_env": str(getattr(core, "METALS_XAU_LIVE_OANDA_ENV", "") or ""),
        "broker_writes": False,
    }
    try:
        with core.get_conn() as conn:
            practice_rows = [dict(r) for r in conn.execute(
                "SELECT broker_trade_id, side FROM metals_demo_trade_links "
                "WHERE asset='XAGUSD' AND status='OPEN'"
            ).fetchall()]
            live_rows = [dict(r) for r in conn.execute(
                "SELECT broker_trade_id, side FROM metals_xag_live_trade_links "
                "WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
            ).fetchall()]
        pids = {str(r.get("broker_trade_id")) for r in practice_rows if r.get("broker_trade_id")}
        lids = {str(r.get("broker_trade_id")) for r in live_rows if r.get("broker_trade_id")}
        snap = live._open_trades(core)  # GET OANDA only
        broker = list(snap.get("xag") or []) if snap.get("ok") else []
        bids = {str(t.get("id")) for t in broker if t.get("id")}
        shorts = sum(float(t.get("currentUnits") or 0) < 0 for t in broker)
        out.update({
            "ok": bool(snap.get("ok")),
            "live_broker_xag_open_count": len(bids),
            "live_broker_xag_short_count": shorts,
            "local_practice_xag_open_links": len(practice_rows),
            "local_live_xag_open_links": len(live_rows),
            "live_broker_xag_unlinked_count": len(bids - lids),
            "live_broker_xag_overlap_with_practice_links": len(bids & pids),
            "practice_and_live_link_overlap_count": len(pids & lids),
            "live_broker_xag_ids_all_matched_to_practice": bool(bids and bids.issubset(pids)),
            "diagnosis": ("same_account_or_shared_trade_ids" if out["same_oanda_account_id"] or (bids & pids)
                          else "unmanaged_broker_xag_positions" if bids-lids
                          else "no_unmanaged_broker_xag"),
        })
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
