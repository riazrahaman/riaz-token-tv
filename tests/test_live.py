import copy
import io
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from token_tv.usage import (normalize_claude, normalize_ollama, normalize_antigravity,
                            ollama_allowance, credit_period_start, timestamp)
from token_tv.sources import (fetch_account, load_config, antigravity_identity, ollama_identity,
                              ollama_plan, imported_row, scoped_env, SourceError)
from token_tv.state import UsageStore
from token_tv.display import render_page, pages, overview_rows, primary_window


class LiveTests(unittest.TestCase):
    def test_provider_cli_environment_does_not_inherit_other_authentication(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "DO-NOT-INHERIT", "XAI_API_KEY": "DO-NOT-INHERIT", "CLAUDE_CODE_OAUTH_TOKEN": "DO-NOT-INHERIT"}):
            for provider, variable in (("claude", "CLAUDE_CONFIG_DIR"), ("antigravity", "ANTIGRAVITY_EXECUTABLE_DATA_DIR")):
                env = scoped_env(provider, "/explicit/account/home")
                self.assertEqual(env[variable], "/explicit/account/home")
                self.assertFalse(any(key in env for key in ("OPENAI_API_KEY", "XAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")))

    def test_ollama_reading_sums_only_the_current_credit_period(self):
        # The account was created on the 26th, so the period began 2026-09-26. The 09-25 bucket
        # is before it and must not count. A bucket without usage_usd inside the period is 0.
        data = {"range": "30d", "buckets": [
            {"from": "2026-09-25T00:00:00Z", "request_count": 500, "usage_usd": 10.0},
            {"from": "2026-09-26T00:00:00Z", "request_count": 0},
            {"from": "2026-09-27T00:00:00Z", "request_count": 7, "usage_usd": 1.5},
            {"from": "2026-10-01T00:00:00Z", "request_count": 9, "usage_usd": 2.5},
        ]}
        created, now = "2026-04-26T12:49:20.350091Z", "2026-10-12T12:00:00Z"
        self.assertEqual(normalize_ollama(data, 50.0, created, now), [
            {"label": "$4", "used_percent": 8.0, "resets_at": 1792972800, "duration_minutes": 43200}])
        # Spend above the allowance clamps to 100.0, and the label keeps the dollar value.
        over = {"buckets": [{"from": "2026-09-26T00:00:00Z", "usage_usd": 999.0}]}
        self.assertEqual(normalize_ollama(over, 50.0, created, now),
                         [{"label": "$999", "used_percent": 100.0, "resets_at": 1792972800, "duration_minutes": 43200}])
        # $68.84 in a $300 plan is 22.95%, labelled with the whole dollars only.
        spent = {"buckets": [{"from": "2026-09-26T00:00:00Z", "usage_usd": 68.84}]}
        self.assertEqual(normalize_ollama(spent, 300.0, created, now)[0],
                         {"label": "$68", "used_percent": 22.95, "resets_at": 1792972800, "duration_minutes": 43200})

    def test_credit_period_anchor_comes_from_the_creation_day(self):
        case = lambda created, now: credit_period_start(created, now)
        # Day 26: the most recent 26th at or before now (this month, or last when it is later).
        self.assertEqual(case("2026-04-26T12:49:20.350091Z", "2026-10-27T09:00:00Z"), datetime(2026, 10, 26, tzinfo=timezone.utc))
        self.assertEqual(case("2026-04-26T12:49:20Z", "2026-10-12T00:00:00Z"), datetime(2026, 9, 26, tzinfo=timezone.utc))
        # A day 31 anchor clamps into short months instead of overflowing.
        self.assertEqual(case("2026-01-31T00:00:00Z", "2026-03-05T00:00:00Z"), datetime(2026, 2, 28, tzinfo=timezone.utc))
        self.assertEqual(case("2026-01-31T00:00:00Z", "2026-02-15T00:00:00Z"), datetime(2026, 1, 31, tzinfo=timezone.utc))
        # Unknown or unusable input stays unknown rather than inventing a period.
        for created in (None, "", "not-a-date", 0):
            self.assertIsNone(case(created, "2026-10-12T00:00:00Z"), created)

    def test_missing_ollama_allowance_or_spend_is_unknown(self):
        created, now = "2026-04-26T12:49:20Z", "2026-10-12T12:00:00Z"
        data = {"buckets": [{"from": "2026-09-26T00:00:00Z", "usage_usd": 25.0}]}
        self.assertEqual(normalize_ollama(data, None, created, now), [])
        self.assertEqual(normalize_ollama(data, 0, created, now), [])
        self.assertEqual(normalize_ollama(data, -5, created, now), [])
        self.assertEqual(normalize_ollama(data, True, created, now), [])
        # No period (unknown creation date) or no buckets: unknown, never a fake 0%.
        self.assertEqual(normalize_ollama(data, 50.0, None, now), [])
        self.assertEqual(normalize_ollama({"totals": {"usage_usd": 25.0}}, 50.0, created, now), [])
        self.assertEqual(normalize_ollama({}, 50.0, created, now), [])
        # A period with no dollars at all is unknown; a malformed dollar value is not a reading.
        self.assertEqual(normalize_ollama({"buckets": [{"from": "2026-09-26T00:00:00Z"}]}, 50.0, created, now), [])
        for bad in (True, float("inf"), float("nan"), "25"):
            data = {"buckets": [{"from": "2026-09-26T00:00:00Z", "usage_usd": bad}]}
            self.assertEqual(normalize_ollama(data, 50.0, created, now), [], bad)
        # Buckets before the period do not make a reading either.
        self.assertEqual(normalize_ollama({"buckets": [{"from": "2026-09-01T00:00:00Z", "usage_usd": 25.0}]},
                                          50.0, created, now), [])

    def test_missing_antigravity_quota_is_unknown(self):
        self.assertEqual(normalize_antigravity({"command": {"data": {"groups": [{"name": "Claude and GPT models", "buckets": [{"window": "weekly", "remaining_fraction": 1.0}]}]}}}), [])
        self.assertEqual(normalize_antigravity({"command": {"data": {"groups": [{"name": "Gemini Models", "buckets": [{"window": "weekly"}, {"window": "5h", "remaining_fraction": True}]}]}}}), [])
        self.assertEqual(normalize_antigravity({}), [])
        self.assertEqual(normalize_claude({"five_hour": {"utilization": None}, "seven_day": {"utilization": 24, "resets_at": None}})[0]["label"], "WEEK")

    def test_antigravity_reports_used_share_of_gemini_limits(self):
        data = {"command": {"data": {"groups": [
            {"name": "Gemini Models", "buckets": [
                {"window": "weekly", "remaining_fraction": 0.25, "reset_time": "2026-10-14T17:46:08Z"},
                {"window": "5h", "remaining_fraction": 0.5, "reset_time": "2026-10-12T19:57:47Z"}]},
            {"name": "Claude and GPT models", "buckets": [{"window": "weekly", "remaining_fraction": 0.0}]}]}}}
        windows = normalize_antigravity(data)
        self.assertEqual([w["label"] for w in windows], ["WEEK", "5H"])
        self.assertEqual([w["used_percent"] for w in windows], [75.0, 50.0])
        self.assertEqual([w["duration_minutes"] for w in windows], [10080, 300])
        self.assertEqual(windows[0]["resets_at"], timestamp("2026-10-14T17:46:08Z"))

    def test_credentials_and_identity_never_enter_snapshot(self):
        account = {"key": "a", "alias": "CLAUDE A", "provider": "claude", "source_home": "/unused", "email": "expected@example.com"}
        with patch("token_tv.sources.claude_payload", return_value=({"account": {"email": "wrong@example.com"}}, {"five_hour": {"utilization": 8}, "access_token": "DO-NOT-EXPORT"})):
            row = fetch_account(account)
        self.assertEqual(row["status"], "identity_mismatch")
        self.assertEqual(row["windows"], [])
        self.assertNotIn("DO-NOT-EXPORT", json.dumps(row))
        self.assertNotIn("wrong@example.com", json.dumps(row))

    def test_configuration_rejects_secret_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"accounts": [{"key": "a", "alias": "A", "provider": "claude", "password": "DO-NOT-USE"}]}))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_ollama_allowance_is_credential_free_metadata_for_ollama_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            # Allowed for ollama, and an ollama account needs no email (it reports none).
            path.write_text(json.dumps({"accounts": [{"key": "o", "alias": "OLLAMA A", "provider": "ollama",
                                                      "ollama_allowance_usd": 25.0}]}))
            self.assertEqual(load_config(path)["accounts"][0]["ollama_allowance_usd"], 25.0)
            for account in ({"key": "a", "alias": "A", "provider": "claude", "email": "a@example.com", "ollama_allowance_usd": 25.0},
                            {"key": "ag", "alias": "AG", "provider": "antigravity", "ollama_allowance_usd": 25.0},
                            {"key": "o", "alias": "OLLAMA A", "provider": "ollama", "ollama_allowance_usd": 0},
                            {"key": "o", "alias": "OLLAMA A", "provider": "ollama", "ollama_allowance_usd": -5},
                            {"key": "o", "alias": "OLLAMA A", "provider": "ollama", "ollama_allowance_usd": True},
                            {"key": "o", "alias": "OLLAMA A", "provider": "ollama", "ollama_allowance_usd": "25"}):
                path.write_text(json.dumps({"accounts": [account]}))
                with self.assertRaises(ValueError, msg=account):
                    load_config(path)

    def test_antigravity_source_is_unverified_and_reports_no_email(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(json.dumps({"issuer::client": {"email": "owner@example.com", "key": "NEVER-EXPORT", "refresh_token": "NEVER-EXPORT"}}))
            self.assertIsNone(antigravity_identity({"source_home": directory}))
            account = {"key": "ag", "alias": "ANTIGRAVITY A", "provider": "antigravity", "source_home": directory, "email": "label-only@example.com"}
            payload = {"status": "SUCCESS", "command": {"name": "usage", "data": {"groups": [{"name": "Gemini Models", "buckets": [
                {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.25, "reset_time": "2026-10-14T17:46:08Z"}]}]}}}
            with patch("token_tv.sources.antigravity_payload", return_value=payload):
                row = fetch_account(account)
            self.assertEqual(row["status"], "ok")
            self.assertFalse(row["identity_verified"])
            self.assertEqual(row["windows"], [{"label": "WEEK", "used_percent": 75.0, "resets_at": timestamp("2026-10-14T17:46:08Z"), "duration_minutes": 10080}])
            self.assertNotIn("NEVER-EXPORT", json.dumps(row))
            self.assertNotIn("owner@example.com", json.dumps(row))

    def test_ollama_allowance_is_the_monthly_plan_credit_budget(self):
        # Plan credits are a monthly budget that can be spent at any pace, not a weekly quota.
        self.assertEqual(ollama_allowance("max"), 300.0)
        self.assertEqual(ollama_allowance("pro"), 60.0)
        self.assertEqual(ollama_allowance("team"), 1000.0)
        self.assertEqual(ollama_allowance(" MAX "), 300.0)  # case-insensitive, stripped
        # free / unknown / missing plans stay unknown, never a fake allowance of 0.
        for plan in ("free", "something-else", "", None, 42):
            self.assertIsNone(ollama_allowance(plan), plan)
        # An explicit override wins while it is a finite number > 0.
        self.assertEqual(ollama_allowance("max", 25.0), 25.0)
        self.assertEqual(ollama_allowance("free", 25.0), 25.0)
        # A missing or invalid override falls back to the plan.
        for bad in (None, 0, -5, True, float("inf"), float("nan"), "25"):
            self.assertEqual(ollama_allowance("max", bad), 300.0, bad)

    def test_ollama_plan_posts_to_api_me_and_is_optional(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def read(self):
                return b'{"Plan": "max", "Email": "DO-NOT-EXPORT"}'

        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append(request)
            return Response()

        with patch("token_tv.sources.ollama_token", return_value="DO-NOT-EXPORT"), \
                patch("token_tv.sources.urlopen", side_effect=fake_urlopen):
            self.assertEqual(ollama_plan({"key": "o"}), "max")
        self.assertEqual(seen[0].get_method(), "POST")
        self.assertEqual(seen[0].full_url, "https://ollama.com/api/me")
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer DO-NOT-EXPORT")
        # The plan is optional enrichment: every failure becomes None, never a provider error.
        with patch("token_tv.sources.ollama_token", side_effect=SourceError("auth_required")):
            self.assertIsNone(ollama_plan({"key": "o"}))
        with patch("token_tv.sources.ollama_token", return_value="key"), \
                patch("token_tv.sources.json_post", side_effect=OSError("boom")):
            self.assertIsNone(ollama_plan({"key": "o"}))
        with patch("token_tv.sources.ollama_token", return_value="key"), \
                patch("token_tv.sources.json_post", return_value={"Email": "only@example.com"}):
            self.assertIsNone(ollama_plan({"key": "o"}))

    def test_ollama_source_is_unverified_and_the_plan_sets_the_allowance(self):
        # Identity: the API reports no email, so nothing is claimed, and the configured email is a label.
        self.assertIsNone(ollama_identity({"source_home": "/unused"}))
        usage = {"range": "30d", "scope": "self", "granularity": "day",
                 "buckets": [{"from": "2026-09-26T00:00:00Z", "until": "2026-09-27T00:00:00Z",
                              "request_count": 100, "usage_usd": 68.84}]}
        payload = {"usage": usage, "plan": "max", "created_at": "2026-04-26T12:49:20.350091Z"}
        account = {"key": "o", "alias": "OLLAMA A", "provider": "ollama", "email": "label-only@example.com"}
        captured = {}

        def fake_normalize(data, allowance, created_at=None, now=None):
            captured.update(data=data, allowance=allowance, created_at=created_at)
            return [{"label": "$68", "used_percent": 22.95, "resets_at": None, "duration_minutes": 43200}]

        # Max = $300/month, so the current period's $68.84 is 22.95%.
        with patch("token_tv.sources.ollama_payload", return_value=payload), \
                patch("token_tv.sources.normalize_ollama", side_effect=fake_normalize):
            row = fetch_account(account)
        self.assertEqual(row["status"], "ok")
        self.assertFalse(row["identity_verified"])
        self.assertEqual(row["windows"], [{"label": "$68", "used_percent": 22.95, "resets_at": None, "duration_minutes": 43200}])
        # The source hands the period's buckets, the plan budget and the creation day to the reader.
        self.assertEqual(captured, {"data": usage, "allowance": 300.0, "created_at": "2026-04-26T12:49:20.350091Z"})
        self.assertNotIn("label-only@example.com", json.dumps(row))
        # An explicit ollama_allowance_usd wins over the plan.
        with patch("token_tv.sources.ollama_payload", return_value=payload), \
                patch("token_tv.sources.normalize_ollama", side_effect=fake_normalize):
            fetch_account(dict(account, ollama_allowance_usd=40.0))
        self.assertEqual(captured["allowance"], 40.0)
        # A free / unknown / missing plan means no honest percentage: unknown, never a fake 0%.
        for plan in ("free", "something-else", None):
            with patch("token_tv.sources.ollama_payload", return_value=dict(payload, plan=plan)):
                row = fetch_account(account)
            self.assertEqual((row["status"], row["windows"]), ("quota_unavailable", []), plan)

    def test_snapshot_import_accepts_dollar_spend_labels_but_not_junk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            row = {"key": "o", "alias": "OLLAMA A", "provider": "ollama", "status": "ok",
                   "identity_verified": False, "fetched_at": time.time(),
                   "windows": [{"label": "$68", "used_percent": 22.95, "resets_at": None, "duration_minutes": 43200},
                               {"label": "BUDGET", "used_percent": 12, "resets_at": None, "duration_minutes": 43200},
                               {"label": "HACK", "used_percent": 50, "resets_at": None, "duration_minutes": 100},
                               {"label": "$nope", "used_percent": 50, "resets_at": None, "duration_minutes": 100}]}
            path.write_text(json.dumps({"accounts": {"o": row}}))
            clean = imported_row({"key": "o", "alias": "OLLAMA A", "provider": "ollama", "snapshot_file": str(path)})
            self.assertEqual([w["label"] for w in clean["windows"]], ["$68", "BUDGET"])

    def test_cache_is_per_account_and_failure_preserves_stale_values(self):
        accounts = [{"key": "a", "alias": "A", "provider": "claude"}, {"key": "b", "alias": "B", "provider": "claude"}]
        replies = {
            "a": {"key": "a", "alias": "A", "provider": "claude", "status": "ok", "windows": [{"label": "WEEK", "used_percent": 31, "resets_at": None, "duration_minutes": 10080}], "fetched_at": 100},
            "b": {"key": "b", "alias": "B", "provider": "claude", "status": "auth_required", "windows": [], "fetched_at": 100},
        }
        calls = []
        def fetch(account):
            calls.append(account["key"])
            return copy.deepcopy(replies[account["key"]])
        store = UsageStore(accounts, fetch=fetch)
        store.refresh()
        one = store.snapshot()
        two = store.snapshot()
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(one, two)
        self.assertEqual(one["accounts"]["a"]["windows"][0]["used_percent"], 31)
        self.assertEqual(one["accounts"]["b"]["windows"], [])
        replies["a"].update(status="error", windows=[], fetched_at=200)
        store.refresh()
        row = store.snapshot()["accounts"]["a"]
        self.assertEqual(row["status"], "stale")
        self.assertEqual(row["windows"][0]["used_percent"], 31)
        self.assertEqual(row["last_success_at"], 100)
        self.assertEqual(row["error_code"], "error")

    def test_wrong_identity_does_not_reuse_previous_account_values(self):
        account = {"key": "a", "alias": "A", "provider": "claude"}
        replies = [dict(account, status="ok", windows=[{"label": "WEEK", "used_percent": 31}], fetched_at=time.time(), identity_verified=True),
                   dict(account, status="identity_mismatch", windows=[], fetched_at=time.time(), identity_verified=False)]
        store = UsageStore([account], fetch=lambda _: replies.pop(0))
        store.refresh()
        store.refresh()
        row = store.snapshot()["accounts"]["a"]
        self.assertEqual(row["status"], "identity_mismatch")
        self.assertEqual(row["windows"], [])

    def test_laptop_fallback_is_replaced_by_verified_mini_login(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.json"
            row = {"key": "a", "alias": "A", "provider": "claude", "status": "ok", "fetched_at": time.time(), "identity_verified": True,
                   "windows": [{"label": "WEEK", "used_percent": 31, "resets_at": None, "duration_minutes": 10080}]}
            path.write_text(json.dumps({"accounts": {"a": row}}))
            account = {"key": "a", "alias": "A", "provider": "claude", "email": "owner@example.com", "source_home": str(Path(directory)/"absent"), "fallback_snapshot_file": str(path)}
            actual = fetch_account(account)
            self.assertEqual(actual["source"], "laptop")
            self.assertEqual(actual["status"], "ok")
            self.assertEqual(actual["mini_auth_status"], "auth_required")
            with patch("token_tv.sources.claude_payload", return_value=({"account": {"email": "owner@example.com"}}, {"seven_day": {"utilization": 24}})):
                actual = fetch_account(account)
            self.assertEqual(actual["source"], "mini")
            self.assertEqual(actual["windows"][0]["used_percent"], 24)
            self.assertNotIn("mini_auth_status", actual)

    def test_renderer_has_real_pixel_dimensions_and_missing_state(self):
        snapshot = {"schema": 1, "accounts": {"a": {"alias": "CLAUDE A", "provider": "claude", "status": "auth_required", "windows": [], "fetched_at": 100}}}
        self.assertEqual(len(pages(snapshot)), 1)
        body = render_page(snapshot, 0)
        image = Image.open(io.BytesIO(body))
        self.assertEqual(image.size, (240, 240))
        self.assertEqual(image.format, "JPEG")
        self.assertGreater(len(image.getcolors(240 * 240)), 20)
        self.assertLess(len(body), 60000)

    def test_overview_keeps_one_account_per_provider_without_losing_details(self):
        def account(provider, alias, status="ok"):
            return {"provider": provider, "alias": alias, "status": status,
                    "windows": [] if status != "ok" else [{"label": "WEEK", "used_percent": 94, "resets_at": None}]}
        snapshot = {"accounts": {
            "ca": account("claude", "CLAUDE A"), "cb": account("claude", "CLAUDE B"),
            "cc": account("claude", "CLAUDE C"), "xa": account("ollama", "OLLAMA A"),
            "ga": account("antigravity", "ANTIGRAVITY A", "auth_required"), "gb": account("antigravity", "ANTIGRAVITY B")}}
        rows = overview_rows(snapshot)
        self.assertEqual([r["key"] for r in rows], ["ca", "xa", "gb"])
        self.assertEqual(len(snapshot["accounts"]), 6)
        self.assertEqual(render_page(snapshot, 0), render_page(snapshot, 1))

    def test_overview_preserves_quota_period_and_unknown_values(self):
        windows = [{"label": "5H", "used_percent": 2}, {"label": "WEEK", "used_percent": 94}]
        self.assertEqual(primary_window({"windows": windows})["label"], "WEEK")
        self.assertIsNone(primary_window({"windows": []}))
        for label in ("BUDGET", "$68"):  # a legacy quota name or the new dollar-spend label
            self.assertEqual(primary_window({"windows": [{"label": label, "used_percent": 100}]})["label"], label)


if __name__ == "__main__":
    unittest.main()
