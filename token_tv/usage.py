"""Small, explicit usage windows. Missing measurements stay missing."""
import math
from datetime import datetime


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if math.isfinite(value) and value > 0 else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp()) if parsed.tzinfo else None
        except ValueError:
            pass
    return None


def window(label, percent, reset=None, duration=None):
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    if not math.isfinite(percent) or percent < 0:
        return None
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        duration = None
    return {"label": label, "used_percent": float(percent),
            "resets_at": timestamp(reset), "duration_minutes": duration}


def normalize_claude(data):
    result = []
    for key, label, duration in (("five_hour", "5H", 300), ("seven_day", "WEEK", 10080)):
        value = data.get(key) or {}
        item = window(label, value.get("utilization"), value.get("resets_at"), duration)
        if item:
            result.append(item)
    return result


# Ollama Cloud plan -> included usage credits in USD per MONTH (https://ollama.com/pricing).
OLLAMA_CREDITS_USD = {"free": 0, "pro": 60, "max": 300, "team": 1000}


def ollama_allowance(plan, override=None):
    """The Ollama Cloud MONTHLY credit budget in USD, from the plan or an override.

    Ollama's plan credits are a monthly budget that can be spent at any pace; they are not a
    weekly quota. Its usage API reports dollars only for windows up to 7 days (`?range=7d`):
    the `30d` totals expose request counts but no `usage_usd`, so a 30-day spend cannot be
    read. The budget is therefore returned whole (the plan's monthly credits), rounded to
    cents, and the reading measures the last 7 days of spend against it. An explicit `override`
    (the user's `ollama_allowance_usd`) wins while it is a finite number > 0. The plan name is
    matched case-insensitively; an unknown plan, or one with no credits (`free`), stays unknown
    (None) rather than a fake 0.
    """
    if (not isinstance(override, bool) and isinstance(override, (int, float))
            and math.isfinite(override) and override > 0):
        return float(override)
    if not isinstance(plan, str):
        return None
    credits = OLLAMA_CREDITS_USD.get(plan.strip().lower(), 0)
    if credits <= 0:
        return None
    return round(float(credits), 2)


def normalize_ollama(data, allowance):
    """Read the JSON from `GET https://ollama.com/api/usage?range=7d`.

    Ollama Cloud reports only the spend of a rolling window; the plan-to-budget mapping lives
    in `ollama_allowance`. The budget is MONTHLY and spend is not time-boxed, so the reading is
    the last 7 days of `totals.usage_usd` measured against the full monthly credit budget: it
    is a credit/spend budget, never a weekly quota. A missing or non-positive allowance, or a
    missing/non-finite spend, stays unknown: the reading is never invented as 0%.
    """
    if isinstance(allowance, bool) or not isinstance(allowance, (int, float)):
        return []
    if not math.isfinite(allowance) or allowance <= 0:
        return []
    totals = data.get("totals") if isinstance(data, dict) else None
    spent = totals.get("usage_usd") if isinstance(totals, dict) else None
    if isinstance(spent, bool) or not isinstance(spent, (int, float)) or not math.isfinite(spent):
        return []
    percent = round(min(100.0, spent / allowance * 100.0), 2)
    item = window("BUDGET", percent, None, 43200)
    return [item] if item else []


def normalize_antigravity(data):
    """Read the JSON printed by `agy -p "/usage" --output-format json`.

    The CLI reports the quota REMAINING in each bucket; TokenTV displays what is
    USED, so every reading is converted with used = (1 - remaining_fraction) * 100.
    Only the Gemini group is shown (the clock shows one row per provider); the
    "Claude and GPT models" group is ignored. A bucket without a usable number
    is skipped, never turned into a fake 0%.
    """
    payload = (data.get("command") or {}).get("data")
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return []
    group = next((item for item in groups if isinstance(item, dict)
                  and "gemini" in str(item.get("name", "")).lower()), None)
    if group is None:
        return []
    result = []
    for bucket in group.get("buckets") or []:
        if not isinstance(bucket, dict):
            continue
        fraction = bucket.get("remaining_fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            continue
        if not math.isfinite(fraction):
            continue
        percent = max(0.0, min(100.0, (1.0 - fraction) * 100.0))
        duration = {"5h": 300, "weekly": 10080}.get(bucket.get("window"))
        label = {300: "5H", 10080: "WEEK"}.get(duration, "WINDOW")
        item = window(label, percent, bucket.get("reset_time"), duration)
        if item:
            result.append(item)
    return result
