"""Inline final XAU execution state in Latest 30 Metals Signals.

Presentation-only overlay. The existing candidate columns describe pre-execution
eligibility. This patch adds the actual live XAU outcome beside that eligibility
state so an approved candidate can visibly read OPENED, INTENTIONAL SKIP, or the
real execution blocker on the same row. Non-executable watch candidates are
shown explicitly as WATCH ONLY rather than leaking legacy cleaner blocker text.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict

import metals_execution_visibility_patch as visibility

VERSION = "metals_xau_execution_visibility_inline_v2_2026_10_14"


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
    if quality in {"watch_candidate", "watch", "watch_only"}:
        return "WATCH ONLY", "warn", "watch_candidate — not executable"
    label, cls = _label(str(row.get("execution_state") or ""))
    reason = str(row.get("reason") or "") or "—"
    return label, cls, reason


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

        # Keep the two final-execution columns directly beside Production Eligible.
        header = "<th>Production Eligible</th>"
        if header in rendered and "<th>Final XAU Execution</th>" not in rendered:
            rendered = rendered.replace(
                header,
                header + "<th>Final XAU Execution</th><th>Execution Reason</th>",
                1,
            )

        table_end = rendered.find("</table>")
        if table_end < 0:
            return rendered
        head = rendered[:table_end]
        tail = rendered[table_end:]

        def amend_row(match: re.Match[str]) -> str:
            inner = match.group(1)
            first = re.match(r"\s*<td>(\d+)</td>", inner)
            if not first:
                return match.group(0)
            rid = first.group(1)
            row = outcomes.get(rid)
            if row:
                label, cls, reason = _display_for(row, qualities.get(rid, ""))
                cells = (
                    f'<td class="{cls}"><strong>{html.escape(label)}</strong></td>'
                    f'<td>{html.escape(reason)}</td>'
                )
            else:
                # XAG and non-live historical rows do not have a live-XAU broker outcome.
                cells = '<td class="flat">—</td><td>—</td>'

            # Insert immediately after the seventh existing cell: ID, Candle, Asset,
            # Long, Short, Model Candidate, Production Eligible.
            ends = list(re.finditer(r"</td>", inner))
            if len(ends) < 7:
                return match.group(0)
            pos = ends[6].end()
            return "<tr>" + inner[:pos] + cells + inner[pos:] + "</tr>"

        head = re.sub(r"<tr>(.*?)</tr>", amend_row, head, flags=re.DOTALL)
        return head + tail

    core._metals_latest_30_signals_html = patched
    core.METALS_XAU_EXECUTION_VISIBILITY_INLINE_VERSION = VERSION
    core._METALS_XAU_EXECUTION_VISIBILITY_INLINE_INSTALLED = True

    probe = patched(8)
    watch_unit = _display_for(
        {"execution_state": "BLOCKED", "reason": "legacy blocker text"},
        "watch_candidate",
    )
    checks = {
        "final_execution_header_present": "Final XAU Execution" in probe,
        "execution_reason_header_present": "Execution Reason" in probe,
        "known_half_frequency_skip_visible": (
            "SKIPPED — 0.5× THROTTLE" in probe
            or "SKIPPED — 0.5&amp;times; THROTTLE" in probe
            or "post-32 broker-safe alternating overflow policy" in probe
        ),
        "watch_candidate_semantic_label": watch_unit == (
            "WATCH ONLY",
            "warn",
            "watch_candidate — not executable",
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
