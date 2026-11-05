"""DeepInfra Messages transport and metadata-only diagnostics."""
import asyncio
import contextlib
import hashlib
import json
import re
import time
from email.utils import parsedate_to_datetime

import httpx
from aiohttp import web


def safe_error(status, payload):
    """Classify errors without reflecting prompts, headers, or provider secrets."""
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', 'replace')[:65536]
    text = str(payload).lower()
    kind = 'api_error'
    if status in (400, 413, 422):
        kind = 'invalid_request_error'
        if any(x in text for x in ('context length', 'context_length', 'context window', 'too many tokens', 'prompt is too long')):
            message = 'Prompt is too long: input plus requested output exceeds the provider context window. Compact the conversation or reduce the requested output.'
        elif 'max_tokens' in text or 'max_completion_tokens' in text:
            message = 'max_tokens is outside the provider output limits. Use a positive output budget within this model\'s supported limit.'
        elif 'tool_reference' in text or 'defer_loading' in text:
            message = 'Unsupported tool_reference or defer_loading: this route does not support deferred tool loading.'
        elif 'reasoning' in text or 'thinking' in text or 'effort' in text:
            message = 'Unsupported reasoning/thinking configuration. Check the provider-specific effort and thinking settings.'
        else:
            message = 'Invalid provider request. Check message content, tool schemas, and supported parameters.'
    elif status == 401:
        kind, message = 'authentication_error', 'Provider authentication failed. Check the configured credential.'
    elif status in (402, 403):
        kind, message = 'permission_error', 'Provider denied access. Check permissions and account balance.'
    elif status == 404:
        kind, message = 'not_found_error', 'The configured provider model or endpoint was not found.'
    elif status == 429:
        kind, message = 'rate_limit_error', 'Provider capacity or rate limit reached. Retry after the advertised delay.'
    else:
        message = 'Provider unavailable or connection interrupted. Retry this request.'
    return {'type': 'error', 'error': {'type': kind, 'message': message}}


class Metrics:
    def __init__(self):
        self.start = time.monotonic()
        self.first = None
        self.usage = {}
        self.stop = None
        self.buffer = b''
        self.discard_line = False

    def event(self, event):
        if not isinstance(event, dict):
            return
        message = event.get('message', {})
        usage = event.get('usage') or message.get('usage') or {}
        for name in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
            value = usage.get(name)
            if type(value) is int and value >= 0:
                self.usage[name] = value
        if usage.get('service_tier') in ('standard', 'priority', 'flex'):
            self.usage['service_tier'] = usage['service_tier']
        delta = event.get('delta', {})
        if self.first is None and (event.get('content') or any(delta.get(k) for k in ('text', 'thinking', 'partial_json'))):
            self.first = time.monotonic() - self.start
        stop = event.get('stop_reason') or delta.get('stop_reason')
        if stop in ('end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal'):
            self.stop = stop

    def feed(self, chunk):
        # Parse one SSE data line at a time. Never retain a whole conversation.
        for part in chunk.splitlines(keepends=True):
            self.buffer += part if not self.discard_line else b''
            if len(self.buffer) > 2_000_000:
                self.buffer = b''
                self.discard_line = True
            if part.endswith(b'\n'):
                line, self.buffer = self.buffer, b''
                if not self.discard_line and line.startswith(b'data:'):
                    with contextlib.suppress(ValueError, UnicodeError):
                        self.event(json.loads(line[5:].strip()))
                self.discard_line = False

    def values(self):
        return dict(duration_ms=round((time.monotonic()-self.start)*1000),
                    ttft_ms=None if self.first is None else round(self.first*1000),
                    usage=self.usage, stop_reason=self.stop)


def cache_key(run, body):
    if not run:
        return None
    metadata = body.get('metadata') or {}
    user = metadata.get('user_id', '') if isinstance(metadata, dict) else ''
    return hashlib.sha256((run + ':' + str(user)).encode()).hexdigest()


def retry_delay(headers, cap):
    value = headers.get('retry-after', '1')
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            delay = 1
    # Do not retry sooner than Retry-After when it exceeds our waiting budget.
    return max(0, delay) if delay <= cap else None


class NativeTransport:
    async def count(self, body):
        fields = {k: body[k] for k in ('model', 'system', 'messages', 'tools', 'tool_choice', 'thinking') if k in body}
        return await self.client.post(self.config['primary_anthropic_base_url'] + '/v1/messages/count_tokens',
                                      headers={'x-api-key': self.keys['primary'], 'anthropic-version': '2023-06-01'},
                                      json=fields, timeout=httpx.Timeout(30, connect=self.config.get('connect_timeout_seconds') or 20))

    async def count_response(self, body):
        try:
            response = await self.count(body)
            if response.is_success:
                data = response.json()
                if type(data.get('input_tokens')) is int and data['input_tokens'] >= 0:
                    return web.json_response({'input_tokens': data['input_tokens']})
                return web.json_response(safe_error(502, ''), status=502)
            return web.json_response(safe_error(response.status_code, response.content), status=response.status_code)
        except (httpx.TransportError, ValueError):
            return web.json_response(safe_error(504, ''), status=504)

    async def native(self, request, body, rid, run):
        from server import eligible, sse
        payload = dict(body)
        payload['thinking'] = {'type': 'adaptive'}
        payload['output_config'] = dict(body.get('output_config') or {}, effort=self.config['primary_reasoning_effort'])
        payload['service_tier'] = self.config['primary_service_tier']
        payload.setdefault('temperature', self.config['primary_temperature'])
        payload.setdefault('top_p', self.config['primary_top_p'])
        key = cache_key(run, body)
        if key:
            payload['prompt_cache_key'] = key
        headers = {'x-api-key': self.keys['primary'], 'anthropic-version': request.headers.get('anthropic-version', '2023-06-01')}
        if request.headers.get('anthropic-beta'):
            headers['anthropic-beta'] = request.headers['anthropic-beta']
        metrics = Metrics()
        response = None
        try:
            for attempt in range(self.config.get('primary_retries', 1) + 1):
                try:
                    # The deadline covers headers + first response bytes, not total generation.
                    async with asyncio.timeout(self.config.get('response_timeout_seconds')):
                        upstream = self.client.build_request('POST', self.config['primary_anthropic_base_url'] + '/v1/messages', headers=headers, json=payload)
                        response = await self.client.send(upstream, stream=True)
                        if response.is_error:
                            error = await response.aread()
                        else:
                            iterator = response.aiter_bytes()
                            first = await anext(iterator, b'')
                    if not response.is_error:
                        break
                    status = response.status_code
                    can_retry = eligible(self.config, body['model'], status, error)
                    delay = retry_delay(response.headers, self.config.get('retry_max_delay_seconds', 10))
                    if can_retry and attempt < self.config.get('primary_retries', 1) and delay is not None:
                        await response.aclose()
                        self.record(event='retry', id=rid, route='deepinfra', status=status, delay_seconds=delay)
                        await asyncio.sleep(delay)
                        continue
                    if can_retry:
                        await response.aclose()
                        return await self.fallback(request, body, rid, 'http_' + str(status))
                    self.record(event='primary_error', id=rid, route='deepinfra', status=status, **metrics.values())
                    return web.json_response(safe_error(status, error), status=status, headers={k:v for k,v in response.headers.items() if k in ('retry-after','x-request-id')})
                except (httpx.TransportError, TimeoutError):
                    if response is not None:
                        await response.aclose()
                    if attempt < self.config.get('primary_retries', 1):
                        self.record(event='retry', id=rid, route='deepinfra', delay_seconds=1)
                        await asyncio.sleep(1)
                        continue
                    return await self.fallback(request, body, rid, 'connection_timeout_or_error')
            if not body.get('stream'):
                # Nonstream responses still obey the socket inactivity deadline.
                raw = first + b''.join([part async for part in iterator])
                try:
                    result = json.loads(raw)
                except ValueError:
                    return web.json_response(safe_error(502, ''), status=502)
                metrics.event(result)
                self.record(event='complete', id=rid, route='deepinfra', model=body['model'], **metrics.values())
                return web.json_response(result, headers={'x-claude-deepseek-route':'deepinfra'})
            downstream = web.StreamResponse(headers={'content-type':'text/event-stream','cache-control':'no-cache','x-claude-deepseek-route':'deepinfra'})
            await downstream.prepare(request)
            try:
                metrics.feed(first)
                await downstream.write(first)
                async for part in iterator:
                    metrics.feed(part)
                    await downstream.write(part)
                if metrics.stop is None:
                    raise RuntimeError('incomplete stream')
                await downstream.write_eof()
                self.record(event='complete', id=rid, route='deepinfra', model=body['model'], **metrics.values())
            except (ConnectionError, asyncio.CancelledError):
                raise
            except Exception:
                self.record(event='stream_interrupted', id=rid, route='deepinfra', **metrics.values())
                with contextlib.suppress(ConnectionError):
                    await downstream.write(sse({'type':'error','error':{'type':'api_error','message':'DeepInfra stream interrupted; not replayed after output began'}}))
                    await downstream.write_eof()
            return downstream
        except asyncio.CancelledError:
            self.record(event='cancelled', id=rid, route='deepinfra', **metrics.values())
            raise
        except (httpx.TransportError, TimeoutError):
            self.record(event='primary_error', id=rid, route='deepinfra', status=504, **metrics.values())
            return web.json_response(safe_error(504, ''), status=504)
        finally:
            if response is not None:
                await response.aclose()
