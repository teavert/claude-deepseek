import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
const cwd = process.argv[2];
const model = process.argv[3];
const client = new Client({ name: 'subscription-integration-check', version: '1.0.0' });
const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'server.mjs')], env: { ...process.env, OPENAI_API_KEY: 'sentinel-must-never-be-used', OPENAI_BASE_URL: 'http://127.0.0.1:1', ANTHROPIC_AUTH_TOKEN: 'sentinel' } });
try {
  await client.connect(transport);
  const parsed = result => JSON.parse(result.content[0].text);
  const tools = await client.listTools();
  assert.equal(tools.tools.length, 4);
  const status = parsed(await client.callTool({ name: 'status', arguments: {} }));
  assert.equal(status.auth, 'chatgpt');
  const badPath = await client.callTool({ name: 'delegate', arguments: { task: 'test', cwd: 'relative-path' } });
  assert.equal(badPath.isError, true);
  const missing = await client.callTool({ name: 'result', arguments: { job_id: '00000000-0000-4000-8000-000000000000', wait_seconds: 0 } });
  assert.equal(missing.isError, true);
  let result = parsed(await client.callTool({ name: 'delegate', arguments: { purpose: 'benign_development', authorization: 'The user authorized this synthetic local installation test.', scope: 'Read only fixture.json in the supplied synthetic fixture directory; no network actions.', task: 'Read fixture.json in this directory. Add the integers in values. Return exactly nonce|sum using the file data, with no other words. Do not modify files.', cwd, ...(model ? { model } : {}) } }));
  assert.ok(result.job_id, JSON.stringify(result));
  console.log(JSON.stringify({ status: 'started', job_id: result.job_id, auth: result.auth }));
  for (let i = 0; ['queued', 'running'].includes(result.status) && i < 24; i++) result = parsed(await client.callTool({ name: 'result', arguments: { job_id: result.job_id, wait_seconds: 25 } }));
  console.log(JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(cwd, model ? model + '-verification.json' : 'bridge-verification.json'), JSON.stringify(result, null, 2));
  assert.equal(result.status, 'completed');
  assert.equal(result.result.trim(), 'CEDAR-5812|83');
  assert.equal(result.auth, 'chatgpt');
  assert.equal(result.reasoning_effort, 'high');
  if (model) assert.equal(result.model, model);
  assert.equal(result.sandbox, 'read-only');
  console.log('PASS: subscription login, API-environment isolation, validation, project read, and returned result.');
} finally { await client.close(); }
