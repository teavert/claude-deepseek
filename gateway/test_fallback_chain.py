import unittest
import json
import tempfile
from pathlib import Path
import httpx
from aiohttp.test_utils import TestClient, TestServer
from server import Bridge, ROOT, CHAIN_MODEL
from status_display import RouteStatus
from test_gateway import ProviderError

CONFIG = json.loads((ROOT.parent / 'templates/gateway.example.json').read_text(encoding='utf-8-sig'))
CONFIG.update(primary_transport='translated', fallback_capacity_checks=False)
KEYS = dict(local='test', primary='primary-test', fallback='outrun-test',
            unlid='unlid-test', allmodels='allmodels-test')


class ChainTests(unittest.IsolatedAsyncioTestCase):
    async def run_chain(self, failures, stream=False, model=None):
        calls, records = [], []
        async def translate(**args):
            calls.append(args)
            index = len(calls) - 1
            if index < len(failures):
                raise failures[index]
            if stream:
                async def chunks():
                    yield {'type': 'message_start', 'message': {'role': 'assistant'}}
                    raise httpx.ReadTimeout('interruption')
                return chunks()
            return {'type': 'message', 'content': [{'type': 'text', 'text': 'I cannot help with that.'}]}
        with tempfile.TemporaryDirectory() as folder:
            status = RouteStatus(Path(folder))
            bridge = Bridge(CONFIG, KEYS, translate=translate, status=status,
                            audit=lambda **r: records.append(r))
            async with TestClient(TestServer(bridge.app())) as client:
                response = await client.post('/v1/messages', headers={
                    'x-api-key': 'test', 'x-claude-deepseek-run': 'a' * 32}, json={
                    'model': model or CONFIG['primary_model'], 'messages': [], 'max_tokens': 10, 'stream': stream})
                body = await response.read()
                snapshot = status.runs['a' * 32]
        return response.status, body, calls, records, snapshot

    async def test_exact_order_credentials_models_and_display(self):
        code, _, calls, records, snapshot = await self.run_chain([ProviderError(429)] * 3)
        self.assertEqual(code, 200)
        self.assertEqual([c['api_key'] for c in calls],
                         ['primary-test', 'outrun-test', 'unlid-test', 'allmodels-test'])
        for args, target in zip(calls[1:], CONFIG['fallback_chain']):
            self.assertEqual(args['api_base'], target['base_url'])
            self.assertEqual(args['model'], 'openai/' + target['model'])
            self.assertEqual(args['timeout'].read, CONFIG['stream_idle_timeout_seconds'])
        self.assertEqual([r['route'] for r in records if r['event'] == 'fallback'],
                         ['outrun', 'unlid', 'allmodels'])
        self.assertEqual(snapshot['last']['text']['route'], 'allmodels')
        self.assertEqual(snapshot['fallback_count'], 3)

    async def test_direct_chain_order_and_selection_is_not_failure(self):
        for stage in range(3):
            code, _, calls, records, snapshot = await self.run_chain([ProviderError(429)] * stage, model=CHAIN_MODEL+'[1m]')
            self.assertEqual(code, 200)
            self.assertEqual([c['api_key'] for c in calls], ['outrun-test', 'unlid-test', 'allmodels-test'][:stage+1])
            self.assertEqual([c['model'] for c in calls], ['openai/' + t['model'] for t in CONFIG['fallback_chain'][:stage+1]])
            self.assertEqual(snapshot['fallback_count'], stage)
            self.assertEqual(snapshot['last']['text']['selected_model'], CHAIN_MODEL)
            self.assertEqual(snapshot['last']['text']['route'], CONFIG['fallback_chain'][stage]['route'])
            self.assertEqual(len([r for r in records if r['event'] == 'chain_start']), 1)

    async def test_direct_chain_stops_on_policy_errors_and_midstream_failure(self):
        for stage in range(3):
            code, _, calls, _, _ = await self.run_chain([ProviderError(503)] * stage + [ProviderError(500, 'content_policy_violation')], model=CHAIN_MODEL)
            self.assertEqual(code, 500)
            self.assertEqual(len(calls), stage+1)
            code, body, calls, _, _ = await self.run_chain([ProviderError(429)] * stage, stream=True, model=CHAIN_MODEL)
            self.assertEqual(code, 200)
            self.assertIn(b'not replayed', body)
            self.assertEqual(len(calls), stage+1)

    async def test_direct_chain_exhaustion_does_not_loop(self):
        code, _, calls, _, _ = await self.run_chain([httpx.ConnectError('offline')] * 3, model=CHAIN_MODEL)
        self.assertEqual(code, 502)
        self.assertEqual(len(calls), 3)

    async def test_stops_on_success_including_refusal_at_every_stage(self):
        for stage in range(4):
            code, body, calls, _, _ = await self.run_chain([ProviderError(503)] * stage)
            self.assertEqual(code, 200)
            self.assertIn(b'cannot help', body)
            self.assertEqual(len(calls), stage + 1)

    async def test_nonretryable_and_policy_errors_stop_every_stage(self):
        for stage in range(4):
            for status, text in [(401, 'bad key'), (402, 'balance'), (403, 'forbidden'),
                                 (400, 'invalid'), (500, 'content_policy_violation')]:
                code, _, calls, _, _ = await self.run_chain(
                    [ProviderError(503)] * stage + [ProviderError(status, text)])
                self.assertEqual(code, status)
                self.assertEqual(len(calls), stage + 1)

    async def test_exhaustion_does_not_loop(self):
        code, _, calls, _, _ = await self.run_chain([httpx.ConnectError('offline')] * 4)
        self.assertEqual(code, 502)
        self.assertEqual(len(calls), 4)

    async def test_midstream_failure_never_advances_at_any_stage(self):
        for stage in range(4):
            code, body, calls, _, _ = await self.run_chain([ProviderError(429)] * stage, stream=True)
            self.assertEqual(code, 200)
            self.assertIn(b'not replayed', body)
            self.assertEqual(len(calls), stage + 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
