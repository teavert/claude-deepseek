import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
const testDir = process.argv[2];
const emptyHome = fs.mkdtempSync(path.join(testDir, 'empty-codex-home-'));
const client = new Client({ name: 'auth-boundary-check', version: '1.0.0' });
try {
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'server.mjs')], env: { ...process.env, CODEX_HOME: emptyHome, OPENAI_API_KEY: 'test-api-key-must-not-be-used' } }));
  const status = await client.callTool({ name: 'status', arguments: {} });
  assert.equal(status.isError, true);
  let refused = await client.callTool({ name: 'delegate', arguments: { purpose: 'benign_development', authorization: 'The user authorized a synthetic local auth test.', scope: 'This temporary local test directory only; no external actions.', task: 'Return READY', cwd: testDir } });
  let job = JSON.parse(refused.content[0].text);
  while (['queued', 'running'].includes(job.status)) {
    refused = await client.callTool({ name: 'result', arguments: { job_id: job.job_id, wait_seconds: 25 } });
    job = JSON.parse(refused.content[0].text);
  }
  assert.equal(refused.isError, true);
  assert.match(refused.content[0].text, /subscription login required/);
  assert.doesNotMatch(refused.content[0].text, /test-api-key-must-not-be-used/);
  console.log('PASS: missing subscription login refuses delegation even when an API-key variable is present. Existing login untouched.');
} finally { await client.close(); }
