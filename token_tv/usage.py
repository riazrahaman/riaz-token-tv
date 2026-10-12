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


def normalize_codex(data):
    result = []
    limits = data.get("rateLimits") or {}
    for key in ("primary", "secondary"):
        value = limits.get(key) or {}
        duration = value.get("windowDurationMins")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            duration = None
        label = {300: "5H", 10080: "WEEK"}.get(duration, "WINDOW")
        item = window(label, value.get("usedPercent"), value.get("resetsAt"), duration)
        if item:
            result.append(item)
    return result


def normalize_codexbar(entry):
    """One provider entry of CodexBar's usage JSON (`codexbar usage --format json`, `serve` GET /usage).
    Only the primary and secondary slots: tertiary and extra windows are model-scoped and would
    overwrite the weekly reading if they shared its label."""
    result = []
    usage = entry.get("usage")
    for key in ("primary", "secondary"):
        value = usage.get(key) if isinstance(usage, dict) else None
        if not isinstance(value, dict):
            continue
        duration = value.get("windowMinutes")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            duration = None
        label = {300: "5H", 10080: "WEEK"}.get(duration, "WINDOW")
        item = window(label, value.get("usedPercent"), value.get("resetsAt"), duration)
        if item:
            result.append(item)
    return result


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
