import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
// Copy the bridge under the same package so its installed dependencies resolve.
const dir = fs.mkdtempSync(path.join(root, '.queue-test-'));
const python = path.join(root, '..', 'gateway', 'venv', 'Scripts', 'python.exe');
const runner = path.join(dir, 'runner.py');
const clients = [], holders = [];
const pause = ms => new Promise(r => setTimeout(r, ms));
const parsed = result => JSON.parse(result.content[0].text);
try {
  fs.copyFileSync(path.join(root, '..', 'codex-workers', 'runner.py'), runner);
  fs.copyFileSync(path.join(root, 'policy.mjs'), path.join(dir, 'policy.mjs'));
  fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify({ ...JSON.parse(fs.readFileSync(path.join(root, 'config.example.json'))), codexPath: python }));
  fs.writeFileSync(path.join(dir, 'server.mjs'), fs.readFileSync(path.join(root, 'server.mjs'), 'utf8')
    .replace("path.join(root, '..', 'codex-workers', 'runner.py')", JSON.stringify(runner))
    .replace("path.join(root, '..', 'gateway', 'venv', 'Scripts', 'python.exe')", JSON.stringify(python)));
  for (let i = 0; i < 2; i++) {
    const status = path.join(dir, `hold-${i}.json`);
    const child = spawn(python, [runner, '--status', status, '--timeout', '30', '--', python, '-c', 'import time; time.sleep(30)'], { windowsHide: true, stdio: 'ignore' });
    holders.push(child);
    const deadline = Date.now() + 10000;
    while ((!fs.existsSync(status) || JSON.parse(fs.readFileSync(status)).state !== 'running') && Date.now() < deadline) await pause(30);
    assert.equal(JSON.parse(fs.readFileSync(status)).state, 'running');
  }
  const emptyHome = path.join(dir, 'empty-home');
  fs.mkdirSync(emptyHome);
  for (let i = 0; i < 2; i++) {
    const client = new Client({ name: `queue-test-${i}`, version: '1.0.0' });
    await client.connect(new StdioClientTransport({ command: process.execPath, args: [path.join(dir, 'server.mjs')], env: { ...process.env, CODEX_HOME: emptyHome } }));
    clients.push(client);
  }
  const input = { purpose: 'benign_development', authorization: 'The user authorized this local queue test.', scope: 'Synthetic local queue test only; no external actions.', task: 'Return READY', cwd: os.tmpdir() };
  const a = parsed(await clients[0].callTool({ name: 'delegate', arguments: input }));
  const b = parsed(await clients[1].callTool({ name: 'delegate', arguments: input }));
  const c = parsed(await clients[0].callTool({ name: 'delegate', arguments: input }));
  await pause(300);
  for (const [client, job] of [[clients[0], a], [clients[1], b], [clients[0], c]]) {
    assert.equal(parsed(await client.callTool({ name: 'result', arguments: { job_id: job.job_id, wait_seconds: 0 } })).status, 'queued');
  }
  assert.equal(parsed(await clients[0].callTool({ name: 'cancel', arguments: { job_id: c.job_id } })).status, 'cancelled');
  // Disconnecting a separate session removes its queued work too.
  await clients[1].close();
  for (const child of holders) { const closed = once(child, 'close'); child.kill(); await closed; }
  const result = parsed(await clients[0].callTool({ name: 'result', arguments: { job_id: a.job_id, wait_seconds: 25 } }));
  assert.equal(result.status, 'failed');
  assert.match(result.error, /subscription login required/);
  assert.equal(parsed(await clients[0].callTool({ name: 'result', arguments: { job_id: c.job_id, wait_seconds: 0 } })).status, 'cancelled');
  console.log('PASS: two MCP sessions share the queue, return queued IDs, cancel queued jobs, and enforce subscription authentication after admission.');
} finally {
  for (const client of clients) await client.close();
  for (const child of holders) if (child.exitCode === null) child.kill();
  await pause(1200);
  fs.rmSync(dir, { recursive: true, force: true });
}
