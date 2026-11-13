"""Estimate Judge charges from frozen prices and reserve before dispatch."""
from sqlalchemy import select

from agenteval.db import GradingBatch, JudgeCall

COST_KEY = "_agenteval_cost"
MAX_OUTPUT_TOKENS = 768


def policy(settings):
    if not (settings.judge_endpoint and settings.judge_model) or settings.judge_input_micro_usd_per_million < 0:
        return None
    return {"input_micro_usd_per_million": settings.judge_input_micro_usd_per_million,
            "output_micro_usd_per_million": settings.judge_output_micro_usd_per_million,
            "reserve_input_tokens": settings.judge_reserve_input_tokens,
            "reserve_output_tokens": MAX_OUTPUT_TOKENS,
            "max_micro_usd": settings.judge_max_micro_usd or None}


def estimate(pricing, input_tokens, output_tokens):
    total = input_tokens * pricing["input_micro_usd_per_million"] + output_tokens * pricing["output_micro_usd_per_million"]
    return (total + 999_999) // 1_000_000


def charge(pricing, reserved, usage):
    tokens = [usage.get(key) for key in ("prompt_tokens", "completion_tokens")] if isinstance(usage, dict) else []
    metered = len(tokens) == 2 and all(type(n) is int and 0 <= n <= 1_000_000_000 for n in tokens)
    return {"reserved_micro_usd": reserved, "estimated_micro_usd": estimate(pricing, *tokens) if metered else None}


def ledger(session, run_id, pricing):
    estimated, reserved, unmetered = 0, 0, 0
    for usage in session.scalars(select(JudgeCall.usage).join(GradingBatch).where(GradingBatch.run_id == run_id)):
        entry = (usage or {}).get(COST_KEY) if pricing and isinstance(usage, dict) else None
        if entry and entry["estimated_micro_usd"] is not None:
            estimated += entry["estimated_micro_usd"]
        else:
            unmetered += 1
            if entry:
                reserved += entry["reserved_micro_usd"]
    maximum = pricing["max_micro_usd"] if pricing else None
    return {"pricing": pricing, "unmetered_calls": unmetered,
            "estimated_micro_usd": estimated if pricing else None, "reserved_micro_usd": reserved if pricing else None,
            "remaining_micro_usd": max(0, maximum - estimated - reserved) if maximum is not None else None}
