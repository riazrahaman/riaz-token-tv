import json
import pytest
from pathlib import Path
from token_tv.history import (
    append_history,
    read_history,
    query_symbol_changes,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_BYTES
)


def test_append_and_read_history(tmp_path):
    history_file = tmp_path / "history.jsonl"
    entry1 = {"updated_at": 1000, "symbols": [{"symbol": "NVDA", "price": 120.5}]}
    entry2 = {"updated_at": 1060, "symbols": [{"symbol": "NVDA", "price": 121.0}]}

    append_history(history_file, entry1)
    append_history(history_file, entry2)

    records = read_history(history_file)
    assert len(records) == 2
    assert records[0]["updated_at"] == 1000
    assert records[1]["updated_at"] == 1060
    assert records[1]["symbols"][0]["price"] == 121.0


def test_pruning_by_max_entries(tmp_path):
    history_file = tmp_path / "history.jsonl"
    for i in range(10):
        append_history(history_file, {"updated_at": 1000 + i, "val": i}, max_entries=4)

    records = read_history(history_file)
    assert len(records) == 4
    assert [r["val"] for r in records] == [6, 7, 8, 9]


def test_pruning_by_max_bytes(tmp_path):
    history_file = tmp_path / "history.jsonl"
    # Write entries with large payloads
    entry = {"updated_at": 1000, "data": "x" * 200}
    for i in range(10):
        entry["updated_at"] = 1000 + i
        append_history(history_file, entry, max_entries=100, max_bytes=500)

    records = read_history(history_file)
    assert len(records) < 10
    assert history_file.stat().st_size <= 500


def test_unknown_is_not_zero_rule(tmp_path):
    history_file = tmp_path / "history.jsonl"
    # Symbol with None/missing price, marked old or failed
    entry1 = {
        "updated_at": 1000,
        "symbols": [
            {"symbol": "TSLA", "price": None, "change": None, "percent": None, "old": True}
        ]
    }
    entry2 = {
        "updated_at": 1060,
        "symbols": [
            {"symbol": "TSLA", "price": 250.0, "change": 2.0, "percent": 0.8, "old": False}
        ]
    }
    append_history(history_file, entry1)
    append_history(history_file, entry2)

    records = read_history(history_file)
    tsla_0 = next(s for s in records[0]["symbols"] if s["symbol"] == "TSLA")
    assert tsla_0["price"] is None
    assert tsla_0["price"] != 0
    assert tsla_0["price"] != 0.0


def test_query_symbol_changes(tmp_path):
    history_file = tmp_path / "history.jsonl"
    records = [
        {"updated_at": 100, "symbols": [{"symbol": "NVDA", "price": 100.0}, {"symbol": "AAPL", "price": 150.0}]},
        {"updated_at": 200, "symbols": [{"symbol": "NVDA", "price": 100.0}, {"symbol": "AAPL", "price": 151.0}]},
        {"updated_at": 300, "symbols": [{"symbol": "NVDA", "price": 105.0}, {"symbol": "AAPL", "price": 151.0}]},
        {"updated_at": 400, "symbols": [{"symbol": "NVDA", "price": None, "old": True}, {"symbol": "AAPL", "price": 151.0}]},
    ]
    for r in records:
        append_history(history_file, r)

    history = read_history(history_file)

    # AAPL changed between 100 and 200
    res_aapl_early = query_symbol_changes(history, "AAPL", since=100, until=200)
    assert res_aapl_early["changed"] is True
    assert res_aapl_early["distinct_prices"] == [150.0, 151.0]

    # AAPL did NOT change between 200 and 400
    res_aapl_late = query_symbol_changes(history, "AAPL", since=200, until=400)
    assert res_aapl_late["changed"] is False
    assert res_aapl_late["distinct_prices"] == [151.0]

    # NVDA changed between 100 and 300
    res_nvda = query_symbol_changes(history, "NVDA", since=100, until=300)
    assert res_nvda["changed"] is True
    assert res_nvda["initial_price"] == 100.0
    assert res_nvda["latest_price"] == 105.0

    # NVDA when query includes reading with None price: does not convert None to 0
    res_nvda_all = query_symbol_changes(history, "NVDA", since=100, until=400)
    assert None in [r["price"] for r in res_nvda_all["readings"]]
    assert 0.0 not in res_nvda_all["distinct_prices"]


def test_query_filter_since_until():
    history = [
        {"updated_at": 100, "symbols": [{"symbol": "MSFT", "price": 300.0}]},
        {"updated_at": 200, "symbols": [{"symbol": "MSFT", "price": 305.0}]},
        {"updated_at": 300, "symbols": [{"symbol": "MSFT", "price": 310.0}]},
    ]
    res = query_symbol_changes(history, "MSFT", since=150, until=250)
    assert len(res["readings"]) == 1
    assert res["readings"][0]["price"] == 305.0
    assert res["changed"] is False


def test_live_handler_history_endpoint(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from token_tv.live import handler
    from token_tv.state import UsageStore

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    append_history(state_dir / "history.jsonl", {"updated_at": 12345, "test": "ok"})

    store = UsageStore([])
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, state_dir=state_dir))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/history"
        with urlopen(url) as resp:
            data = json.load(resp)
        assert len(data) == 1
        assert data[0]["updated_at"] == 12345
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cli_history_command(tmp_path, capsys):
    from token_tv.cli import main
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"poll_seconds": 300}))
    state_stocks = tmp_path / "state-stocks"
    state_stocks.mkdir()
    append_history(state_stocks / "history.jsonl", {
        "updated_at": 1000,
        "symbols": [{"symbol": "NVDA", "price": 100.0}]
    })
    append_history(state_stocks / "history.jsonl", {
        "updated_at": 1100,
        "symbols": [{"symbol": "NVDA", "price": 105.0}]
    })

    code = main(["history", "--config", str(cfg_file), "--symbol", "NVDA"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Symbol: NVDA" in captured.out
    assert "Changed: True" in captured.out
    assert "Distinct Prices: [100.0, 105.0]" in captured.out

