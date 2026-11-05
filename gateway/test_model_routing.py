import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx
from aiohttp.test_utils import TestClient, TestServer
from gpt_main import GptMain, GptFailure, host_input, thread_params, message, stream_events, upstream_failure, tool_alias
from server import Bridge, ROOT, CHAIN_MODEL
from status_display import request_session, session_key

CONFIG = json.loads((ROOT.parent / 'templates/gateway.example.json').read_text())
RUN = 'c' * 32
BODY = {'model': CONFIG['primary_model'], 'messages': [{'role': 'user', 'content': 'fixture'}], 'max_tokens': 128}
GPT = message([{'type': 'text', 'text': 'GPT fixture'}], 'end_turn', {'input_tokens': 10, 'output_tokens': 2})
DEEPSEEK = {**GPT, 'model': CONFIG['primary_model']}


class NativeModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gpt = AsyncMock()
        self.gpt.complete.return_value = GPT
        self.requests, self.records = [], []
        def transport(request):
            self.requests.append(request)
            data = {'input_tokens': 123} if request.url.path.endswith('count_tokens') else DEEPSEEK
            return httpx.Response(200, json=data)
        self.bridge = Bridge(CONFIG, {'local': 'fixture', 'primary': 'provider-fixture'}, gpt=self.gpt,
            audit=lambda **r: self.records.append(r),
            client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        self.client = TestClient(TestServer(self.bridge.app()))
        await self.client.start_server()
        self.headers = {'x-api-key': 'fixture', 'x-claude-deepseek-run': RUN}

    async def asyncTearDown(self):
        await self.client.close()

    async def request(self, **overrides):
        return await self.client.post('/v1/messages', headers=self.headers, json={**BODY, **overrides})

    async def test_native_selection_switches_both_directions_without_toggle_state(self):
        for selected, expected in [(CONFIG['primary_model'], CONFIG['primary_model']),
                ('gpt-6-astra', 'gpt-6-astra'), (CONFIG['primary_model'], CONFIG['primary_model'])]:
            self.assertEqual((await (await self.request(model=selected)).json())['model'], expected)
        self.gpt.complete.assert_awaited_once()

    async def test_legacy_toggle_is_retired_and_cannot_override_native_selection(self):
        response = await self.client.post('/control/gptswitch', headers=self.headers, json={'action':'on'})
        self.assertEqual(response.status, 410)
        self.assertIn('/model', await response.text())
        self.assertEqual((await (await self.request()).json())['model'], CONFIG['primary_model'])
        self.gpt.complete.assert_not_awaited()

    async def test_auth_and_model_validation(self):
        response = await self.client.post('/v1/messages', headers={'x-api-key':'bad'}, json={**BODY,'model':'gpt-6-astra'})
        self.assertEqual(response.status, 401)
        self.assertEqual((await self.request(model='not-configured')).status, 400)

    async def test_model_list_and_count_use_correct_provider_id(self):
        response = await self.client.get('/v1/models', headers=self.headers)
        ids = {row['id'] for row in (await response.json())['data']}
        self.assertIn('gpt-6-astra', ids)
        self.assertIn(CONFIG['primary_model'], ids)
        self.assertIn(CHAIN_MODEL, ids)
        response = await self.client.post('/v1/messages/count_tokens', headers=self.headers,
            json={**BODY,'model':'gpt-6-astra'})
        self.assertEqual((await response.json())['input_tokens'], 123)
        self.assertEqual(json.loads(self.requests[-1].content)['model'],CONFIG['primary_model'])
        self.gpt.complete.assert_not_awaited()

    async def test_chain_capacity_skips_outrun_and_counts_with_exact_primary_id(self):
        self.bridge.keys.update(fallback='outrun-fixture', unlid='unlid-fixture', allmodels='allmodels-fixture')
        self.bridge.translate = AsyncMock(return_value=DEEPSEEK)
        response = await self.request(model=CHAIN_MODEL, max_tokens=40000)
        self.assertEqual(response.status, 200)
        args = self.bridge.translate.call_args.kwargs
        self.assertEqual(args['model'], 'openai/' + CONFIG['fallback_chain'][1]['model'])
        self.assertEqual(args['api_key'], 'unlid-fixture')
        self.assertTrue(all(r.url.path.endswith('count_tokens') for r in self.requests))
        self.assertEqual(json.loads(self.requests[-1].content)['model'], CONFIG['primary_model'])
        self.gpt.complete.assert_not_awaited()
        response = await self.client.post('/v1/messages/count_tokens', headers=self.headers, json={**BODY, 'model': CHAIN_MODEL+'[1m]'})
        self.assertEqual((await response.json())['input_tokens'], 123)

    async def test_native_default_context_marker_never_reaches_provider(self):
        response = await self.request(model=CONFIG['primary_model']+'[1m]')
        self.assertEqual(response.status,200)
        self.assertEqual(json.loads(self.requests[-1].content)['model'], CONFIG['primary_model'])
        response = await self.request(model='gpt-6-astra[1m]')
        self.assertEqual((await response.json())['model'],'gpt-6-astra')

    async def test_concurrent_models_do_not_change_each_other(self):
        async def request(selected):
            return (await (await self.request(model=selected)).json())['model']
        selected = ['gpt-6-astra', CONFIG['primary_model'], 'gpt-6-astra', CONFIG['primary_model']]
        self.assertEqual(await asyncio.gather(*(request(model) for model in selected)), selected)

    async def test_failure_uses_unmodified_deepseek_and_gpt_retries_next_request(self):
        self.gpt.complete.side_effect = GptFailure('timeout')
        response = await self.request(model='gpt-6-astra', temperature=0.4)
        self.assertEqual(response.status, 200)
        sent = json.loads(self.requests[0].content)
        self.assertEqual(sent['temperature'], 0.4)
        self.assertEqual(sent['max_tokens'], 128)
        self.assertEqual(sent['model'], CONFIG['primary_model'])
        self.assertEqual(sent['output_config']['effort'], 'max')
        self.assertNotIn('gpt', json.dumps(sent))
        self.assertEqual(next(r for r in self.records if r['event'] == 'fallback')['from_route'], 'gpt')
        self.gpt.complete.side_effect = None
        self.assertEqual((await (await self.request(model='gpt-6-astra')).json())['model'], 'gpt-6-astra')

    async def test_policy_errors_and_successful_refusals_do_not_fallback(self):
        self.gpt.complete.side_effect = upstream_failure({'message': 'content policy'})
        self.assertEqual((await self.request(model='gpt-6-astra')).status, 400)
        self.gpt.complete.side_effect = None
        self.gpt.complete.return_value = message([{'type': 'text', 'text': 'I cannot help with that.'}], 'end_turn', GPT['usage'])
        self.assertEqual((await self.request(model='gpt-6-astra')).status, 200)
        self.assertEqual(self.requests, [])

    async def test_cancellation_does_not_fallback(self):
        self.gpt.complete.side_effect = asyncio.CancelledError()
        with self.assertRaises(Exception):
            await self.request(model='gpt-6-astra')
        self.assertEqual(self.requests, [])

    async def test_streaming_tool_call_is_anthropic_compatible(self):
        tool = {'type': 'tool_use', 'id': 'toolu_fixture', 'name': 'lookup', 'input': {'name': 'a'}}
        self.gpt.complete.return_value = message([tool], 'tool_use', GPT['usage'])
        response = await self.request(model='gpt-6-astra', stream=True)
        events = [json.loads(line[6:]) for line in (await response.text()).splitlines() if line.startswith('data: ')]
        self.assertEqual(response.headers['x-claude-deepseek-route'], 'gpt')
        self.assertEqual(events[0]['message']['model'], 'gpt-6-astra')
        self.assertEqual(events[1]['content_block']['name'], 'lookup')
        self.assertEqual(json.loads(events[2]['delta']['partial_json']), {'name': 'a'})
        self.assertEqual(events[-2]['delta']['stop_reason'], 'tool_use')
        self.assertEqual(events[-1]['type'], 'message_stop')


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_current_and_legacy_conversation_metadata(self):
        session = '12345678-1234-1234-1234-123456789abc'
        for user_id in [json.dumps({'session_id': session}), 'user_fixture_account__session_' + session]:
            self.assertEqual(request_session({'metadata': {'user_id': user_id}}), session.replace('-', ''))
        for value in [None, [], {}, '../../other', '']:
            self.assertEqual(session_key(value), '')
        self.assertEqual(request_session({'metadata': {'user_id': '{}'}}), '')
        self.assertEqual(request_session({'metadata': []}), '')

    def test_history_preserves_tool_results_and_omits_private_thinking(self):
        body = {'messages': [
            {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'private'},
                {'type': 'tool_use', 'id': 'one', 'name': 'lookup', 'input': {'x': 1}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'one', 'content': 'result'}]}]}
        before = copy.deepcopy(body)
        result = host_input(body)[0]['text']
        self.assertNotIn('private', result)
        self.assertIn('tool_use_id', result)
        self.assertIn('result', result)
        self.assertEqual(body, before)

    def test_thread_uses_high_and_has_no_execution_environment(self):
        params = thread_params({'tools': [{'name': 'lookup', 'input_schema': {'type': 'object'}}]})
        self.assertEqual(params['model'], 'gpt-6-astra')
        self.assertEqual(params['config']['model_reasoning_effort'], 'high')
        self.assertEqual(params['environments'], [])
        self.assertTrue(params['ephemeral'])
        self.assertEqual(params['dynamicTools'][0]['name'], tool_alias('lookup'))
        self.assertIn('Claude tool: lookup', params['dynamicTools'][0]['description'])

    def test_tool_choice_and_images(self):
        body = {'tools': [{'name': 'one'}, {'name': 'two'}], 'tool_choice': {'type': 'tool', 'name': 'two'}}
        self.assertEqual([t['name'] for t in thread_params(body)['dynamicTools']], [tool_alias('two')])
        image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'fixture'}}
        inputs = host_input({'messages': [{'role': 'user', 'content': [image]}]})
        self.assertEqual(inputs[1]['url'], 'data:image/png;base64,fixture')

    async def protocol(self, notifications, model='gpt-6-astra', effort='high'):
        stream = asyncio.StreamReader()
        for event in [{'id': 1, 'result': {}}, {'id': 2, 'result': {'account': {'type': 'chatgpt'}}},
                      {'id': 3, 'result': {'thread': {'id': 'fixture'}, 'model': model, 'reasoningEffort': effort}},
                      {'id': 4, 'result': {}}, *notifications]:
            stream.feed_data((json.dumps(event) + '\n').encode())
        stream.feed_eof()
        class Writer:
            def write(self, raw): pass
            async def drain(self): pass
        process = type('Process', (), {'stdin': Writer(), 'stdout': stream})()
        return await GptMain({}).exchange(process, thread_params({'tools': [{'name': 'lookup'}]}), [],
                                          tool_names={tool_alias('lookup'): 'lookup'})

    async def test_protocol_text_and_tool_handoff(self):
        result = await self.protocol([
            {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'hello'}}},
            {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}])
        self.assertEqual(result['content'][0]['text'], 'hello')
        result = await self.protocol([{'id': 20, 'method': 'item/tool/call', 'params': {'tool': tool_alias('lookup'), 'arguments': {'name': 'fixture'}}}])
        self.assertEqual(result['stop_reason'], 'tool_use')
        self.assertEqual(result['content'][0]['name'], 'lookup')

    def test_reserved_and_nonstandard_tool_names_have_distinct_stable_aliases(self):
        names = ['mcp__codex-subscription__cancel', 'mcp__fixture__tool.with.dot', 'Read', 'read']
        params = thread_params({'tools': [{'name': name} for name in names]})
        aliases = [t['name'] for t in params['dynamicTools']]
        self.assertEqual(len(set(aliases)), len(names))
        for name, alias in zip(names, aliases):
            self.assertRegex(alias, r'^host_[a-f0-9]{24}$')
            self.assertEqual(alias, tool_alias(name))
        error = upstream_failure({'code': -32600, 'message': 'dynamic tool name is reserved: mcp__fixture__test'})
        self.assertEqual(error.reason, 'gpt_tool_name_rejected')

    def test_error_categories_do_not_expose_upstream_details(self):
        for message_text, reason in [('invalid input schema private-data', 'gpt_tool_schema_rejected'),
                ('rate limit private-data', 'gpt_rate_limit'), ('context window exceeded', 'gpt_context_limit'),
                ('authentication failed private-data', 'gpt_authentication_failed')]:
            self.assertEqual(upstream_failure({'message': message_text}).reason, reason)

    async def test_protocol_rejects_model_effort_mismatch_and_unknown_tools(self):
        for args in ({'model': 'different'}, {'effort': 'low'}):
            with self.assertRaises(GptFailure):
                await self.protocol([], **args)
        with self.assertRaises(GptFailure) as error:
            await self.protocol([{'id': 20, 'method': 'item/tool/call', 'params': {'tool': 'exec', 'arguments': {}}}])
        self.assertFalse(error.exception.fallback)

    async def test_protocol_policy_error_and_eof(self):
        with self.assertRaises(GptFailure) as error:
            await self.protocol([{'method': 'turn/completed', 'params': {'turn': {'status': 'failed', 'error': {'message': 'safety filter'}}}}])
        self.assertFalse(error.exception.fallback)
        with self.assertRaises(GptFailure) as error:
            await self.protocol([])
        self.assertTrue(error.exception.fallback)


if __name__ == '__main__':
    unittest.main()
