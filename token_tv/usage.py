"""Small, explicit usage windows. Missing measurements stay missing."""
import calendar
import math
from datetime import datetime, timezone


def parse_time(value):
    """A timezone-aware UTC datetime from a Unix timestamp or an ISO string, or None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value <= 0:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    return None


def timestamp(value):
    parsed = parse_time(value)
    return int(parsed.timestamp()) if parsed else None


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
    weekly quota. The budget is returned whole (the plan's monthly credits), rounded to cents.
    An explicit `override` (the user's `ollama_allowance_usd`) wins while it is a finite number
    > 0. The plan name is matched case-insensitively; an unknown plan, or one with no credits
    (`free`), stays unknown (None) rather than a fake 0.
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


def credit_period_start(created_at, now=None):
    """UTC start of the current monthly credit period from the account's creation date.

    Ollama refills plan credits monthly on the day-of-month the account was created, so the
    period runs from the most recent occurrence of that day at or before `now`, at 00:00 UTC.
    Short months clamp the day (day 31 -> 28/29 in February). An unknown creation date (or an
    explicit unusable `now`) returns None, so no window is invented.
    """
    created = parse_time(created_at)
    current = parse_time(now) if now is not None else datetime.now(timezone.utc)
    if created is None or current is None:
        return None

    def occurrence(year, month):
        day = min(created.day, calendar.monthrange(year, month)[1])
        return datetime(year, month, day, tzinfo=timezone.utc)

    start = occurrence(current.year, current.month)
    if start > current:
        previous = (current.year - 1, 12) if current.month == 1 else (current.year, current.month - 1)
        start = occurrence(*previous)
    return start


def credit_period_end(start):
    """The UTC moment the credit period that began at `start` refills.

    Ollama refills on the same day-of-month one month later (clamped for short months), so the
    clock can count down to it.
    """
    month, year = start.month + 1, start.year
    if month > 12:
        month, year = 1, year + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, tzinfo=timezone.utc)


def normalize_ollama(data, allowance, created_at=None, now=None):
    """Read the JSON from `GET https://ollama.com/api/usage?range=30d` for one credit period.

    Ollama reports a bucket per day. Dollars (`usage_usd`) may be absent on a bucket, which
    counts as 0 while the bucket is inside the period. The period starts on the day-of-month
    the account was created (see `credit_period_start`); buckets before it are excluded, so the
    reading is the current monthly credit spend measured against the plan's monthly budget,
    never a rolling 7-day window. A missing/non-finite/unusable bucket, an unknown allowance,
    an unknown period, or no in-period dollars at all stays unknown instead of a fake 0%. The
    window's reset is the refill date (one month on from the period start).
    """
    if isinstance(allowance, bool) or not isinstance(allowance, (int, float)):
        return []
    if not math.isfinite(allowance) or allowance <= 0:
        return []
    start = credit_period_start(created_at, now)
    buckets = data.get("buckets") if isinstance(data, dict) else None
    if start is None or not isinstance(buckets, list):
        return []
    spent, known = 0.0, False
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        when = parse_time(bucket.get("from"))
        if when is None or when < start:
            continue
        value = bucket.get("usage_usd")
        if value is None:
            continue  # a bucket inside the period without dollars counts as 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return []  # malformed dollars: never guess a number
        spent += value
        known = True
    if not known:
        return []
    percent = round(min(100.0, spent / allowance * 100.0), 2)
    item = window("$" + str(int(spent)), percent, credit_period_end(start).isoformat(), 43200)
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
