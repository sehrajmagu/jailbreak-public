"""No paid calls: verify isolation, tool/reviewer routing, safe budget errors, and default-on routing."""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import gateway
from app.auth import require_team
from app.db import get_db
from app.engine import chat
from app.engine.chat import open_adapter
from app.engine.config import BotConfig, available_hosts
from app.routers import team_pages
from app.schemas import TeamSession

CONFIG = BotConfig(id="bot", level=1, label="A", model="Test", provider="anthropic",
                   host="anthropic", api_model="claude-test", check_type="answer",
                   context_limit_tokens=1000, system_prompt="Hidden", tools=[], defences={})


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        (folder / "keys.json").write_text(json.dumps({"team-a": "sk-a", "team-b": "sk-b"}))
        (folder / "routes.json").write_text(json.dumps({"anthropic:claude-test": "claude-alias",
                                                        "openai:review-test": "review-alias"}))
        self.env = patch.dict(os.environ, {"LITELLM_ENABLED": "true",
            "LITELLM_TEAM_KEYS_FILE": str(folder / "keys.json"),
            "LITELLM_ROUTES_FILE": str(folder / "routes.json")})
        self.env.start()
        self.requests = []
        self.responses = []
        self.real_client = AsyncOpenAI

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def client(self, **kwargs):
        async def transport(request):
            self.requests.append((request.headers["authorization"], json.loads(request.content)))
            response = self.responses.pop(0) if self.responses else None
            if isinstance(response, httpx.Response):
                return response
            if response is not None:
                status, body = response
                return httpx.Response(status, json=body)
            body = ('data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"alias",'
                    '"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                    'data: {"id":"x","object":"chat.completion.chunk","created":0,"model":"alias",'
                    '"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n\n'
                    'data: [DONE]\n\n')
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return self.real_client(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))

    async def test_concurrent_teams_and_review_calls_use_own_key(self):
        gate = asyncio.Event()
        async def turn(team):
            async with gateway.team_scope(team):
                await gate.wait()
                for config in (CONFIG, replace(CONFIG, host="openai", api_model="review-test")):
                    adapter = open_adapter(config)
                    self.assertEqual(adapter.client.max_retries, 0)
                    events = [event async for event in adapter.stream(adapter.build_messages([
                        {"role": "user", "content": team, "toolCalls": []}]))]
                    self.assertTrue(events)
        with patch.object(gateway, "AsyncOpenAI", side_effect=self.client):
            tasks = [asyncio.create_task(turn(team)) for team in ("team-a", "team-b")]
            gate.set()
            await asyncio.gather(*tasks)
        self.assertEqual(len(self.requests), 4)
        for auth, body in self.requests:
            team = body["messages"][-1]["content"]
            self.assertEqual(auth, "Bearer sk-" + team[-1])
            self.assertIn(body["model"], ("claude-alias", "review-alias"))
            self.assertEqual(body["max_completion_tokens"], 2048)
        self.assertIsNone(gateway._client.get())

    async def test_no_missing_key_route_or_context_can_bypass_proxy(self):
        with self.assertRaises(LookupError):
            async with gateway.team_scope("unknown"):
                self.fail("Missing key admitted")
        with self.assertRaises(RuntimeError):
            open_adapter(CONFIG)
        with patch.object(gateway, "AsyncOpenAI", side_effect=self.client):
            with self.assertRaises(LookupError):
                async with gateway.team_scope("team-a"):
                    open_adapter(replace(CONFIG, api_model="unmapped"))
        self.assertIsNone(gateway._client.get())
        self.assertEqual(self.requests, [])

    async def test_cancelled_turn_clears_context(self):
        with patch.object(gateway, "AsyncOpenAI", side_effect=self.client):
            with self.assertRaises(asyncio.CancelledError):
                async with gateway.team_scope("team-a"):
                    raise asyncio.CancelledError()
        self.assertIsNone(gateway._client.get())

    async def run_engine(self, config=CONFIG):
        with patch.object(gateway, "AsyncOpenAI", side_effect=self.client), patch.object(chat, "load_config", return_value=config):
            async with gateway.team_scope("team-a"):
                return [event async for event in chat.run_chat("bot", [
                    {"role": "user", "content": "hello", "toolCalls": []}])]

    async def test_budget_refusals_use_safe_code_across_status_codes(self):
        for status in (400, 422, 429):
            with self.subTest(status=status):
                self.responses = [(status, {"error": {"type": "budget_exceeded",
                    "message": "PRIVATE-KEY-and-prompt", "code": str(status)}})]
                with self.assertLogs("app.engine.chat", level="ERROR") as logs:
                    events = await self.run_engine()
                self.assertEqual(events[-1], {"type": "error", "code": "budget_exceeded",
                    "usage": {"inputTokens": 0, "outputTokens": 0}})
                self.assertNotIn("PRIVATE-KEY", json.dumps(events) + str(logs.output))

    async def test_rate_limits_provider_quota_and_message_text_are_not_team_budget(self):
        for status, error_type in ((429, "throttling_error"), (402, "insufficient_quota"), (500, None)):
            with self.subTest(status=status):
                self.responses = [(status, {"error": {"type": error_type,
                    "message": "budget_exceeded or Budget has been exceeded!"}})]
                with self.assertLogs("app.engine.chat", level="ERROR"):
                    events = await self.run_engine()
                self.assertEqual(events[-1]["code"], "bot_unavailable")

    async def test_stream_budget_error_is_classified(self):
        self.responses = [httpx.Response(200, headers={"content-type": "text/event-stream"},
            text='data: {"error":{"type":"budget_exceeded","message":"PRIVATE"}}\n\n')]
        with self.assertLogs("app.engine.chat", level="ERROR"):
            events = await self.run_engine()
        self.assertEqual(events[-1]["code"], "budget_exceeded")
        self.assertNotIn("PRIVATE", json.dumps(events))

    async def test_reviewer_budget_refusal_keeps_usage_and_hides_held_reply(self):
        self.responses = [None, (429, {"error": {"type": "budget_exceeded", "message": "PRIVATE"}})]
        config = replace(CONFIG, defences={"reviewerModel": "Review"}, reviewer={
            "model": "Review", "provider": "openai", "host": "openai", "api_model": "review-test"})
        with self.assertLogs("app.engine.chat", level="ERROR"):
            events = await self.run_engine(config)
        self.assertEqual([event["type"] for event in events], ["start", "error"])
        self.assertEqual(events[-1]["code"], "budget_exceeded")
        self.assertEqual(events[-1]["usage"], {"inputTokens": 10, "outputTokens": 2})

    async def test_gateway_is_on_by_default_and_missing_scope_cannot_bypass_it(self):
        with patch.dict(os.environ):
            os.environ.pop("LITELLM_ENABLED", None)
            self.assertTrue(gateway.enabled())
            for value in ("", "typo", "true", "TRUE"):
                os.environ["LITELLM_ENABLED"] = value
                self.assertTrue(gateway.enabled())
            os.environ["LITELLM_ENABLED"] = "true"
            with self.assertRaises(RuntimeError):
                open_adapter(CONFIG)
        # Even matching JSON from a direct SDK error isn't a team budget refusal.
        error = type("ProviderError", (Exception,), {"body": {"type": "budget_exceeded"}})()
        self.assertFalse(gateway.budget_exceeded(error))

    async def whole_reply_events(self, message, finish_reason="stop", **config):
        """The real adapter against a fake gateway that only answers a request for the whole reply."""
        self.responses.append((200, {"id": "x", "object": "chat.completion", "created": 0, "model": "alias",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}))
        qwen = replace(CONFIG, host="openai_compatible", api_model="qwen", **config)
        (Path(os.environ["LITELLM_ROUTES_FILE"])).write_text(json.dumps({"openai_compatible:qwen": "open"}))
        with patch.object(gateway, "AsyncOpenAI", side_effect=self.client):
            async with gateway.team_scope("team-a"):
                adapter = open_adapter(qwen)
                return [event async for event in adapter.stream(adapter.build_messages([
                    {"role": "user", "content": "hi", "toolCalls": []}]))]

    async def test_cloudflare_style_hosts_ask_for_the_whole_reply(self):
        events = await self.whole_reply_events({"content": "\n\nThe code is 7391", "reasoning": "hidden"})
        self.assertNotIn("stream", self.requests[0][1])
        self.assertEqual(self.requests[0][1]["model"], "open")
        self.assertEqual(events[0].text, "The code is 7391")
        self.assertEqual((events[-1].stop_reason, events[-1].content), ("stop", "The code is 7391"))
        self.assertEqual((events[-1].usage.input_tokens, events[-1].usage.output_tokens), (10, 2))
        self.assertNotIn("hidden", str(events))

    async def test_whole_reply_keeps_a_reply_that_is_only_a_number(self):
        events = await self.whole_reply_events({"content": "4"})
        self.assertEqual(events[0].text, "4")

    async def test_whole_reply_keeps_numeric_json_content(self):
        events = await self.whole_reply_events({"content": 4})
        self.assertEqual(events[0].text, "4")

    async def test_whole_reply_requires_a_finish_reason(self):
        # Match the streaming path: an unfinished provider response is not success.
        with self.assertRaises(RuntimeError):
            await self.whole_reply_events({"content": "partial"}, finish_reason=None)

    async def test_whole_reply_runs_tool_calls(self):
        events = await self.whole_reply_events({"content": None, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "look_up", "arguments": "{\"id\": 7}"}}]}, finish_reason="tool_calls")
        self.assertEqual((events[0].id, events[0].name, events[0].arguments), ("c1", "look_up", "{\"id\": 7}"))
        self.assertEqual(events[-1].stop_reason, "tool")

    async def test_whole_reply_cut_off_never_runs_half_a_tool_call(self):
        events = await self.whole_reply_events({"content": "part", "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "look_up", "arguments": "{\"id"}}]}, finish_reason="length")
        self.assertEqual(events[-1].stop_reason, "length")
        self.assertFalse([event for event in events if type(event).__name__ == "ToolRequest"])

    async def test_whole_reply_reports_a_refusal(self):
        events = await self.whole_reply_events({"content": None, "refusal": "No."})
        self.assertEqual(events[-1].stop_reason, "refusal")
        events = await self.whole_reply_events({"content": "x"}, finish_reason="content_filter")
        self.assertEqual(events[-1].stop_reason, "refusal")

    def budget_app(self, handler):
        """The real endpoint and gateway code, signed in as team-a, with a fake LiteLLM."""
        (Path(os.environ["LITELLM_TEAM_KEYS_FILE"]).with_name(gateway.BUDGET_READER_FILE)).write_text("sk-reader\n")
        app = FastAPI()
        app.include_router(team_pages.router)
        app.dependency_overrides[require_team] = lambda: TeamSession(team_id="team-a", team_name="A")
        def fake_get(url, **kwargs):
            self.requests.append((url, kwargs))
            return httpx.Client(transport=httpx.MockTransport(handler)).get(url, **kwargs)
        patcher = patch.object(httpx, "get", side_effect=fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app, raise_server_exceptions=False)

    def test_budget_is_read_for_the_session_team_with_the_read_only_key(self):
        def litellm(request):
            return httpx.Response(200, json={"info": {"spend": 1.25, "max_budget": 5, "token": "private"}})
        client = self.budget_app(litellm)
        response = client.get("/me/budget")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"budgetUsd": 5.0, "spentUsd": 1.25, "remainingUsd": 3.75})
        url, sent = self.requests[0]
        self.assertEqual(url, "http://127.0.0.1:4000/key/info")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer sk-reader")
        self.assertEqual(sent["params"]["key"], hashlib.sha256(b"sk-a").hexdigest())
        self.assertNotIn("sk-a", url + json.dumps(sent))
        self.assertNotIn(hashlib.sha256(b"sk-b").hexdigest(), json.dumps(sent))

    def test_overspent_budget_shows_zero_not_a_negative(self):
        client = self.budget_app(lambda r: httpx.Response(200, json={"info": {"spend": 5.5, "max_budget": 5}}))
        self.assertEqual(client.get("/me/budget").json()["remainingUsd"], 0)

    def test_budget_failures_are_503_and_never_log_details(self):
        failures = {
            "refused": lambda r: httpx.Response(403, json={"detail": "sk-a"}),
            "not json": lambda r: httpx.Response(200, text="sk-a"),
            "no cap": lambda r: httpx.Response(200, json={"info": {"spend": 1, "max_budget": None}}),
            "down": lambda r: (_ for _ in ()).throw(httpx.ConnectError("sk-a")),
        }
        for name, handler in failures.items():
            with self.subTest(name), self.assertLogs("app.gateway", "WARNING") as logs:
                self.assertEqual(self.budget_app(handler).get("/me/budget").status_code, 503)
                self.assertNotIn("sk-a", "".join(logs.output))

    def test_budget_is_unavailable_without_the_gateway_reader_or_a_team_key(self):
        client = self.budget_app(lambda r: httpx.Response(200, json={"info": {"spend": 0, "max_budget": 5}}))
        with patch.dict(os.environ, {"LITELLM_ENABLED": "false"}):
            self.assertEqual(client.get("/me/budget").status_code, 503)
        keys = Path(os.environ["LITELLM_TEAM_KEYS_FILE"])
        keys.write_text(json.dumps({"team-b": "sk-b"}))
        self.assertEqual(client.get("/me/budget").status_code, 503)
        keys.write_text(json.dumps({"team-a": "sk-a"}))
        keys.with_name(gateway.BUDGET_READER_FILE).unlink()
        self.assertEqual(client.get("/me/budget").status_code, 503)

    def test_budget_needs_a_team_session(self):
        app = FastAPI()
        app.include_router(team_pages.router)
        app.dependency_overrides[get_db] = lambda: None
        with patch.dict(os.environ, {"SESSION_SECRET": "test-secret"}):
            self.assertEqual(TestClient(app).get("/me/budget").status_code, 401)

    async def test_gateway_only_hosts_never_send_keys_to_a_direct_sdk(self):
        with patch.dict(os.environ, {"LITELLM_ENABLED": "false"}):
            for host in ("bedrock", "openai_compatible"):
                with self.subTest(host=host), self.assertRaises(LookupError):
                    open_adapter(replace(CONFIG, host=host))
        self.assertEqual(self.requests, [])

    async def test_available_hosts_from_routes_without_direct_provider_keys(self):
        self.assertEqual(available_hosts(), ["anthropic", "openai"])
        with patch.dict(os.environ, {"LITELLM_ENABLED": "false", "ANTHROPIC_API_KEY": "direct"}):
            self.assertIn("anthropic", available_hosts())
            async with gateway.team_scope("unknown"):
                self.assertIsNone(gateway._client.get())


if __name__ == "__main__":
    unittest.main()
