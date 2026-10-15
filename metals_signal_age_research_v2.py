"""Research-only Metals signal-age study v2.

Reads historical XAU/XAG raw signals and measures fixed-horizon entry quality by
ordinal candidate age inside same-direction episodes. ZERO execution authority:
this module never places, blocks, resizes, closes, or modifies broker trades.
"""
from __future__ import annotations

import bisect
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Dict, List, Optional, Sequence

VERSION = "metals_signal_age_research_v2_2026_10_15"
BUCKETS = ((1, 5), (6, 10), (11, 15), (16, 20), (21, 24), (25, 32), (33, 10**9))
CUTOFFS = (5, 10, 15, 20, 24, 32, 10**9)
HORIZONS = (24, 48, 96)
EPISODE_GAP_HOURS = 24.0
ASSET_VARIANTS = {
    "XAUUSD": ("XAU", "XAUUSD", "XAU_USD"),
    "XAGUSD": ("XAG", "XAGUSD", "XAG_USD"),
}


def _d(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    try:
        x = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc)
    except Exception:
        return None


def _raw(core: Any, row: Any) -> Dict[str, Any]:
    try:
        x = core._raw_signal_json(row)
        return dict(x) if isinstance(x, dict) else {}
    except Exception:
        d = _d(row)
        try:
            x = json.loads(str(d.get("raw_json") or "{}"))
            if isinstance(x, dict) and isinstance(x.get("payload"), dict):
                x = x["payload"]
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}


def _candidate(core: Any, row: Any) -> Dict[str, Any]:
    raw = _raw(core, row)
    try:
        c = dict(core.metals_demo_candidate_for_row(raw, row) or {})
    except Exception:
        try:
            c = dict(core.metals_demo_candidate_for_row(raw, _d(row)) or {})
        except Exception:
            return {"candidate": False, "side": "", "score": None}
    try:
        side = str(core._metals_demo_side(c.get("demo_side")) or "").lower()
    except Exception:
        side = str(c.get("demo_side") or "").lower()
    return {
        "candidate": bool(c.get("demo_candidate")),
        "side": side if side in {"long", "short"} else "",
        "score": _f(c.get("score", c.get("candidate_score"))),
    }


def _load_asset(core: Any, asset: str) -> List[Dict[str, Any]]:
    variants = ASSET_VARIANTS.get(asset, (asset,))
    placeholders = ",".join("?" for _ in variants)
    with core.get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id,pair,timestamp_readable,exec_close,exec_high,exec_low,raw_json
            FROM raw_signals
            WHERE UPPER(pair) IN ({placeholders}) AND exec_close IS NOT NULL
            ORDER BY id ASC
            """,
            tuple(v.upper() for v in variants),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = _d(row)
        t = _dt(d.get("timestamp_readable"))
        close = _f(d.get("exec_close"))
        if t is None or close is None or close <= 0:
            continue
        d["_ts"] = t.timestamp()
        d["_close"] = close
        d["_high"] = _f(d.get("exec_high")) or close
        d["_low"] = _f(d.get("exec_low")) or close
        d["_cand"] = _candidate(core, row)
        out.append(d)
    return out


def _sl_pct(core: Any, asset: str) -> float:
    try:
        x = float(core._metals_demo_sl_pct(asset))
        return x if x > 0 else 1.0
    except Exception:
        return 1.0


def _forward_metrics(
    rows: Sequence[Dict[str, Any]],
    times: Sequence[float],
    idx: int,
    side: str,
    sl_pct: float,
    hours: int,
) -> Optional[Dict[str, float]]:
    entry = float(rows[idx]["_close"])
    target = float(rows[idx]["_ts"]) + float(hours) * 3600.0
    end_idx = bisect.bisect_left(times, target, lo=idx + 1)
    if end_idx >= len(rows) or float(times[end_idx]) - target > 3.5 * 3600.0:
        return None
    path = rows[idx + 1 : end_idx + 1]
    if not path:
        return None
    exit_px = float(rows[end_idx]["_close"])
    high = max(float(r["_high"]) for r in path)
    low = min(float(r["_low"]) for r in path)
    if side == "long":
        ret_pct = (exit_px / entry - 1.0) * 100.0
        mfe_pct = max(0.0, (high / entry - 1.0) * 100.0)
        mae_pct = max(0.0, (1.0 - low / entry) * 100.0)
    else:
        ret_pct = (1.0 - exit_px / entry) * 100.0
        mfe_pct = max(0.0, (1.0 - low / entry) * 100.0)
        mae_pct = max(0.0, (high / entry - 1.0) * 100.0)
    return {"r": ret_pct / sl_pct, "mfe_r": mfe_pct / sl_pct, "mae_r": mae_pct / sl_pct}


def _assign_ordinals(cands: List[Dict[str, Any]], gap_reset: bool) -> None:
    last_side = ""
    last_ts: Optional[float] = None
    episode = 0
    ordinal = 0
    for c in cands:
        side, ts = str(c["side"]), float(c["ts"])
        reset = side != last_side
        if gap_reset and last_ts is not None and side == last_side:
            reset = reset or (ts - last_ts > EPISODE_GAP_HOURS * 3600.0)
        if reset:
            episode += 1
            ordinal = 0
        ordinal += 1
        c["episode_gap" if gap_reset else "episode_side"] = episode
        c["ordinal_gap" if gap_reset else "ordinal_side"] = ordinal
        last_side, last_ts = side, ts


def _bucket(n: int) -> str:
    for lo, hi in BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
    return ""


def _summary(vals: List[Dict[str, float]], key: str) -> Dict[str, Any]:
    xs = [float(v[key]) for v in vals if v.get(key) is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": len(xs),
        "mean": mean(xs),
        "median": median(xs),
        "win_rate": sum(x > 0 for x in xs) / len(xs),
    }


def _bucket_table(records: List[Dict[str, Any]], ordinal_key: str, horizon: int) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, float]]] = defaultdict(list)
    for r in records:
        m = r["h"].get(horizon)
        if m:
            groups[_bucket(int(r[ordinal_key]))].append(m)
    labels = [f"{lo}-{hi}" if hi < 10**9 else f"{lo}+" for lo, hi in BUCKETS]
    out = []
    for label in labels:
        vals = groups.get(label, [])
        rr, mfe, mae = _summary(vals, "r"), _summary(vals, "mfe_r"), _summary(vals, "mae_r")
        out.append({
            "bucket": label,
            "n": rr["n"],
            "mean_r": rr["mean"],
            "median_r": rr["median"],
            "win_rate": rr["win_rate"],
            "mean_mfe_r": mfe["mean"],
            "mean_mae_r": mae["mean"],
        })
    return out


def _cutoffs(records: List[Dict[str, Any]], ordinal_key: str, horizon: int) -> List[Dict[str, Any]]:
    out = []
    for cutoff in CUTOFFS:
        xs = [
            float(r["h"][horizon]["r"])
            for r in records
            if int(r[ordinal_key]) <= cutoff and r["h"].get(horizon)
        ]
        out.append({
            "cutoff": None if cutoff >= 10**9 else cutoff,
            "label": "all" if cutoff >= 10**9 else str(cutoff),
            "n": len(xs),
            "total_r": sum(xs),
            "mean_r_per_trade": mean(xs) if xs else None,
        })
    return out


def _weights(n: int) -> Dict[str, float]:
    return {
        "ALL_EQUAL": 1.0,
        "CAP20": 1.0 if n <= 20 else 0.0,
        "CAP32": 1.0 if n <= 32 else 0.0,
        "INDICES_INSPIRED": 2.0 if n <= 5 else (1.0 if n <= 10 else (0.5 if n <= 20 else 0.0)),
        "GENTLE_TAPER": 1.25 if n <= 5 else (1.0 if n <= 10 else (0.75 if n <= 20 else (0.5 if n <= 32 else 0.25))),
    }


def _policies(records: List[Dict[str, Any]], ordinal_key: str, horizon: int) -> List[Dict[str, Any]]:
    agg: Dict[str, Dict[str, float]] = defaultdict(lambda: {"r": 0.0, "risk": 0.0, "trades": 0.0})
    for r in records:
        m = r["h"].get(horizon)
        if not m:
            continue
        for name, w in _weights(int(r[ordinal_key])).items():
            if w <= 0:
                continue
            agg[name]["r"] += w * float(m["r"])
            agg[name]["risk"] += w
            agg[name]["trades"] += 1
    out = []
    for name in ("ALL_EQUAL", "CAP20", "CAP32", "INDICES_INSPIRED", "GENTLE_TAPER"):
        a = agg[name]
        out.append({
            "policy": name,
            "weighted_total_r": a["r"],
            "risk_units": a["risk"],
            "r_per_risk_unit": a["r"] / a["risk"] if a["risk"] else None,
            "included_trade_count": int(a["trades"]),
        })
    return out


def _loo(records: List[Dict[str, Any]], episode_key: str, ordinal_key: str, horizon: int) -> Dict[str, Any]:
    by_ep: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r["h"].get(horizon):
            by_ep[int(r[episode_key])].append(r)
    eligible = {eid: rs for eid, rs in by_ep.items() if max(int(x[ordinal_key]) for x in rs) >= 10}
    if len(eligible) < 3:
        return {"available": False, "eligible_episodes": len(eligible)}
    choices = (5, 10, 15, 20, 24, 32)
    selected: Counter = Counter()
    holdout, all_holdout = 0.0, 0.0
    detail = []
    for hold_id, hold in eligible.items():
        train = [r for eid, rs in eligible.items() if eid != hold_id for r in rs]
        scores = {
            c: sum(float(r["h"][horizon]["r"]) for r in train if int(r[ordinal_key]) <= c)
            for c in choices
        }
        best = max(choices, key=lambda c: (scores[c], -c))
        selected[best] += 1
        hr = sum(float(r["h"][horizon]["r"]) for r in hold if int(r[ordinal_key]) <= best)
        ha = sum(float(r["h"][horizon]["r"]) for r in hold)
        holdout += hr
        all_holdout += ha
        detail.append({"episode": hold_id, "selected_cutoff": best, "holdout_r": hr, "holdout_all_r": ha})
    return {
        "available": True,
        "eligible_episodes": len(eligible),
        "selected_cutoff_counts": dict(sorted(selected.items())),
        "holdout_selected_total_r": holdout,
        "holdout_all_total_r": all_holdout,
        "episodes": detail,
    }


def _side(records: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    rs = [r for r in records if r["side"] == side]
    if not rs:
        return {"side": side, "candidate_count": 0}
    out: Dict[str, Any] = {
        "side": side,
        "candidate_count": len(rs),
        "first_signal": rs[0]["time"],
        "last_signal": rs[-1]["time"],
        "episode_counts": {
            "gap_24h": len({int(r["episode_gap"]) for r in rs}),
            "side_flip_only": len({int(r["episode_side"]) for r in rs}),
        },
    }
    for name, ordinal_key, episode_key in (
        ("gap_24h", "ordinal_gap", "episode_gap"),
        ("side_flip_only", "ordinal_side", "episode_side"),
    ):
        out[name] = {
            "buckets_24h": _bucket_table(rs, ordinal_key, 24),
            "buckets_48h": _bucket_table(rs, ordinal_key, 48),
            "buckets_96h": _bucket_table(rs, ordinal_key, 96),
            "cutoffs_48h": _cutoffs(rs, ordinal_key, 48),
            "policies_48h": _policies(rs, ordinal_key, 48),
            "leave_one_episode_out_48h": _loo(rs, episode_key, ordinal_key, 48),
        }
    return out


def study(core: Any) -> Dict[str, Any]:
    core.init_db()
    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "research_only": True,
        "execution_authority": False,
        "strategy_changed": False,
        "definitions": {
            "signal_age": "ordinal current-rule candidate within a same-direction episode",
            "gap_24h_episode": "reset on side change or >24h gap between same-side candidates",
            "side_flip_only_episode": "reset only when candidate side changes",
            "r_basis": "fixed-horizon directional return divided by current asset demo SL percent",
            "important": "entry-quality study only; not an ATR2/MFE25/harvest execution replay",
        },
        "assets": {},
    }
    for asset in ("XAUUSD", "XAGUSD"):
        rows = _load_asset(core, asset)
        times = [float(r["_ts"]) for r in rows]
        sl = _sl_pct(core, asset)
        cands: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows):
            c = row.get("_cand") or {}
            if not c.get("candidate") or c.get("side") not in {"long", "short"}:
                continue
            rec = {
                "raw_signal_id": int(row.get("id") or 0),
                "time": str(row.get("timestamp_readable") or ""),
                "ts": float(row["_ts"]),
                "side": str(c["side"]),
                "score": c.get("score"),
                "h": {},
            }
            for h in HORIZONS:
                m = _forward_metrics(rows, times, idx, rec["side"], sl, h)
                if m:
                    rec["h"][h] = m
            cands.append(rec)
        _assign_ordinals(cands, True)
        _assign_ordinals(cands, False)
        result["assets"][asset] = {
            "raw_signal_count": len(rows),
            "candidate_count": len(cands),
            "pair_variants": list(ASSET_VARIANTS[asset]),
            "sl_pct_used": sl,
            "long": _side(cands, "long"),
            "short": _side(cands, "short"),
        }
    return result


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_SIGNAL_AGE_RESEARCH_INSTALLED", False):
        return {"installed": True, "version": VERSION, "research_only": True, "execution_authority": False}

    def endpoint() -> Dict[str, Any]:
        try:
            return study(core)
        except Exception as exc:
            return {
                "ok": False,
                "version": VERSION,
                "research_only": True,
                "execution_authority": False,
                "strategy_changed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    app.add_api_route("/analysis/metals-signal-age", endpoint, methods=["GET"])
    core.metals_signal_age_research = endpoint
    core._METALS_SIGNAL_AGE_RESEARCH_INSTALLED = True
    return {
        "installed": True,
        "version": VERSION,
        "research_only": True,
        "execution_authority": False,
        "strategy_changed": False,
        "route": "/analysis/metals-signal-age",
    }
