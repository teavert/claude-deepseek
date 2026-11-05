import asyncio
import json
import unittest
from unittest.mock import AsyncMock
import httpx
from aiohttp.test_utils import TestClient, TestServer
from native import Metrics, cache_key, retry_delay, safe_error
from server import Bridge, ROOT

CONFIG = json.loads((ROOT.parent/'templates/gateway.example.json').read_text())
KEYS = dict(local='local-fixture', primary='primary-fixture', fallback='fallback-fixture', unlid='unlid-fixture', allmodels='allmodels-fixture')
MODEL = CONFIG['primary_model']
RESPONSE = dict(type='message', role='assistant', model=MODEL,
                content=[{'type':'text','text':'OK'}], stop_reason='end_turn',
                usage={'input_tokens':10,'output_tokens':2,'cache_read_input_tokens':100,'service_tier':'priority'})


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, transport, body=None, config=None, path='/v1/messages'):
        self.records, self.fallbacks = [], []
        async def translate(**args):
            self.fallbacks.append(args)
            return RESPONSE
        bridge = Bridge({**CONFIG, 'primary_retries':0, 'fallback_capacity_checks':False, **(config or {})}, KEYS,
                        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)), translate=translate,
                        audit=lambda **r: self.records.append(r))
        async with TestClient(TestServer(bridge.app())) as client:
            response = await client.post(path, headers={'x-api-key':KEYS['local'],'x-claude-deepseek-run':'a'*32},
                                         json=body or dict(model=MODEL,messages=[{'role':'user','content':'fixture'}],max_tokens=100))
            return response.status, await response.read()

    async def test_native_fields_and_metrics(self):
        def upstream(request):
            self.assertEqual(str(request.url), 'https://api.deepinfra.com/anthropic/v1/messages')
            self.assertEqual(request.headers['x-api-key'], KEYS['primary'])
            b=json.loads(request.content)
            self.assertEqual(b['thinking'], {'type':'adaptive'})
            self.assertEqual(b['output_config']['effort'],'max')
            self.assertEqual(b['service_tier'],'priority')
            self.assertEqual(b['temperature'],0.3)
            self.assertEqual(b['max_tokens'],100)
            self.assertEqual(len(b['prompt_cache_key']),64)
            self.assertTrue(b['tools'][0]['defer_loading'])
            return httpx.Response(200,json=RESPONSE)
        code,_=await self.exercise(upstream,dict(model=MODEL,messages=[],max_tokens=100,temperature=0.3,tools=[{'name':'fixture','defer_loading':True}]))
        self.assertEqual(code,200)
        done=next(r for r in self.records if r['event']=='complete')
        self.assertEqual(done['usage']['cache_read_input_tokens'],100)
        self.assertIsNotNone(done['ttft_ms'])
        self.assertNotIn('primary-fixture',json.dumps(self.records))

    async def test_count_tokens(self):
        def upstream(request):
            self.assertTrue(request.url.path.endswith('/count_tokens'))
            self.assertNotIn('max_tokens', json.loads(request.content))
            return httpx.Response(200,json={'input_tokens':123})
        code,raw=await self.exercise(upstream,path='/v1/messages/count_tokens')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['input_tokens'],123)

    async def test_context_error_is_recognizable_without_echoing_content(self):
        code,raw=await self.exercise(lambda r:httpx.Response(400,json={'error':{'message':'context length exceeded: PRIVATE-PROMPT sk-secret'}}))
        self.assertEqual(code,400)
        self.assertIn(b'Prompt is too long',raw)
        self.assertNotIn(b'PRIVATE',raw)
        self.assertEqual(self.fallbacks,[])

    async def test_retry_then_success(self):
        calls=[]
        def upstream(request):
            calls.append(1)
            return httpx.Response(429,headers={'retry-after':'0'},json={'error':'busy'}) if len(calls)==1 else httpx.Response(200,json=RESPONSE)
        code,_=await self.exercise(upstream,config={'primary_retries':1})
        self.assertEqual(code,200)
        self.assertEqual(len(calls),2)
        self.assertEqual(self.fallbacks,[])

    async def test_transient_failure_uses_existing_chain(self):
        code,_=await self.exercise(lambda r:httpx.Response(503,json={'error':'busy'}))
        self.assertEqual(code,200)
        self.assertEqual(len(self.fallbacks),1)
        self.assertNotIn('extra_body',self.fallbacks[0])

    async def test_fallback_reference_conversion_preserves_tool_arguments(self):
        literal={'type':'tool_reference','defer_loading':True,'tool_name':'literal'}
        body=dict(model=MODEL,max_tokens=100,tools=[{'name':'lookup','defer_loading':True,'input_schema':{'properties':{'defer_loading':{'type':'boolean'}}}}],
                  messages=[{'role':'assistant','content':[{'type':'tool_use','id':'1','name':'lookup','input':literal}]},
                            {'role':'user','content':[{'type':'tool_result','tool_use_id':'1','content':[{'type':'tool_reference','tool_name':'lookup'}]}]}])
        code,_=await self.exercise(lambda r:httpx.Response(503,json={'error':'busy'}),body)
        self.assertEqual(code,200)
        sent=self.fallbacks[0]
        self.assertEqual(sent['messages'][0]['content'][0]['input'],literal)
        self.assertEqual(sent['messages'][1]['content'][0]['content'][0]['type'],'text')
        self.assertNotIn('defer_loading',sent['tools'][0])
        self.assertIn('defer_loading',sent['tools'][0]['input_schema']['properties'])

    async def test_policy_error_never_retries_or_falls_back(self):
        code,_=await self.exercise(lambda r:httpx.Response(500,json={'error':'content_policy_violation'}),config={'primary_retries':1})
        self.assertEqual(code,500)
        self.assertEqual(self.fallbacks,[])

    async def test_stream_usage_and_fragmentation(self):
        events=[{'type':'message_start','message':{'usage':{'input_tokens':10,'cache_read_input_tokens':90}}},
                {'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'private reasoning'}},
                {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':20}}, {'type':'message_stop'}]
        raw=b''.join(('data: '+json.dumps(e)+'\n\n').encode() for e in events)
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for i in range(0,len(raw),7): yield raw[i:i+7]
        code,received=await self.exercise(lambda r:httpx.Response(200,stream=Stream()),dict(model=MODEL,messages=[],max_tokens=100,stream=True))
        self.assertEqual(code,200)
        self.assertEqual(received,raw)
        done=next(r for r in self.records if r['event']=='complete')
        self.assertEqual(done['usage']['output_tokens'],20)
        self.assertEqual(done['usage']['cache_read_input_tokens'],90)
        self.assertNotIn('private reasoning',json.dumps(self.records))

    async def test_midstream_failure_not_replayed(self):
        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"message_start"}\n\n'
                raise httpx.ReadTimeout('fixture')
        code,raw=await self.exercise(lambda r:httpx.Response(200,stream=Broken()),dict(model=MODEL,messages=[],max_tokens=100,stream=True))
        self.assertEqual(code,200)
        self.assertIn(b'not replayed',raw)
        self.assertEqual(self.fallbacks,[])

    async def test_fallback_capacity_skips_unknown_small_route(self):
        def upstream(request):
            if request.url.path.endswith('count_tokens'):
                return httpx.Response(200,json={'input_tokens':150000})
            return httpx.Response(503,json={'error':'busy'})
        code,_=await self.exercise(upstream,config={'fallback_capacity_checks':True})
        self.assertEqual(code,200)
        self.assertEqual(self.fallbacks[0]['api_key'],KEYS['unlid'])
        self.assertTrue(any(r['event']=='fallback_skipped' for r in self.records))
        self.assertEqual(next(r for r in self.records if r['event']=='fallback')['from_route'],'deepinfra')

    async def test_unavailable_count_uses_conservative_capacity_guard(self):
        def upstream(request):
            return httpx.Response(503,json={'error':'busy'})
        code,_=await self.exercise(upstream,dict(model=MODEL,max_tokens=100,messages=[{'role':'user','content':'x'*150000}]),
                                   config={'fallback_capacity_checks':True})
        self.assertEqual(code,200)
        self.assertEqual(self.fallbacks[0]['api_key'],KEYS['unlid'])

    async def test_excessive_retry_after_moves_on_without_early_retry(self):
        calls=[]
        def upstream(request):
            calls.append(1)
            return httpx.Response(429,headers={'retry-after':'60'},json={'error':'busy'})
        code,_=await self.exercise(upstream,config={'primary_retries':1})
        self.assertEqual(code,200)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.fallbacks),1)

    async def test_timeout_uses_existing_fallback(self):
        def upstream(request):
            raise httpx.ConnectTimeout('fixture')
        code,_=await self.exercise(upstream)
        self.assertEqual(code,200)
        self.assertEqual(len(self.fallbacks),1)

    async def test_oversize_request_never_sent_to_fallback(self):
        def upstream(request):
            return httpx.Response(200,json={'input_tokens':1048000}) if request.url.path.endswith('count_tokens') else httpx.Response(503,json={'error':'busy'})
        code,_=await self.exercise(upstream,config={'fallback_capacity_checks':True})
        self.assertEqual(code,400)
        self.assertEqual(self.fallbacks,[])

    async def test_cancel_pending_request(self):
        started=asyncio.Event()
        async def upstream(request):
            started.set()
            await asyncio.Event().wait()
        bridge=Bridge(CONFIG,KEYS,client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
        bridge.fallback=AsyncMock()
        class Request: headers={}
        task=asyncio.create_task(bridge.native(Request(),dict(model=MODEL,messages=[],max_tokens=100),'id','a'*32))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        bridge.fallback.assert_not_called()
        await bridge.client.aclose()


class Helpers(unittest.TestCase):
    def test_cache_identity(self):
        self.assertEqual(cache_key('a',{'messages':['one']}),cache_key('a',{'messages':['two']}))
        self.assertNotEqual(cache_key('a',{}),cache_key('b',{}))
        self.assertIsNone(cache_key('',{}))

    def test_retry_budget(self):
        self.assertIsNone(retry_delay({'retry-after':'60'},10))
        self.assertEqual(retry_delay({'retry-after':'3'},10),3)

    def test_error_classification(self):
        self.assertEqual(safe_error(401,'secret')['error']['type'],'authentication_error')
        self.assertIn('max_tokens',safe_error(422,'max_tokens bad')['error']['message'])


if __name__=='__main__': unittest.main()
