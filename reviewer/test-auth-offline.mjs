import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const dir=fs.mkdtempSync(path.join(root,'.queue-test-auth-'));
const project=fs.mkdtempSync(path.join(os.tmpdir(),'auth-fixture-'));
const python=path.join(root,'..','gateway','venv','Scripts','python.exe');
const runner=path.join(root,'..','codex-workers','runner.py');
const client=new Client({name:'offline-auth',version:'1.0.0'});
try {
  fs.copyFileSync(path.join(root,'policy.mjs'),path.join(dir,'policy.mjs'));
  fs.writeFileSync(path.join(dir,'config.json'),JSON.stringify({codexPath:python,model:'fixture',reasoningEffort:'high',timeoutSeconds:20}));
  fs.writeFileSync(path.join(dir,'server.mjs'),fs.readFileSync(path.join(root,'server.mjs'),'utf8')
    .replace("path.join(root, '..', 'codex-workers', 'runner.py')",JSON.stringify(runner))
    .replace("path.join(root, '..', 'gateway', 'venv', 'Scripts', 'python.exe')",JSON.stringify(python)));
  await client.connect(new StdioClientTransport({command:process.execPath,args:[path.join(dir,'server.mjs')],env:{...process.env,OPENAI_API_KEY:'synthetic-not-a-real-key'}}));
  const status=await client.callTool({name:'status',arguments:{}});
  assert.equal(status.isError,true);
  assert.match(status.content[0].text,/subscription login required/);
  const reply=await client.callTool({name:'delegate',arguments:{purpose:'benign_development',authorization:'User authorized synthetic offline authentication tests.',scope:'Synthetic fixtures only; no provider network calls.',task:'Return READY',cwd:project}});
  let job=JSON.parse(reply.content[0].text);
  while(['queued','running'].includes(job.status)) job=JSON.parse((await client.callTool({name:'result',arguments:{job_id:job.job_id,wait_seconds:25}})).content[0].text);
  assert.equal(job.status,'failed',JSON.stringify(job));
  assert.match(job.error,/subscription login required/);
  assert.doesNotMatch(JSON.stringify(job),/synthetic-not-a-real-key/);
  console.log('PASS: offline authentication fails closed without exposing credentials or contacting Codex.');
} finally { await client.close(); await new Promise(r=>setTimeout(r,1000)); fs.rmSync(dir,{recursive:true,force:true}); fs.rmSync(project,{recursive:true,force:true}); }
