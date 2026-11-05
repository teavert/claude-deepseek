import asyncio
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from aiohttp.test_utils import TestClient, TestServer
from server import Bridge, ROOT, read_protected_key

CONFIG = json.loads((ROOT.parent / "templates/gateway.example.json").read_text(encoding="utf-8-sig"))
CONFIG.update(primary_transport='translated', fallback_capacity_checks=False)
CONFIG.update(primary_model="deepseek-ai/DeepSeek-V4.1-Flash-1017", primary_base_url="https://api.deepinfra.com/v1/openai",
              support_base_url="https://openrouter.ai/api")
KEYS = {"primary": "di-test", "support": "or-test", "fallback": "am-test", "local": "local-test"}


class ProviderError(Exception):
    def __init__(self, status, message="server error"):
        super().__init__(message)
        self.status_code = status


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def case(self, error=None, support=False, stream=False):
        calls=[]
        async def translate(**args):
            calls.append(args)
            if args["api_key"] == "di-test" and error:
                raise error
            if stream:
                async def chunks():
                    yield {"type":"message_start","message":{"role":"assistant"}}
                    raise httpx.ReadTimeout("midstream")
                return chunks()
            return {"type":"message","content":[{"type":"text","text":"I cannot help with that."}]}
        def transport(req):
            self.assertEqual(req.url.host, "openrouter.ai")
            self.assertEqual(req.headers["authorization"], "Bearer or-test")
            return httpx.Response(429,json={"error":{"type":"rate_limit_error"}})
        bridge=Bridge(CONFIG,KEYS,translate=translate,client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        async with TestClient(TestServer(bridge.app())) as client:
            response=await client.post('/v1/messages',headers={'x-api-key':'local-test'},json={
                'model':CONFIG['support_model'] if support else CONFIG['primary_model'],
                'messages':[{'role':'user','content':'fixture'}], 'max_tokens':20,'stream':stream})
            data=await response.read()
        return response.status,data,calls

    async def test_primary_endpoint_and_credential(self):
        status,data,calls=await self.case()
        self.assertEqual(status,200)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['api_base'],CONFIG['primary_base_url'])
        self.assertEqual(calls[0]['model'],'openai/'+CONFIG['primary_model'])
        self.assertEqual(calls[0]['extra_body'], {'service_tier': 'priority', 'reasoning_effort': 'max'})
        self.assertIn(b'cannot help',data)

    async def test_transient_fallback(self):
        for code in [408,429,500,502,503,504,529]:
            with self.subTest(code=code):
                status,_,calls=await self.case(ProviderError(code))
                self.assertEqual(status,200)
                self.assertEqual([c['api_key'] for c in calls],['di-test','am-test'])
                self.assertNotIn('extra_body', calls[1])

    async def test_timeout_fallback(self):
        status,_,calls=await self.case(TimeoutError())
        self.assertEqual(status,200)
        self.assertEqual(len(calls),2)

    async def test_connection_fallback(self):
        status,_,calls=await self.case(httpx.ConnectError('fixture'))
        self.assertEqual(status,200)
        self.assertEqual(len(calls),2)

    async def test_non_transient_errors_pass_through(self):
        for code in [400,401,402,403,404,413,422]:
            status,_,calls=await self.case(ProviderError(code))
            self.assertEqual(status,code)
            self.assertEqual(len(calls),1)

    async def test_policy_error_never_falls_back(self):
        status,_,calls=await self.case(ProviderError(500,'content_policy_violation'))
        self.assertEqual(status,500)
        self.assertEqual(len(calls),1)

    async def test_kimi_keeps_openrouter_and_no_fallback(self):
        status,_,calls=await self.case(support=True)
        self.assertEqual(status,429)
        self.assertEqual(calls,[])

    async def test_midstream_never_replayed(self):
        status,data,calls=await self.case(stream=True)
        self.assertEqual(status,200)
        self.assertIn(b'not replayed',data)
        self.assertEqual(len(calls),1)



if __name__ == '__main__':
    unittest.main(verbosity=2)
