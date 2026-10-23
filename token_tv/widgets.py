"""Normalized payload for mobile widgets (Android Jetpack Glance and iOS WidgetKit).

Exposes full session and weekly usage windows, progress percentages, and reset
countdowns for Claude, ChatGPT, Antigravity, and Ollama Cloud.
"""
import time
from token_tv.display import time_left, quota_period

PROVIDER_NAMES = {
    "claude": "Claude",
    "chatgpt": "ChatGPT",
    "antigravity": "Antigravity",
    "ollama": "Ollama Cloud"
}


def _extract_windows(row):
    """Separate session window (5H/3H/1D) and weekly/budget window (WEEK/$XX)."""
    windows = row.get("windows") or []
    session_win = None
    weekly_win = None

    for w in windows:
        label = (w.get("label") or "").upper()
        if label in ("5H", "3H", "1D", "HOUR") or (w.get("duration_minutes") or 0) <= 1440:
            if session_win is None:
                session_win = w
        elif label in ("WEEK", "7D", "MONTH") or label.startswith("$") or (w.get("duration_minutes") or 0) > 1440:
            if weekly_win is None:
                weekly_win = w

    # Fallback if unassigned
    if not session_win and windows:
        session_win = windows[0]
    if not weekly_win and len(windows) > 1:
        weekly_win = windows[1]
    elif not weekly_win and windows and windows[0] != session_win:
        weekly_win = windows[0]

    return session_win, weekly_win


def _format_window_payload(win, now=None):
    if not win:
        return None
    used = win.get("used_percent")
    resets_at = win.get("resets_at")
    resets_in = win.get("resets_in")
    if resets_in is None and resets_at:
        now_ts = now if now is not None else time.time()
        mins = max(0, int((resets_at - now_ts + 59) // 60))
        if mins >= 1440:
            resets_in = f"{mins // 1440}d {mins % 1440 // 60}h"
        else:
            resets_in = f"{mins // 60}h {mins % 60}m"

    return {
        "label": quota_period(win) if win.get("label") else "",
        "used_percent": round(used, 1) if used is not None else None,
        "resets_in": resets_in or "--",
        "resets_at": round(resets_at) if resets_at else None,
        "duration_minutes": win.get("duration_minutes")
    }


def widgets_payload(snapshot, now=None):
    """Produce structured dictionary for Glance and WidgetKit consumption."""
    now_ts = now if now is not None else time.time()
    if snapshot is None:
        snapshot = {}
    accounts = snapshot.get("accounts", {})
    providers_list = []
    summary_parts = []

    # Map accounts
    for key, row in accounts.items():
        provider = row.get("provider", "unknown")
        alias = row.get("alias") or provider.upper()
        status = row.get("status", "ok")
        s_win, w_win = _extract_windows(row)

        session = _format_window_payload(s_win, now_ts)
        weekly = _format_window_payload(w_win, now_ts)

        item = {
            "key": key,
            "provider": provider,
            "name": PROVIDER_NAMES.get(provider, provider.capitalize()),
            "alias": alias,
            "status": status,
            "session": session,
            "weekly": weekly
        }
        providers_list.append(item)

        # Build short summary
        s_text = f"{int(session['used_percent'])}% {session['label']}" if session and session['used_percent'] is not None else "--"
        w_text = f"{int(weekly['used_percent'])}% {weekly['label']}" if weekly and weekly['used_percent'] is not None else "--"
        summary_parts.append(f"{alias} S:{s_text} W:{w_text}")

    return {
        "updated_at": snapshot.get("updated_at") or int(now_ts),
        "summary": " · ".join(summary_parts) or "NO ACCOUNTS",
        "providers": providers_list
    }
