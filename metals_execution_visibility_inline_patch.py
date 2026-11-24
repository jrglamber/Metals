"""Render a compact, single-outcome view of recent Metals signals."""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict

import metals_execution_visibility_patch as visibility
import metals_xag_live_signal_status_panel as xag_live_panel

VERSION = "metals_signals_lane_separated_outcomes_v2_2026_11_24"


def _label(state: str) -> tuple[str, str]:
    s = str(state or "").strip().upper()
    if s == "OPENED":
        return "OPENED", "pos"
    if s == "INTENTIONAL_HALF_FREQUENCY_SKIP":
        return "SKIPPED — 0.5× THROTTLE", "warn"
    if s.startswith("BLOCKED") or s in {"NOT_OPENED", "NO_EXECUTION_RECORD"}:
        return "BLOCKED", "neg"
    return s or "—", "flat"


def _display_for(row: Dict[str, Any], signal_quality: str) -> tuple[str, str, str]:
    quality = str(signal_quality or "").strip().lower()
    state = str(row.get("execution_state") or "").strip().upper()
    # A broker-linked trade is the authoritative outcome. A model's earlier
    # watch classification must never hide an order that actually opened.
    if state == "OPENED":
        return "OPENED", "pos", str(row.get("reason") or "broker trade linked")
    if state == "NO_EXECUTION_RECORD" and quality in {"watch_candidate", "watch", "watch_only"}:
        return "WATCH ONLY", "warn", "candidate marked non-executable; no live trade link found"
    label, cls = _label(state)
    reason = str(row.get("reason") or "") or "—"
    return label, cls, reason


def _plain_cell(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"</(?:strong|b|span|em)\s*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _compact_signals_table(
    rendered: str,
    outcomes: Dict[str, Dict[str, Any]],
    qualities: Dict[str, str],
    xag_live: Dict[str, Dict[str, Any]],
) -> str:
    # The page can contain other signal tables (for example the independent
    # XAG live audit). Target only the model-candidate table this renderer owns.
    match = next((
        candidate for candidate in re.finditer(r"<table>.*?</table>", rendered, flags=re.DOTALL)
        if "<th>Model Candidate</th>" in candidate.group(0)
    ), None)
    if not match:
        return rendered
    old_table = match.group(0)
    rows = re.findall(r"<tr>(.*?)</tr>", old_table, flags=re.DOTALL)
    if not rows:
        return rendered
    headers = (
        "<th>Time</th><th>Asset</th><th>Candidate</th>"
        "<th>Candidate Gate</th><th>XAG Practice Guard</th><th>Live Outcome</th><th>Detail</th>"
    )
    compact_rows = []
    for row_html in rows[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.DOTALL)
        if len(cells) < 16:
            continue
        values = [_plain_cell(cell) for cell in cells]
        rid = values[0]
        is_candidate = values[5].upper() == "TRUE"
        eligible = values[6].upper() == "TRUE"
        asset = values[2]
        candidate = values[7] if is_candidate else "—"
        eligibility = "Yes" if eligible else "No"
        guard = values[8] if asset == "XAGUSD" else "—"
        if asset == "XAGUSD":
            if "XAG_GUARD_PENDING_FAIL_CLOSED" in guard:
                guard = guard.replace("XAG_GUARD_PENDING_FAIL_CLOSED", "PENDING · fail-closed")
            live = xag_live.get(rid) or {}
            live_state = str(live.get("state") or "")
            if live_state == "LIVE TRACKED":
                result, result_class = "LIVE TRACKED", "pos"
                reason = "broker-linked live position"
            elif live_state and live_state != "NO LIVE EXECUTION AUDIT":
                result = live_state
                result_class = "warn" if "BLOCK" in live_state or "FAILED" in live_state else "flat"
                reason = str(live.get("reason") or "live audit recorded")
            else:
                result, result_class = "NO LIVE AUDIT", "flat"
                reason = "No live XAG decision recorded for this signal"
        elif (outcome := outcomes.get(rid)):
            result, result_class, reason = _display_for(outcome, qualities.get(rid, ""))
        elif is_candidate and not eligible:
            result, result_class, reason = "BLOCKED", "neg", values[12]
        elif is_candidate:
            result, result_class, reason = "ELIGIBLE · NO LIVE LINK", "warn", "No broker-linked execution found"
        else:
            result, result_class, reason = "NO CANDIDATE", "flat", values[12]
        compact_rows.append(
            "<tr>"
            f"<td>{html.escape(values[1])}</td>"
            f"<td><strong>{html.escape(asset)}</strong></td>"
            f"<td>{html.escape(candidate)}</td>"
            f"<td>{html.escape(eligibility)}</td>"
            f"<td>{html.escape(guard or '—')}</td>"
            f"<td class=\"{result_class}\"><strong>{html.escape(result)}</strong></td>"
            f"<td>{html.escape(reason or '—')}</td>"
            "</tr>"
        )
    compact = (
        '<div class="table-scroll"><table><thead><tr>' + headers + '</tr></thead><tbody>'
        + ("".join(compact_rows) or '<tr><td colspan="7">No recent Metals signals.</td></tr>')
        + '</tbody></table></div>'
    )
    return rendered[:match.start()] + compact + rendered[match.end():]


def _recent_xau_signal_qualities(core: Any, limit: int) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with core.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, raw_json
                FROM raw_signals
                WHERE UPPER(pair) IN ('XAUUSD','XAU')
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        for raw_row in rows:
            try:
                d = dict(raw_row)
            except Exception:
                continue
            rid = str(d.get("id") or "")
            raw = d.get("raw_json")
            payload: Dict[str, Any] = {}
            if isinstance(raw, dict):
                payload = raw
            else:
                try:
                    parsed = json.loads(str(raw or "{}"))
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}
            out[rid] = str(payload.get("signal_quality") or "").strip().lower()
    except Exception:
        return {}
    return out


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_EXECUTION_VISIBILITY_INLINE_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_EXECUTION_VISIBILITY_INLINE_STATUS", {}))

    original = getattr(core, "_metals_latest_30_signals_html", None)
    if not callable(original):
        raise RuntimeError("Inline execution visibility install refused: latest-signals renderer missing")

    def patched(limit: int = 30) -> str:
        rendered = str(original(limit) or "")
        try:
            outcomes = {
                str(r.get("raw_signal_id")): r
                for r in visibility._recent_xau_outcomes(core, max(30, int(limit or 30)))
            }
        except Exception:
            outcomes = {}
        qualities = _recent_xau_signal_qualities(core, max(30, int(limit or 30)))
        rendered = re.sub(
            r'<details><summary>Live XAU Execution Outcomes</summary>.*?</details>',
            '', rendered, flags=re.DOTALL,
        )
        # Keep one concise table with a single final outcome for each signal.
        rendered = re.sub(
            r'<div class="section-note small"><strong>Latest 30 Metals Signals\.</strong>.*?</div>',
            '<div class="section-note small"><strong>Latest 30 Metals Signals.</strong> '
            'The XAG Practice Guard is separate from the live lane. Live Outcome uses broker links/live audits; '
            'Candidate Gate eligibility alone does not mean a trade opened.</div>',
            rendered, count=1, flags=re.DOTALL,
        )
        try:
            xag_live = {
                str(r.get("signal_id")): r
                for r in xag_live_panel.rows(core, limit=max(30, int(limit or 30)))
            }
        except Exception:
            xag_live = {}
        return _compact_signals_table(rendered, outcomes, qualities, xag_live)

    core._metals_latest_30_signals_html = patched
    core.METALS_XAU_EXECUTION_VISIBILITY_INLINE_VERSION = VERSION
    core._METALS_XAU_EXECUTION_VISIBILITY_INLINE_INSTALLED = True

    probe = patched(8)
    watch_unit = _display_for(
        {"execution_state": "NO_EXECUTION_RECORD", "reason": ""},
        "watch_candidate",
    )
    opened_unit = _display_for(
        {"execution_state": "OPENED", "reason": "broker trade linked"},
        "watch_candidate",
    )
    # This self-test must be independent of current live history. A prior version
    # required a recent real half-frequency skip to remain within the probe rows,
    # which could make a healthy service fail startup simply as that event aged out.
    skip_unit = _display_for(
        {
            "execution_state": "INTENTIONAL_HALF_FREQUENCY_SKIP",
            "reason": "post-32 broker-safe alternating overflow policy",
        },
        "",
    )
    checks = {
        "compact_header_present": all(
            f"<th>{label}</th>" in probe
            for label in ("Time", "Asset", "Candidate", "Candidate Gate", "XAG Practice Guard", "Live Outcome", "Detail")
        ),
        "duplicate_outcomes_section_removed": "Live XAU Execution Outcomes" not in probe,
        "opened_overrides_watch_classification": opened_unit == (
            "OPENED", "pos", "broker trade linked",
        ),
        "half_frequency_skip_renderer": skip_unit == (
            "SKIPPED — 0.5× THROTTLE",
            "warn",
            "post-32 broker-safe alternating overflow policy",
        ),
        "watch_candidate_semantic_label": watch_unit == (
            "WATCH ONLY",
            "warn",
            "candidate marked non-executable; no live trade link found",
        ),
    }
    if not all(checks.values()):
        core._metals_latest_30_signals_html = original
        core._METALS_XAU_EXECUTION_VISIBILITY_INLINE_INSTALLED = False
        raise RuntimeError(f"Inline XAU execution visibility self-test failed: {checks}")

    status = {
        "installed": True,
        "version": VERSION,
        "presentation_only": True,
        "strategy_changed": False,
        "startup_self_test": checks,
    }
    core._METALS_XAU_EXECUTION_VISIBILITY_INLINE_STATUS = status
    return status
