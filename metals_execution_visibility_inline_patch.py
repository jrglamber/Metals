"""Inline final XAU execution state in Latest 30 Metals Signals.

Presentation-only overlay.  The existing candidate columns describe pre-execution
eligibility.  This patch adds the actual live XAU outcome beside that eligibility
state so an approved candidate can visibly read OPENED, INTENTIONAL SKIP, or the
real execution blocker on the same row.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict

import metals_execution_visibility_patch as visibility

VERSION = "metals_xau_execution_visibility_inline_v1_2026_10_14"


def _label(state: str) -> tuple[str, str]:
    s = str(state or "").strip().upper()
    if s == "OPENED":
        return "OPENED", "pos"
    if s == "INTENTIONAL_HALF_FREQUENCY_SKIP":
        return "SKIPPED — 0.5× THROTTLE", "warn"
    if s.startswith("BLOCKED") or s in {"NOT_OPENED", "NO_EXECUTION_RECORD"}:
        return "BLOCKED", "neg"
    return s or "—", "flat"


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
                label, cls = _label(str(row.get("execution_state") or ""))
                reason = str(row.get("reason") or "")
                cells = (
                    f'<td class="{cls}"><strong>{html.escape(label)}</strong></td>'
                    f'<td>{html.escape(reason or "—")}</td>'
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
    checks = {
        "final_execution_header_present": "Final XAU Execution" in probe,
        "execution_reason_header_present": "Execution Reason" in probe,
        "known_half_frequency_skip_visible": (
            "SKIPPED — 0.5× THROTTLE" in probe
            or "SKIPPED — 0.5&amp;times; THROTTLE" in probe
            or "post-32 broker-safe alternating overflow policy" in probe
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
