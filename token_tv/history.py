"""Append-only, size-bounded JSONL history buffer for TokenTV snapshots.

Supports both stock watchlist polls and live provider usage snapshots.
Prunes oldest entries when exceeding max entry count or file size.
Maintains the 'unknown is never 0' rule (missing quotes remain None).
"""

import json
import os
import tempfile
from pathlib import Path

DEFAULT_MAX_ENTRIES = 1000
DEFAULT_MAX_BYTES = 1024 * 1024  # 1 MB


def append_history(path, entry, max_entries=DEFAULT_MAX_ENTRIES, max_bytes=DEFAULT_MAX_BYTES):
    """Append a snapshot entry to the JSONL history buffer, pruning oldest entries if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    encoded_line = line.encode("utf-8")

    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

    # Check if pruning is required
    try:
        size = path.stat().st_size
    except OSError:
        return

    # If file size exceeds max_bytes or we want to enforce line bounds:
    # We prune when file size exceeds max_bytes, or periodically / when lines exceed max_entries.
    if size > max_bytes:
        _prune(path, max_entries=max_entries, max_bytes=max_bytes)
    else:
        # Check line count if small or periodically
        # Cheap line count estimation or exact read
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_entries:
            _prune_lines(path, lines, max_entries=max_entries, max_bytes=max_bytes)


def _prune(path, max_entries=DEFAULT_MAX_ENTRIES, max_bytes=DEFAULT_MAX_BYTES):
    """Prune file on disk to fit within max_entries and max_bytes."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    _prune_lines(path, lines, max_entries=max_entries, max_bytes=max_bytes)


def _prune_lines(path, lines, max_entries=DEFAULT_MAX_ENTRIES, max_bytes=DEFAULT_MAX_BYTES):
    """Keep the most recent entries that satisfy both max_entries and max_bytes."""
    kept = lines[-max_entries:] if len(lines) > max_entries else lines
    # Further prune by byte size if needed
    total_bytes = sum(len(line.encode("utf-8")) for line in kept)
    while kept and total_bytes > max_bytes:
        removed = kept.pop(0)
        total_bytes -= len(removed.encode("utf-8"))

    # Atomic write back to avoid corrupting on crash
    dir_name = path.parent
    fd, tmp_file = tempfile.mkstemp(dir=dir_name, prefix="hist_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(kept)
    os.replace(tmp_file, path)


def read_history(path, since=None, until=None):
    """Read snapshot entries from a JSONL file, optionally filtered by timestamp."""
    path = Path(path)
    if not path.exists():
        return []

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = entry.get("updated_at")
            if ts is not None:
                if since is not None and ts < since:
                    continue
                if until is not None and ts > until:
                    continue
            entries.append(entry)
    return entries


def query_symbol_changes(history, symbol, since=None, until=None):
    """Query whether a stock symbol's price changed across historical readings.

    Returns:
        dict: {
            'symbol': symbol,
            'changed': bool,
            'distinct_prices': list of distinct non-None prices in order of appearance,
            'initial_price': first observed non-None price,
            'latest_price': most recent observed non-None price,
            'readings': list of dicts with timestamp, price, change, percent, old
        }
    """
    symbol_upper = symbol.upper()
    readings = []

    for entry in history:
        ts = entry.get("updated_at")
        if ts is not None:
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue

        symbols = entry.get("symbols") or []
        for row in symbols:
            if row.get("symbol", "").upper() == symbol_upper:
                # Crucial: Never coerce None to 0 or 0.0
                price = row.get("price")
                readings.append({
                    "updated_at": ts,
                    "price": price,
                    "change": row.get("change"),
                    "percent": row.get("percent"),
                    "old": row.get("old", False)
                })

    distinct_prices = []
    for r in readings:
        p = r["price"]
        if p is not None and p not in distinct_prices:
            distinct_prices.append(p)

    valid_prices = [r["price"] for r in readings if r["price"] is not None]
    initial_price = valid_prices[0] if valid_prices else None
    latest_price = valid_prices[-1] if valid_prices else None

    return {
        "symbol": symbol_upper,
        "changed": len(distinct_prices) > 1,
        "distinct_prices": distinct_prices,
        "initial_price": initial_price,
        "latest_price": latest_price,
        "readings": readings
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Query TokenTV history ring buffer")
    parser.add_argument("--history", required=True, help="Path to history.jsonl")
    parser.add_argument("--symbol", help="Stock symbol to query (e.g. NVDA)")
    parser.add_argument("--since", type=int, help="Start timestamp")
    parser.add_argument("--until", type=int, help="End timestamp")
    args = parser.parse_args()

    history = read_history(args.history, since=args.since, until=args.until)
    if args.symbol:
        res = query_symbol_changes(history, args.symbol, since=args.since, until=args.until)
        print(f"Symbol: {res['symbol']}")
        print(f"Changed: {res['changed']}")
        print(f"Distinct Prices: {res['distinct_prices']}")
        print(f"Initial: {res['initial_price']} -> Latest: {res['latest_price']}")
        print(f"Readings Count: {len(res['readings'])}")
    else:
        print(f"Total history entries: {len(history)}")


if __name__ == "__main__":
    main()
