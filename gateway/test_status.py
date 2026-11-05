import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

PROFILE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROFILE / "gateway"))
import status_display as display
from server import Bridge
from aiohttp.test_utils import TestClient, TestServer
import httpx

CONFIG = json.loads((PROFILE / "templates/gateway.example.json").read_text(encoding="utf-8-sig"))
CONFIG.update(primary_transport='translated', fallback_capacity_checks=False)
KEYS = dict(local="local-test", primary="di-test", fallback="am-test", support="or-test")
RUN = "a" * 32


class StatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = display.STATE
        display.STATE = Path(self.temp.name)
        self.status = display.RouteStatus(display.STATE)

    def tearDown(self):
        display.STATE = self.old
        self.temp.cleanup()

    async def test_fallback_visible_during_request_and_recovery(self):
        calls = 0
        class Limit(Exception):
            status_code = 429
        async def translate(**args):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertIn("DeepInfra [ACTIVE]", display.render({}, RUN))
                raise Limit()
            if calls == 2:
                screen = display.render({}, RUN)
                self.assertIn("Outrun [ACTIVE]", screen)
                self.assertIn("FALLBACK 1/3", screen)
                self.assertIn("DeepInfra -> Outrun: rate limit (HTTP 429)", " ".join(screen.split()))
            return {"type": "message", "content": []}
        bridge = Bridge(CONFIG, KEYS, translate=translate, status=self.status)
        async with TestClient(TestServer(bridge.app())) as client:
            for _ in range(2):
                response = await client.post("/v1/messages", headers={"x-api-key": "local-test", "x-claude-deepseek-run": RUN},
                    json={"model": CONFIG["primary_model"], "messages": [], "max_tokens": 10})
                self.assertEqual(response.status, 200)
                await response.read()
            screen = display.render({}, RUN)
            self.assertIn("DeepInfra [last: idle]", screen)
            self.assertIn("Last switch: DeepInfra -> Outrun: rate limit (HTTP 429)", " ".join(screen.split()))
            self.assertIn("switches 1", screen)
            self.assertNotIn("fallback", display.render({}, "b" * 32))

    async def test_concurrent_sessions_and_support_header_privacy(self):
        async def translate(**args):
            await asyncio.sleep(0.01)
            return {"type": "message", "content": []}
        def transport(request):
            self.assertNotIn("x-claude-deepseek-run", request.headers)
            return httpx.Response(200, json={"type": "message", "content": []})
        records = []
        bridge = Bridge(CONFIG, KEYS, translate=translate, status=self.status,
                        audit=lambda **r: records.append(r), client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        async with TestClient(TestServer(bridge.app())) as client:
            async def request(run, model):
                response = await client.post("/v1/messages", headers={"x-api-key": "local-test", "x-claude-deepseek-run": run},
                    json={"model": model, "messages": [], "max_tokens": 10})
                self.assertEqual(response.status, 200)
                await response.read()
            await asyncio.gather(request(RUN, CONFIG["primary_model"]), request("b" * 32, CONFIG["support_model"]))
        a = display.read_json(display.STATE / RUN / "routes.json")
        b = display.read_json(display.STATE / ("b" * 32) / "routes.json")
        self.assertEqual(set(a["last"]), {"text"})
        self.assertEqual(set(b["last"]), {"support"})
        self.assertFalse(a["active"] or b["active"])
        for run in (RUN, "b" * 32):
            events = [r for r in records if r["run_id"] == run]
            self.assertEqual(len({r["id"] for r in events}), 1)

    async def test_background_success_does_not_hide_main_fallback(self):
        def event(kind, rid, **fields):
            self.status.record(dict(event=kind, run_id=RUN, id=rid, time=1, **fields))
        event('request', 'main', path='/v1/messages', route='gpt', model='gpt-6-astra', workload='tools')
        event('fallback', 'main', from_route='gpt', route='deepinfra', fallback_model=CONFIG['primary_model'], reason='gpt_tool_name_rejected')
        event('complete', 'main', usage={'input_tokens':1000,'output_tokens':100})
        event('request', 'title', path='/v1/messages', route='gpt', model='gpt-6-astra', workload='text')
        event('complete', 'title', usage={'input_tokens':20,'output_tokens':5})
        screen = ' '.join(display.render({}, RUN).split())
        self.assertTrue(screen.startswith('Tool-capable request: DeepSeek'))
        self.assertIn('FALLBACK', screen)
        self.assertIn('GPT adapter rejected a tool name', screen)
        self.assertIn('Text-only/background: GPT Astra high [idle]', screen)
        self.assertIn('tokens in 1,000', screen)
        self.assertIn('GPT review (Astra high)', screen)
        saved=display.read_json(display.STATE/RUN/'routes.json')
        self.assertEqual(saved['last']['lead']['route'],'deepinfra')
        self.assertEqual(saved['last']['text']['route'],'gpt')

    async def test_hooks_overlap_finish_reset_and_session_isolation(self):
        payload = {"session_id": "session1", "hook_event_name": "SubagentStart", "agent_id": "one", "agent_type": "frontier-reviewer", "last_assistant_message": "PRIVATE"}
        display.hook(payload, RUN)
        self.assertIn("PLANNING", display.render(payload, RUN))
        self.assertNotIn("PLANNING", display.render({"session_id": "other"}, RUN))
        second = dict(payload, agent_id="two", agent_type="finding-validator")
        display.hook(second, RUN)
        self.assertIn("PLANNING + VALIDATING", display.render(payload, RUN))
        display.hook(dict(payload, hook_event_name="SubagentStop"), RUN)
        self.assertIn("VALIDATING", display.render(payload, RUN))
        self.assertIn("planning finished", display.render(payload, RUN))
        display.hook(dict(second, hook_event_name="SubagentStop"), RUN)
        self.assertIn("ChatGPT subscription: idle", display.render(payload, RUN))
        display.hook(dict(payload, hook_event_name="UserPromptSubmit"), RUN)
        self.assertNotIn("finished", display.render(payload, RUN))
        for path in display.STATE.rglob("*.json"):
            self.assertNotIn("PRIVATE", path.read_text())

    async def test_missing_corrupt_and_unsafe_ids(self):
        self.assertEqual(display.valid_run("../../private"), "")
        self.assertIn("restart", display.render({}, ""))
        path = display.STATE / RUN / "routes.json"
        path.parent.mkdir()
        path.write_text("broken")
        self.assertIn("ready", display.render({}, RUN))

    async def test_agent_failure_does_not_stay_active(self):
        payload = {"session_id": "failure", "hook_event_name": "SubagentStart", "agent_id": "one", "agent_type": "frontier-reviewer"}
        display.hook(payload, RUN)
        display.hook({"session_id": "failure", "hook_event_name": "PostToolUseFailure", "tool_name": "Agent",
                      "tool_input": {"subagent_type": "frontier-reviewer"}, "error": "PRIVATE FAILURE BODY"}, RUN)
        screen = display.render(payload, RUN)
        self.assertIn("planning FAILED", screen)
        self.assertNotIn("PLANNING", screen)
        display.hook(dict(payload, hook_event_name="UserPromptSubmit"), RUN)
        self.assertNotIn("PLANNING", display.render(payload, RUN))
        self.assertNotIn("FAILED", display.render(payload, RUN))
        for path in display.STATE.rglob("*.json"):
            self.assertNotIn("PRIVATE", path.read_text())

    async def test_full_chain_display_and_terminal_failure(self):
        def record(event, **values):
            self.status.record(dict(event=event, run_id=RUN, id="chain", **values))
        record("request", path="/v1/messages", route="deepinfra", model=CONFIG['primary_model'])
        sources = ['DeepInfra', 'Outrun', 'Unlid']
        reasons = ['http_429', 'http_503', 'connection_error']
        labels = ['rate limit (HTTP 429)', 'service unavailable (HTTP 503)', 'connection failed']
        for i, target in enumerate(CONFIG['fallback_chain']):
            record('fallback', route=target['route'], fallback_model=target['model'],
                   fallback_position=i + 1, fallback_total=3, reason=reasons[i])
            screen = ' '.join(display.render({}, RUN).split())
            self.assertIn(f"FALLBACK {i + 1}/3", screen)
            self.assertIn(f"{display.PROVIDERS[target['route']]} [ACTIVE]", screen)
            for j in range(i + 1):
                self.assertIn(f"{sources[j]} -> {display.PROVIDERS[CONFIG['fallback_chain'][j]['route']]}: {labels[j]}", screen)
        record('fallback_error', status=402)
        screen = ' '.join(display.render({}, RUN).split())
        self.assertIn('AllModels [last: error]', screen)
        self.assertIn('Stopped: payment/balance error (HTTP 402); no further automatic switch', screen)
        self.assertNotIn('[ACTIVE]', screen)
        record('request', path='/v1/messages', route='deepinfra', model=CONFIG['primary_model'])
        screen = ' '.join(display.render({}, RUN).split())
        self.assertIn('DeepInfra [ACTIVE]', screen)
        self.assertIn('Last switch: Unlid -> AllModels: connection failed', screen)
        self.assertNotIn('Stopped:', screen)
        self.assertNotIn('FALLBACK 3/3', screen)

    async def test_older_status_snapshots_still_render(self):
        display.atomic_json(display.STATE / RUN / 'routes.json', {
            'last': {'lead': {'role': 'lead', 'route': 'outrun', 'state': 'idle', 'reason': 'http_429'}}})
        self.assertIn('Reason: rate limit (HTTP 429)', display.render({}, RUN))

    async def test_usage_and_latency_display(self):
        self.status.record(dict(event='request',run_id=RUN,id='metrics',path='/v1/messages',route='deepinfra',model=CONFIG['primary_model']))
        self.status.record(dict(event='complete',run_id=RUN,id='metrics',duration_ms=2500,ttft_ms=1000,
                                usage={'input_tokens':50,'cache_read_input_tokens':10000,'output_tokens':20}))
        screen=' '.join(display.render({},RUN).split())
        self.assertIn('First output 1.0s',screen)
        self.assertIn('cached 10,000',screen)
        self.assertIn('total 2.5s',screen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
