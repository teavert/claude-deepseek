import { validateReview, reviewInstructions, purposes } from './policy.mjs';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(fs.readFileSync(path.join(root, 'config.json'), 'utf8').replace(/^\uFEFF/, ''));
const jobs = new Map();
const workerRunner = path.join(root, '..', 'codex-workers', 'runner.py');
const workerPython = path.join(root, '..', 'gateway', 'venv', 'Scripts', 'python.exe');
function supervised(command, statusFile, timeout, options, requireChatGPT = false) {
  return spawn(workerPython, [workerRunner, '--status', statusFile, '--timeout', String(timeout), ...(requireChatGPT ? ['--require-chatgpt'] : []), '--', ...command], options);
}
const jobRoot = path.join(root, 'jobs');
fs.mkdirSync(jobRoot, { recursive: true });
const redact = value => String(value).replace(/\b(?:sk-|ul-)[A-Za-z0-9_-]{15,}/g, '[REDACTED]');
const response = (value, error = false) => ({ content: [{ type: 'text', text: JSON.stringify(value, null, 2) }], isError: error });

function childEnv() {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(OPENAI_|ANTHROPIC_|UNLID_|CLAUDE_|CODEX_API_KEY$|CODEX_ACCESS_TOKEN$|CODEX_THREAD_ID$|CODEX_TURN_ID$)/i.test(key)) delete env[key];
  }
  return env;
}
function executable() {
  // Prefer the known installation; fall back to the current PATH after an app update.
  if (fs.existsSync(config.codexPath)) return config.codexPath;
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    const candidate = path.join(dir, 'codex.exe');
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error('Codex CLI is missing. Open Codex or repair its CLI installation.');
}
function terminate(child) {
  if (!child?.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    killer.on('error', () => child.kill());
  } else child.kill('SIGTERM');
}
function loginStatus() {
  return new Promise((resolve, reject) => {
    const statusDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-login-'));
    const child = supervised([executable(), 'login', 'status'], path.join(statusDir, 'worker.json'), 15, { env: childEnv(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    // The supervisor starts the login timeout after queue admission.

    for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { output = (output + chunk).slice(-16000); });
    child.once('error', reject);
    child.once('close', code => {
      fs.rmSync(statusDir, { recursive: true, force: true });
      if (code === 0 && /Logged in using ChatGPT/i.test(output)) resolve('chatgpt');
      else reject(new Error('ChatGPT subscription login required. Run codex login in your terminal. This bridge does not fall back to an API key.'));
    });
  });
}
function metadata(job) {
  return { job_id: job.id, status: job.status, auth: 'chatgpt', provider: 'openai', model: job.model, reasoning_effort: config.reasoningEffort, purpose: job.purpose, sandbox: job.sandbox, cwd: job.cwd, queued_at: job.queuedAt, started_at: job.startedAt, finished_at: job.finishedAt, exit_code: job.exitCode, codex_thread_id: job.threadId, tool_events: job.toolEvents, usage: job.usage, error: job.error, result: job.result };
}
function save(job) {
  fs.writeFileSync(path.join(job.dir, 'result.json'), JSON.stringify(metadata(job), null, 2));
}
function startJob(input) {
  const cwd = fs.realpathSync(input.cwd);
  if (!fs.statSync(cwd).isDirectory()) throw new Error('cwd must name an existing project directory.');
  if ([os.homedir(), root, path.dirname(root)].some(p => path.resolve(p).toLowerCase() === cwd.toLowerCase())) throw new Error('Choose the target project directory, not your home or the credential directory.');
  const job = { id: crypto.randomUUID(), status: 'queued', model: input.model || config.model, sandbox: 'read-only', cwd, purpose: input.purpose, queuedAt: new Date().toISOString(), startedAt: null, result: '', error: '', usage: null, threadId: null, toolEvents: [] };
  job.dir = path.join(jobRoot, job.id);
  fs.mkdirSync(job.dir);
  const argv = ['exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--sandbox', job.sandbox,
    '--disable', 'multi_agent', '-c', 'windows.sandbox="unelevated"', '-c', 'forced_login_method="chatgpt"', '-c', 'model_provider="openai"', '-c', 'approval_policy="never"',
    '-c', `model_reasoning_effort=${JSON.stringify(config.reasoningEffort)}`, '-m', job.model, '--json', '-'];
  // No shell interpolation. Task content is sent exclusively on stdin.
  job.workerStatus = path.join(job.dir, 'worker.json');
  const child = supervised([executable(), ...argv], job.workerStatus, config.timeoutSeconds, { cwd, env: childEnv(), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] }, true);
  job.child = child;
  jobs.set(job.id, job);
  let buffer = '', diagnostics = '', terminalSeen = false, finished = false;
  function refreshWorker() {
    let state;
    try { state = JSON.parse(fs.readFileSync(job.workerStatus, 'utf8')).state; } catch { return; }
    if (state === 'failed_auth' && ['queued', 'running'].includes(job.status)) { job.status = 'failed'; job.error = 'ChatGPT subscription login required. Run codex login in your terminal. This bridge does not fall back to an API key.'; }
    if (state === 'running' && job.status === 'queued') { job.status = 'running'; job.startedAt = new Date().toISOString(); save(job); }
    if (state === 'timed_out' && ['queued', 'running'].includes(job.status)) { job.status = 'timed_out'; job.error = 'Job exceeded its time limit.'; }
  }
  const timer = setInterval(refreshWorker, 100);
  const event = line => {
    let item; try { item = JSON.parse(line); } catch { return; }
    if (item.type === 'thread.started') job.threadId = item.thread_id;
    if (item.type === 'item.completed' && ['command_execution', 'file_change', 'mcp_tool_call'].includes(item.item?.type)) job.toolEvents.push({ type: item.item.type, status: item.item.status, exit_code: item.item.exit_code });
    if (item.type === 'item.completed' && item.item?.type === 'agent_message') job.result = redact(item.item.text);
    if (item.type === 'turn.completed') { terminalSeen = true; job.usage = item.usage; }
    if (item.type === 'turn.failed') job.error = redact(item.error?.message || JSON.stringify(item.error));
    if (item.type === 'error') diagnostics = redact(item.message || JSON.stringify(item)).slice(-8000);
  };
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', text => {
    buffer += text;
    let next;
    while ((next = buffer.indexOf('\n')) >= 0) { event(buffer.slice(0, next)); buffer = buffer.slice(next + 1); }
    if (buffer.length > 8 * 1024 * 1024) { job.error = 'Unexpectedly large Codex event.'; terminate(child); }
  });
  child.stderr.on('data', text => { diagnostics = redact(diagnostics + text).slice(-8000); });
  function finish(code, error) {
    if (finished) return;
    finished = true;
    clearInterval(timer);
    refreshWorker();
    if (buffer) event(buffer);
    job.exitCode = code;
    job.finishedAt = new Date().toISOString();
    if (error) job.error = redact(error.message);
    if (['queued', 'running'].includes(job.status)) job.status = code === 0 && terminalSeen && !job.error && job.result ? 'completed' : 'failed';
    if (job.status === 'failed' && !job.error) job.error = diagnostics || 'Codex ended without a final result.';
    save(job);
  }
  child.once('error', error => finish(null, error));
  child.once('close', code => finish(code));
  child.stdin.on('error', () => {});
  child.stdin.end(`${reviewInstructions}\nPurpose: ${input.purpose}\nAuthorization: ${input.authorization}\nScope: ${input.scope}\n\nYou are a GPT specialist delegated a bounded task by Claude Code. Stay within the supplied project and scope. Do not spawn another coding agent or alter agent configuration. Do not access credentials. Return the result, supporting evidence, and any limitations. This is a read-only job. Do not modify project files. For requested changes, return a precise patch or implementation instructions for Claude Code to apply.\n\nTASK:\n${input.task}\n`);
  save(job);
  return job;
}

const server = new McpServer({ name: 'codex-subscription', version: '1.0.0' });
server.registerTool('status', {
  description: 'Check whether the local Codex CLI is signed in with ChatGPT for subscription-backed GPT jobs. Never returns credentials.',
  inputSchema: {}, annotations: { readOnlyHint: true }
}, async () => {
  try { return response({ auth: await loginStatus(), provider: 'openai', default_model: config.model, reasoning_effort: config.reasoningEffort, prompt_preflight: true, worker_limit: 2, active_jobs: [...jobs.values()].filter(j => j.status === 'running').length, queued_jobs: [...jobs.values()].filter(j => j.status === 'queued').length }); }
  catch (error) { return response({ error: error.message }, true); }
});
server.registerTool('delegate', {
  description: 'Delegate a bounded read-only GPT task to the signed-in Codex CLI using ChatGPT subscription access. Returns a queued job ID (two workers shared with the GPT gateway); call result with wait_seconds=25 until completed. GPT reviews, plans, or drafts patches; Claude applies requested edits. No API-key fallback. Input and relevant project context go to OpenAI.',
  inputSchema: { purpose: z.enum(purposes), authorization: z.string().min(10).max(4000), scope: z.string().min(10).max(4000), task: z.string().min(1).max(100000), cwd: z.string().min(1).describe('Absolute path of the target project directory'), model: z.string().regex(/^gpt-[a-z0-9._-]+$/).optional().describe('Omit to use the configured GPT model; specify only if requested.') }
}, async input => {
  try {
    if (!path.isAbsolute(input.cwd)) throw new Error('cwd must be an absolute project path.');
    validateReview(input);
    // Authentication is enforced by the admitted Codex worker. Return a queued
    // job immediately, without starting a separate login process per request.
    return response(metadata(startJob(input)));
  } catch (error) { return response({ error: redact(error.message) }, true); }
});
server.registerTool('result', {
  description: 'Get a GPT delegation result. While queued or running, use wait_seconds=25 to wait without rapid polling. A failed job is not a successful GPT answer.',
  inputSchema: { job_id: z.string().uuid(), wait_seconds: z.number().int().min(0).max(25).default(25) }, annotations: { readOnlyHint: true }
}, async ({ job_id, wait_seconds }) => {
  const job = jobs.get(job_id);
  if (!job) return response({ error: 'Unknown job ID in this Claude session.' }, true);
  const deadline = Date.now() + wait_seconds * 1000;
  while (['queued', 'running'].includes(job.status) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 250));
  return response(metadata(job), ['failed', 'timed_out', 'cancelled'].includes(job.status));
});
server.registerTool('cancel', {
  description: 'Cancel a GPT job started by this Claude session.', inputSchema: { job_id: z.string().uuid() }
}, async ({ job_id }) => {
  const job = jobs.get(job_id);
  if (!job) return response({ error: 'Unknown job ID.' }, true);
  if (['queued', 'running'].includes(job.status)) { job.status = 'cancelled'; job.error = 'Cancelled by caller.'; terminate(job.child); save(job); }
  return response(metadata(job));
});
function shutdown() {
  for (const job of jobs.values()) if (['queued', 'running'].includes(job.status)) { job.status = 'cancelled'; job.error = 'Claude session disconnected.'; terminate(job.child); save(job); }
}
process.stdin.once('end', () => { shutdown(); setTimeout(() => process.exit(0), 1000).unref(); });
process.once('SIGINT', () => { shutdown(); setTimeout(() => process.exit(0), 1000).unref(); });
process.once('SIGTERM', () => { shutdown(); setTimeout(() => process.exit(0), 1000).unref(); });
await server.connect(new StdioServerTransport());
