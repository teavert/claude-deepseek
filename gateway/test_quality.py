"""Exercise the installed LiteLLM translator against a local OpenAI endpoint."""
import json
import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from server import Bridge, ROOT

CONFIG = json.loads((ROOT.parent / 'templates/gateway.example.json').read_text())
CONFIG.update(primary_transport='translated', fallback_capacity_checks=False)


class QualityTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, stream=False, overrides=None, history=False):
        captured = []

        async def upstream(request):
            body = await request.json()
            captured.append(body)
            message = {'role': 'assistant', 'content': 'OK', 'reasoning_content': 'Checked.'}
            result = {'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 1,
                      'model': CONFIG['primary_model'],
                      'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
                      'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
            if body.get('stream'):
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                for delta, reason in [(message, None), ({}, 'stop')]:
                    chunk = dict(result, object='chat.completion.chunk',
                                 choices=[{'index': 0, 'delta': delta, 'finish_reason': reason}])
                    await response.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                await response.write(b'data: [DONE]\n\n')
                return response
            return web.json_response(result)

        app = web.Application()
        app.router.add_post('/v1/chat/completions', upstream)
        async with TestServer(app) as provider:
            config = dict(CONFIG, primary_base_url=str(provider.make_url('/v1')))
            bridge = Bridge(config, {'local': 'test', 'primary': 'fake'})
            async with TestClient(TestServer(bridge.app())) as client:
                health = await (await client.get('/health', headers={'x-api-key': 'test'})).json()
                self.assertEqual(health['primary_reasoning_effort'], 'max')
                messages = [{'role': 'user', 'content': 'Check fixture'}]
                if history:
                    messages += [
                        {'role': 'assistant', 'content': [
                            {'type': 'thinking', 'thinking': 'Check the tool.', 'signature': ''},
                            {'type': 'tool_use', 'id': 'call_1', 'name': 'lookup', 'input': {'name': 'fixture'}}]},
                        {'role': 'user', 'content': [
                            {'type': 'tool_result', 'tool_use_id': 'call_1', 'content': '42'}]}]
                body = dict(model=CONFIG['primary_model'], messages=messages,
                            max_tokens=1234, stream=stream,
                            thinking={'type': 'enabled', 'budget_tokens': 1024},
                            output_config={'effort': 'low'},
                            tools=[{'name': 'lookup', 'description': 'Lookup',
                                    'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}}}])
                body.update(overrides or {})
                response = await client.post('/v1/messages', headers={'x-api-key': 'test'}, json=body)
                raw = await response.read()
                self.assertEqual(response.status, 200, raw)
        self.assertEqual(len(captured), 1)
        sent = captured[0]
        self.assertEqual(sent['model'], CONFIG['primary_model'])
        self.assertEqual(sent['reasoning_effort'], 'max')
        self.assertIs(type(sent['reasoning_effort']), str)
        self.assertEqual(sent['service_tier'], 'priority')
        self.assertEqual(sent.get('max_tokens', sent.get('max_completion_tokens')), 1234)
        self.assertNotIn('thinking', sent)
        self.assertNotIn('output_config', sent)
        self.assertEqual(sent['temperature'], (overrides or {}).get('temperature', 1.0))
        self.assertEqual(sent['top_p'], (overrides or {}).get('top_p', 0.95))
        self.assertEqual(sent['tools'][0]['function']['name'], 'lookup')
        if history:
            assistant = next(m for m in sent['messages'] if m['role'] == 'assistant')
            self.assertEqual(assistant['tool_calls'][0]['id'], 'call_1')
            self.assertEqual(assistant['reasoning_content'], 'Check the tool.')
            self.assertEqual(sent['messages'][-1]['tool_call_id'], 'call_1')
        if stream:
            self.assertIn(b'message_stop', raw)
            self.assertIn(b'OK', raw)
        else:
            self.assertTrue(any(b.get('text') == 'OK' for b in json.loads(raw)['content']))

    async def test_defaults_and_history(self):
        await self.exercise(history=True)

    async def test_streaming_and_explicit_sampling(self):
        await self.exercise(stream=True, overrides={'temperature': 0.3, 'top_p': 0.8})


if __name__ == '__main__':
    unittest.main()
