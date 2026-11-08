"""Operator tests with a fake management service; no Docker or provider keys needed."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.runtime = self.home / "runtime"
        self.runtime.mkdir()
        self.patches = [patch.object(setup, "HOME", self.home), patch.object(setup, "RUNTIME", self.runtime)]
        for item in self.patches:
            item.start()
        self.inventory = self.home / "models.json"
        self.inventory.write_text(json.dumps({"models": [{"alias": "one", "host": "anthropic",
            "provider": "anthropic", "api_model": "test-model", "key_slot": 3}]}))
        self.csv = self.home / "teams.csv"
        self.csv.write_text("team_id,budget_usd\n11111111-1111-4111-8111-111111111111,5.00\n")

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_config_security_and_three_slots(self):
        setup.init()
        self.assertEqual((self.home / ".env").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            setup.init()
        setup.configure(self.inventory)
        config = json.loads((self.runtime / "config.json").read_text())
        self.assertEqual(config["model_list"][0]["litellm_params"]["api_key"], "os.environ/PROVIDER_KEY_3")
        self.assertTrue(config["general_settings"]["fail_closed_budget_enforcement"])
        self.assertFalse(config["general_settings"]["disable_budget_reservation"])
        self.assertEqual(config["litellm_settings"]["cache_params"]["supported_call_types"], [])
        self.assertEqual(config["router_settings"]["fallbacks"], [])

    def configure_entry(self, **changes):
        entry = {"alias": "one", "host": "bedrock", "provider": "bedrock",
                 "api_model": "global.anthropic.test-model", "key_slot": 3,
                 "bedrock_auth": "bearer", "aws_region_name": "us-east-1"}
        entry.update(changes)
        self.inventory.write_text(json.dumps({"models": [entry]}))
        setup.configure(self.inventory)
        return json.loads((self.runtime / "config.json").read_text())["model_list"][0]["litellm_params"]

    def test_bedrock_bearer_region_and_key_slot_are_explicit(self):
        params = self.configure_entry()
        self.assertEqual(params["model"], "bedrock/global.anthropic.test-model")
        self.assertEqual(params["api_key"], "os.environ/PROVIDER_KEY_3")
        self.assertEqual(params["aws_region_name"], "us-east-1")

    def test_bedrock_iam_never_sends_a_provider_bearer_key(self):
        params = self.configure_entry(bedrock_auth="iam", key_slot=None)
        self.assertNotIn("api_key", params)
        self.assertEqual(params["aws_region_name"], "us-east-1")

    def test_direct_claude_switch_retains_alias_and_other_routes(self):
        other = {"alias": "qwen", "host": "openai_compatible", "provider": "openai",
                 "api_model": "qwen-test", "key_slot": 2, "api_base": "https://example.com/v1", "free_model": True}
        old = {"alias": "claude-fable", "host": "bedrock", "provider": "bedrock",
               "api_model": "global.anthropic.test", "key_slot": 3, "bedrock_auth": "bearer",
               "aws_region_name": "us-east-1", "max_parallel_requests": 2, "free_model": True}
        self.inventory.write_text(json.dumps({"models": [other, old]}))
        setup.configure_direct_claude(self.inventory, "claude-fable-5-1", 3)
        inventory = json.loads(self.inventory.read_text())["models"]
        self.assertEqual(inventory[0], other)
        self.assertEqual(inventory[1]["alias"], "claude-fable")
        self.assertEqual(inventory[1]["max_parallel_requests"], 2)
        params = json.loads((self.runtime / "config.json").read_text())["model_list"][1]["litellm_params"]
        self.assertEqual(params["model"], "anthropic/claude-fable-5-1")
        self.assertEqual(params["api_key"], "os.environ/PROVIDER_KEY_3")
        self.assertNotIn("aws_region_name", params)
        self.assertNotIn("input_cost_per_token", params)
        routes = json.loads((self.runtime / "routes.json").read_text())
        self.assertEqual(routes["anthropic:claude-fable-5-1"], "claude-fable")
        self.assertNotIn("bedrock:global.anthropic.test", routes)

    def test_invalid_direct_claude_switch_preserves_inventory(self):
        before = self.inventory.read_bytes()
        with self.assertRaises(ValueError):
            setup.configure_direct_claude(self.inventory, "global.anthropic.test", 3)
        self.assertEqual(self.inventory.read_bytes(), before)
        invalid = {"models": [{"alias": "other", "host": "not-a-host", "provider": "openai", "api_model": "x", "key_slot": 1}]}
        self.inventory.write_text(json.dumps(invalid))
        before = self.inventory.read_bytes()
        with self.assertRaises(ValueError):
            setup.configure_direct_claude(self.inventory, "claude-fable-5-1", 3)
        self.assertEqual(self.inventory.read_bytes(), before)
        self.assertFalse((self.runtime / "config.json").exists())

    def test_bedrock_missing_auth_or_region_is_rejected(self):
        for changes in ({"bedrock_auth": None}, {"aws_region_name": None}, {"aws_region_name": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configure_entry(**changes)

    def test_custom_qwen_endpoint_and_explicit_free_pricing(self):
        params = self.configure_entry(host="openai_compatible", provider="openai",
            api_model="qwen-test", key_slot=2, api_base="https://example.com/v1", free_model=True)
        self.assertEqual(params["api_base"], "https://example.com/v1")
        self.assertEqual(params["api_key"], "os.environ/PROVIDER_KEY_2")
        self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (0, 0))

    def test_custom_endpoint_missing_or_insecure_is_rejected(self):
        for address in ("http://example.com/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=private"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.configure_entry(host="openai_compatible", provider="openai", api_base=address)
        with self.assertRaises(ValueError):
            self.configure_entry(host="openai_compatible", provider="openai")

    def write_backup_inventory(self, **backup):
        self.inventory.write_text(json.dumps({"models": [
            {"alias": "open", "host": "openai_compatible", "provider": "openai", "api_model": "cf-qwen", "key_slot": 2,
             "api_base": "https://cf.example.com/v1", "free_model": True},
            {"alias": "open", "host": "openai_compatible", "provider": "openai", "api_model": "or-qwen", "key_slot": 4,
             "api_base": "https://or.example.com/v1", **backup},
        ]}))

    def test_backup_is_the_same_alias_tried_second(self):
        self.write_backup_inventory(backup_order=2, free_model=True)
        setup.configure(self.inventory)
        config = json.loads((self.runtime / "config.json").read_text())
        self.assertEqual([(m["model_name"], m["litellm_params"]["order"]) for m in config["model_list"]],
                         [("open", 1), ("open", 2)])
        self.assertEqual(config["model_list"][1]["litellm_params"]["api_key"], "os.environ/PROVIDER_KEY_4")
        # Never a switch to another model, and no new alias for teams to be granted.
        self.assertEqual(config["router_settings"]["fallbacks"], [])
        self.assertEqual(config["router_settings"]["num_retries"], 0)
        self.assertEqual(json.loads((self.runtime / "routes.json").read_text()),
                         {"openai_compatible:cf-qwen": "open"})

    def test_several_accounts_each_use_their_own_key_and_address(self):
        rows = [{"alias": "open", "host": "openai_compatible", "provider": "openai", "api_model": "@cf/qwen",
                 "key_slot": slot, "api_base": f"https://example.com/{slot}/v1", "free_model": True,
                 **({"backup_order": order} if order > 1 else {})}
                for order, slot in ((1, 2), (2, 4), (3, 5), (4, 8))]
        self.inventory.write_text(json.dumps({"models": rows}))
        setup.configure(self.inventory)
        config = json.loads((self.runtime / "config.json").read_text())
        params = [m["litellm_params"] for m in config["model_list"]]
        self.assertEqual([p["order"] for p in params], [1, 2, 3, 4])
        self.assertEqual([p["api_key"] for p in params], [f"os.environ/PROVIDER_KEY_{s}" for s in (2, 4, 5, 8)])
        self.assertEqual(len({p["api_base"] for p in params}), 4)
        self.assertEqual(config["router_settings"]["fallbacks"], [])
        self.assertEqual(json.loads((self.runtime / "routes.json").read_text()), {"openai_compatible:@cf/qwen": "open"})
        rows[3]["key_slot"] = 9
        self.inventory.write_text(json.dumps({"models": rows}))
        with self.assertRaises(ValueError):
            setup.configure(self.inventory)

    def test_empty_keys_are_named_without_printing_values(self):
        (self.home / ".env").write_text("PROVIDER_KEY_2=private-value\nPROVIDER_KEY_4=\n")
        self.write_backup_inventory(backup_order=2, free_model=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            setup.configure(self.inventory)
        self.assertIn("PROVIDER_KEY_4 is empty", output.getvalue())
        self.assertNotIn("PROVIDER_KEY_2 is empty", output.getvalue())
        self.assertNotIn("private-value", output.getvalue())

    def test_without_a_backup_nothing_gets_an_order(self):
        setup.configure(self.inventory)
        config = json.loads((self.runtime / "config.json").read_text())
        self.assertNotIn("order", config["model_list"][0]["litellm_params"])

    def test_bad_backups_are_rejected(self):
        for backup in ({"backup_order": 1, "free_model": True}, {"backup_order": True, "free_model": True},
                       {"backup_order": "2", "free_model": True}, {"backup_order": 2},
                       {"free_model": True}):  # The last is a second primary on one alias.
            self.write_backup_inventory(**backup)
            with self.subTest(backup=backup), self.assertRaises(ValueError):
                setup.configure(self.inventory)
        self.inventory.write_text(json.dumps({"models": [
            {"alias": "open", "host": "openai", "provider": "openai", "api_model": "x", "key_slot": 4,
             "backup_order": 2, "free_model": True}]}))
        with self.assertRaises(ValueError):  # A backup with no primary.
            setup.configure(self.inventory)

    def test_backup_can_be_priced_to_charge_teams(self):
        self.write_backup_inventory(backup_order=2, input_cost_per_token=1e-7, output_cost_per_token=5e-7)
        setup.configure(self.inventory)
        params = json.loads((self.runtime / "config.json").read_text())["model_list"][1]["litellm_params"]
        self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (1e-7, 5e-7))

    def test_budget_reader_is_read_only_and_reused_after_a_lost_response(self):
        created = []
        def fail(method, path, body=None):
            if method == "GET":
                raise RuntimeError("Gateway management request failed: HTTP 404")
            created.append(body)
            raise RuntimeError("Lost response after creation")
        with patch.object(setup, "api", side_effect=fail), self.assertRaises(RuntimeError):
            setup.budget_reader()
        self.assertEqual(created[0]["key_type"], "read_only")
        path = self.runtime / "budget-reader-key"
        self.assertEqual(path.read_text(), created[0]["key"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.object(setup, "api", return_value={}) as api:
            setup.budget_reader()
            self.assertEqual(api.call_count, 1)  # Found the old key, so no second key.
        self.assertEqual(path.read_text(), created[0]["key"])

    def test_paid_model_cannot_be_silently_priced_as_free(self):
        with self.assertRaises(ValueError):
            self.configure_entry(input_cost_per_token=0, output_cost_per_token=0)
        with self.assertRaises(ValueError):
            self.configure_entry(free_model=True, input_cost_per_token=1, output_cost_per_token=1)

    def test_repeat_provision_and_update_do_not_reset_spend(self):
        setup.configure(self.inventory)
        created = {}
        calls = []
        def api(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                raise RuntimeError("Gateway management request failed: HTTP 404")
            created.update(body or {})
            return {}
        with patch.object(setup, "api", side_effect=api):
            setup.provision(self.csv)
            key = created["key"]
            setup.provision(self.csv)
            self.assertEqual(len(calls), 2)
            setup.provision(self.csv, update=True)
        self.assertEqual(calls[-1][2]["key"], key)
        self.assertNotIn("spend", calls[-1][2])
        self.assertNotIn("budget_duration", calls[-1][2])
        self.assertEqual(created["key_type"], "llm_api")
        self.assertIsNone(created["budget_duration"])
        self.assertEqual((self.runtime / "team-keys.json").stat().st_mode & 0o777, 0o600)

    def test_lost_create_response_reuses_candidate(self):
        setup.configure(self.inventory)
        created = []
        def fail(method, path, body=None):
            if method == "GET":
                raise RuntimeError("Gateway management request failed: HTTP 404")
            created.append(body["key"])
            raise RuntimeError("Lost response after creation")
        with patch.object(setup, "api", side_effect=fail):
            with self.assertRaises(RuntimeError):
                setup.provision(self.csv)
        with patch.object(setup, "api", return_value={}) as api:
            setup.provision(self.csv)
            self.assertEqual(api.call_count, 1)  # Found old key; no fresh budget/key.
        self.assertEqual(list(json.loads((self.runtime / "team-keys.json").read_text()).values()), created)

    def test_smoke_uses_team_credential_and_bounded_output(self):
        setup.configure(self.inventory)
        team = "11111111-1111-4111-8111-111111111111"
        setup.save(self.runtime / "team-keys.json", {team: "sk-private-team"})
        with patch.object(setup, "api", return_value={"usage": {"total_tokens": 9}}) as api:
            setup.smoke(team, "one")
        args, kwargs = api.call_args
        self.assertEqual(args[:2], ("POST", "/v1/chat/completions"))
        self.assertEqual(args[2]["max_completion_tokens"], 32)
        self.assertEqual(kwargs, {"key": "sk-private-team"})

    def test_unknown_server_fault_never_creates_another_key(self):
        setup.configure(self.inventory)
        with patch.object(setup, "api", side_effect=RuntimeError("HTTP 503")) as api:
            with self.assertRaises(RuntimeError):
                setup.provision(self.csv)
            self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
