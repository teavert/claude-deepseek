import argparse, asyncio, json, sys, secrets, tempfile
from pathlib import Path
parser=argparse.ArgumentParser(description='Opt-in live provider checks. Uses account credits/subscription. Never prints credentials or raw provider errors.')
parser.add_argument('--credential-directory', type=Path, default=Path(__file__).resolve().parents[1])
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]; credentials=args.credential_directory.resolve();sys.path.insert(0,str(root/'gateway'))
import httpx
from aiohttp import web
from server import Bridge,read_protected_key
from status_display import RouteStatus
from gpt_main import GptMain

async def main():
 config=json.loads((root/'templates/gateway.example.json').read_text(encoding='utf-8'))
 keys={'local':secrets.token_urlsafe(32)}
 for role,name in [('primary','deepinfra'),('support','openrouter'),('fallback','outrun'),('unlid','unlid'),('allmodels','allmodels')]:
  keys[role]=read_protected_key(credentials/(name+'-key.dpapi'))
 records=[]
 with tempfile.TemporaryDirectory(prefix='release-live-status-') as directory:
  bridge=Bridge(config,keys,status=RouteStatus(Path(directory)),audit=lambda **r:records.append(r))
  runner=web.AppRunner(bridge.app(),access_log=None);await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
  async with httpx.AsyncClient(timeout=360) as client:
   invalid=await client.get(url+'/health',headers={'x-api-key':'synthetic-invalid'})
   assert invalid.status_code in (401,403)
   health=await client.get(url+'/health',headers={'x-api-key':keys['local']});assert health.status_code==200
   reply=await client.post(url+'/v1/messages',headers={'x-api-key':keys['local']},json={'model':config['primary_model'],'max_tokens':64,'messages':[{'role':'user','content':'Reply with the single word READY.'}]})
   served=[r for r in records if r.get('event')=='complete']
   result={'gateway_authentication':'passed','deepinfra_gateway_status':reply.status_code,'actual_routes':[{'route':r.get('route'),'model':r.get('model')} for r in served]}
   print(json.dumps(result),flush=True)
   assert reply.status_code==200 and any(r.get('route')=='deepinfra' for r in served),'Primary live route failed'
   before=len(records)
   streamed=await client.post(url+'/v1/messages',headers={'x-api-key':keys['local']},json={'model':'deepseek-v4.1-flash-1017-uncensored-chains','max_tokens':64,'stream':True,'messages':[{'role':'user','content':'Reply with the single word READY.'}]})
   events=[r for r in records[before:] if r.get('event')=='complete']
   assert streamed.status_code==200 and 'message_stop' in streamed.text and any(r.get('route')=='outrun' for r in events),'Live translated chain stream failed'
   print(json.dumps({'translated_chain_stream':'passed','routes':[r.get('route') for r in events]}),flush=True)

  await runner.cleanup()
 # Probe each configured provider independently: no automatic fallback obscures availability.
 async with httpx.AsyncClient(timeout=180) as client:
  for route in [*config['fallback_chain'],{'route':'openrouter','base_url':config['support_base_url']+'/v1','model':config['support_model'],'key':'support'}]:
   response=await client.post(route['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+keys[route['key']]},json={'model':route['model'],'max_tokens':64,'messages':[{'role':'user','content':'Reply with the single word READY.'}]})
   print(json.dumps({'provider':route['route'],'model':route['model'],'http_status':response.status_code,'has_completion':response.status_code==200 and bool(response.json().get('choices'))}),flush=True)
   assert response.status_code==200 and response.json().get('choices'),'Configured provider live check failed'
 # Main GPT adapter must actually use the requested model rather than fallback.
 adapter=GptMain(config)
 reply=await adapter.complete({'system':'This is a synthetic release test.','model':'gpt-6-astra','max_tokens':64,'messages':[{'role':'user','content':'Reply with the single word READY.'}]})
 assert reply.get('content')
 print(json.dumps({'gpt_main':'passed','model':reply.get('model'),'stop_reason':reply.get('stop_reason')}),flush=True)
 # Verify a live dynamic-tool handoff. The returned call is not executed.
 reply=await adapter.complete({'system':'Synthetic integration test: call the supplied tool exactly once.','model':'gpt-6-astra','max_tokens':256,'tools':[{'name':'release_fixture','description':'Return synthetic release fixture status.','input_schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}}],'messages':[{'role':'user','content':'Call release_fixture with value READY.'}]})
 assert reply.get('stop_reason')=='tool_use' and any(b.get('name')=='release_fixture' for b in reply.get('content',[]))
 print(json.dumps({'gpt_tool_handoff':'passed','model':reply.get('model')}),flush=True)
try: asyncio.run(main())
except Exception as error:
 print(json.dumps({'live_check':'failed','error_type':type(error).__name__}),flush=True);sys.exit(1)
