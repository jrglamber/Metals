"""Research-only Metals AI Observer resilience patch.

Installed by the active production presentation entrypoint. Does not change
broker orders, trade rules, sizing, stops, or the deterministic signal engine.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

VERSION = "metals_ai_observer_json_retry_v1_2026_11_18"
_RETRYABLE_PARSE_ERRORS = ("JSONDecodeError:", "structured regime decision missing required fields")


def _is_parse_failure(api):
    if not isinstance(api, dict) or api.get("ok"):
        return False
    error = str(api.get("error") or "")
    return any(error.startswith(prefix) for prefix in _RETRYABLE_PARSE_ERRORS)


def install(core):
    """Repair AI-only response parsing and retry recently failed snapshots.

    Guarded, idempotent and fail-open for live deterministic trading.
    Snapshot reprocessing never repeats or replays the trading signal.
    """
    if getattr(core, "_pep_ai_json_retry_installed", False):
        return {"installed": True, "version": VERSION, "already_installed": True}
    if not callable(getattr(core, "_aiobs_openai_call", None)):
        return {"installed": False, "version": VERSION, "reason": "observer_call_missing"}

    previous = core._aiobs_openai_call

    def resilient_ai_call(model_input, raw_signal_id):
        api = previous(model_input, raw_signal_id)
        if _is_parse_failure(api):
            result = dict(api)
            # Retry the same immutable AI-only snapshot; never re-run the
            # deterministic signal or trigger a new broker instruction.
            result["retryable"] = True
            result["retry_after_seconds"] = 45
            return result
        return api

    # A 700-token cap can truncate strict JSON, particularly when the model
    # spends output tokens on reasoning. Respect explicit operator overrides.
    original_tokens = getattr(core, "AI_SHADOW_MAX_OUTPUT_TOKENS", 700)
    if not os.getenv("AI_SHADOW_MAX_OUTPUT_TOKENS"):
        core.AI_SHADOW_MAX_OUTPUT_TOKENS = max(int(original_tokens), 1800)

    core._aiobs_openai_call = resilient_ai_call
    core._pep_ai_json_retry_installed = True

    recovered = 0
    recovery_error = None
    if getattr(core, "AI_SHADOW_ENABLED", False) and getattr(core, "AI_SHADOW_OPENAI_API_KEY", ""):
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            with core.get_conn() as conn:
                rows = conn.execute(
                    """SELECT raw_signal_id FROM ai_regime_observer
                       WHERE status='ERROR' AND api_eligible=1
                         AND created_at_utc>=?
                         AND (error LIKE 'JSONDecodeError:%'
                              OR error LIKE 'ValueError: structured regime decision missing required fields%')
                       ORDER BY raw_signal_id DESC LIMIT 10""",
                    (cutoff,),
                ).fetchall()
            for row in rows:
                queued = core.enqueue_ai_regime_observer(int(row["raw_signal_id"]))
                if queued.get("queued"):
                    recovered += 1
        except Exception as exc:
            # Research recovery must NEVER prevent the trading app starting.
            recovery_error = f"{type(exc).__name__}: {str(exc)[:120]}"

    return {
        "installed": True,
        "version": VERSION,
        "research_only": True,
        "execution_authority": False,
        "token_budget": getattr(core, "AI_SHADOW_MAX_OUTPUT_TOKENS", None),
        "requeued_recent_json_failures": recovered,
        "recovery_error": recovery_error,
    }
