"""Loopback-only reliability bridge; provider refusals never select a fallback."""
import asyncio
import contextlib
from contextvars import ContextVar
from status_display import RouteStatus, valid_run, request_session
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import time
import uuid

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_TELEMETRY", "False")
import httpx
from aiohttp import web
import litellm
from native import NativeTransport, Metrics, safe_error, cache_key
from gpt_main import GptMain, GptFailure, MODEL as GPT_MODEL, stream_events

litellm.telemetry = False
litellm.suppress_debug_info = True
litellm.set_verbose = False
litellm.drop_params = True
# These providers expose the Chat Completions API, including DeepInfra's service tier.
litellm.use_chat_completions_url_for_anthropic_messages = True
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
ROOT = Path(__file__).resolve().parent
SERVICE = "claude-deepseek-reliability-v1"
CHAIN_MODEL = "deepseek-v4.1-flash-1017-uncensored-chains"
CHAIN_LABEL = "DeepSeek V4.1 Flash 1017 uncensored chains + GPT Astra high as reviewer/second brain"
RUN_ID = ContextVar("status_run_id", default="")
REQUEST_ID = ContextVar("status_request_id", default="")
FALLBACK_BUDGET = ContextVar("fallback_budget", default=None)
TECHNICAL_STATUS = {408, 429, 500, 502, 503, 504, 529}
POLICY_ERROR = re.compile(r"content.?filter|content.?policy|moderation|safety|refus|policy.?violation|guardrail|blocked.?prompt", re.I)


def read_protected_key(path):
    """Read PowerShell ConvertFrom-SecureString's Windows DPAPI format."""
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    data = bytes.fromhex(Path(path).read_text(encoding="ascii").strip())
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, dest = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(dest)):
        raise RuntimeError("Cannot decrypt the local credential for this Windows account")
    try:
        return ctypes.string_at(dest.data, dest.size).decode("utf-16-le")
    finally:
        free(ctypes.cast(dest.data, ctypes.c_void_p))


def eligible(config, model, status=None, error_body=b"", transport=False):
    if model != config["primary_model"]:
        return False
    if transport:
        return True
    if status not in TECHNICAL_STATUS or status not in config["fallback_http_statuses"]:
        return False
    # Inspect only an error envelope, never successful model output or refusal text.
    return not POLICY_ERROR.search(error_body[:65536].decode("utf-8", "replace"))


def api_error(message, status=502, kind="api_error"):
    return web.json_response({"type": "error", "error": {"type": kind, "message": message}}, status=status)


def sse(event):
    if hasattr(event, "model_dump"):
        event = event.model_dump(exclude_none=True)
    if isinstance(event, (bytes, str)):
        return event.encode() if isinstance(event, str) else event
    return (f"event: {event['type']}\ndata: " + json.dumps(event, separators=(",", ":")) + "\n\n").encode()


class Bridge(NativeTransport):
    def __init__(self, config, keys, *, client=None, translate=None, audit=None, status=None, gpt=None):
        self.config, self.keys = config, keys
        self.active_requests = set()
        self.gpt = gpt or GptMain(config)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config["stream_idle_timeout_seconds"], connect=config["connect_timeout_seconds"]),
            follow_redirects=False, trust_env=False)
        self.translate = translate or litellm.anthropic.messages.acreate
        self.audit = audit or (lambda **record: None)
        self.status = status
        self.fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def record(self, **record):
        # Fixed metadata only: no request/response bodies, keys, headers or exception strings.
        record = dict(time=time.time(), run_id=RUN_ID.get(), **record)
        self.audit(**record)
        if self.status is not None:
            # A display/write failure must never affect provider routing.
            with contextlib.suppress(Exception):
                self.status.record(record)

    async def handle(self, request):
        run_token = RUN_ID.set(valid_run(request.headers.get("x-claude-deepseek-run", "")))
        request_token = REQUEST_ID.set(uuid.uuid4().hex)
        budget_token = FALLBACK_BUDGET.set(None)
        if request.path == "/v1/messages":
            self.active_requests.add(REQUEST_ID.get())
        try:
            return await self._handle(request)
        finally:
            self.active_requests.discard(REQUEST_ID.get())
            self.record(event="request_end", id=REQUEST_ID.get())
            REQUEST_ID.reset(request_token)
            FALLBACK_BUDGET.reset(budget_token)
            RUN_ID.reset(run_token)

    async def _handle(self, request):
        auth = request.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-api-key", "")
        if not hmac.compare_digest(provided, self.keys["local"]):
            return api_error("Local gateway authentication required", 401, "authentication_error")
        if request.path == "/health" and request.method == "GET":
            return web.json_response({"service": SERVICE, "config_sha256": self.fingerprint, "pid": os.getpid(),
                                      "active_requests": len(self.active_requests),
                                      "transport": self.config.get("primary_transport", "translated"),
                                      "gateway_revision": 8,
                                      "gpt_worker_limit": 2,
                                      "model_selection": "request_model",
                                      "gpt_main": {"model": GPT_MODEL, "effort": "high", "fallback": self.config["primary_model"]},
                                      "primary_service_tier": self.config.get("primary_service_tier"),
                                      "primary_reasoning_effort": self.config.get("primary_reasoning_effort"),
                                      "primary_temperature": self.config.get("primary_temperature", 1.0),
                                      "primary_top_p": self.config.get("primary_top_p", 0.95),
                                      "primary": self.config["primary_model"], "fallback": self.config["fallback_model"],
                                      "support": self.config["support_model"], "fallback_chain": self.config["fallback_chain"], "status_display": 1})
        if request.path == "/control/gptswitch" and request.method == "POST":
            return api_error("The toggle has been replaced by native /model. Select GPT Astra high or DeepSeek there.", 410, "invalid_request_error")
        if (request.method, request.path) not in {("POST", "/v1/messages"),
                                                 ("POST", "/v1/messages/count_tokens"),
                                                 ("GET", "/v1/models")}:
            return api_error("Unsupported local gateway route", 404, "not_found_error")
        raw = await request.read()
        try:
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            return api_error("Request must be a JSON object", 400, "invalid_request_error")
        # Agent-view workers may reuse another launcher's environment. Claude's
        # per-request conversation ID is authoritative over inherited headers.
        conversation = request_session(body)
        if conversation:
            RUN_ID.set(conversation)
        selected_model = body.get("model", "")
        if not isinstance(selected_model, str):
            return api_error("Model must be a string", 400, "invalid_request_error")
        # Native Default can attach Claude's context marker to a pinned custom
        # model. It is a client context hint, never part of a provider model ID.
        selected_model = selected_model.removesuffix("[1m]")
        use_gpt = selected_model == GPT_MODEL
        use_chain = selected_model == CHAIN_MODEL
        model = self.config["primary_model"] if use_gpt or use_chain else selected_model
        if request.method == "POST" and model not in {self.config["primary_model"], self.config["support_model"]}:
            return api_error("Model is not configured in this gateway", 400, "invalid_request_error")
        if model != body.get("model") and request.method == "POST":
            # The native DeepSeek route/count endpoint must receive its exact
            # provider ID. The client-selected GPT ID never leaks downstream.
            body = dict(body, model=model)
        rid = REQUEST_ID.get()
        if request.path == "/v1/models":
            return web.json_response({"data": [{"id": GPT_MODEL, "type": "model", "display_name": "GPT Astra high → DeepSeek → existing fallback chain"},
                                               {"id": CHAIN_MODEL, "type": "model", "display_name": CHAIN_LABEL},
                                               {"id": self.config["primary_model"], "type": "model", "display_name": "DeepSeek + GPT Astra high as reviewer/second brain → existing fallback chain"},
                                               {"id": self.config["support_model"], "type": "model", "display_name": "Kimi on OpenRouter"}],
                                      "has_more": False})
        if model == self.config["primary_model"]:
            if request.path == "/v1/messages/count_tokens":
                return await self.count_response(body)
            self.record(event="request", id=rid, model=selected_model,
                        route="gpt" if use_gpt else ("outrun" if use_chain else "deepinfra"), path=request.path,
                        workload="tools" if body.get("tools") else "text", selected_model=selected_model)
            self.record(event="request_size", id=rid, tool_count=len(body.get("tools", [])),
                        system_bytes=len(json.dumps(body.get("system", "")).encode()),
                        tool_schema_bytes=len(json.dumps(body.get("tools", [])).encode()),
                        messages_bytes=len(json.dumps(body.get("messages", [])).encode()))
            if use_chain:
                return await self.fallback(request, body, rid, "selected_chain")
            if use_gpt:
                try:
                    started = time.monotonic()
                    result = await self.gpt.complete(body)
                except GptFailure as exc:
                    if not exc.fallback:
                        self.record(event="primary_error", id=rid, route="gpt", reason=exc.reason)
                        return api_error("GPT could not complete this request; automatic fallback is not permitted for this error", 400)
                    self.record(event="fallback", id=rid, route="deepinfra", from_route="gpt",
                                model=GPT_MODEL, fallback_model=model, reason=exc.reason)
                else:
                    if body.get("stream"):
                        output = web.StreamResponse(headers={"content-type": "text/event-stream", "cache-control": "no-cache",
                                                             "x-claude-deepseek-route": "gpt"})
                        await output.prepare(request)
                        for event in stream_events(result):
                            await output.write(sse(event))
                        await output.write_eof()
                    else:
                        output = web.json_response(result, headers={"x-claude-deepseek-route": "gpt"})
                    self.record(event="complete", id=rid, route="gpt", model=GPT_MODEL,
                                reasoning_effort="high", duration_ms=round((time.monotonic()-started)*1000),
                                usage=result["usage"], stop_reason=result["stop_reason"])
                    return output
            if self.config.get("primary_transport") == "native":
                return await self.native(request, body, rid, RUN_ID.get())
            return await self.translated(request, body, rid, "deepinfra")
        headers = {"authorization": "Bearer " + self.keys["support"], "content-type": "application/json",
                   "anthropic-version": request.headers.get("anthropic-version", "2023-06-01")}
        if request.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = request.headers["anthropic-beta"]
        self.record(event="request", id=rid, model=model, route="openrouter", path=request.path)
        response = None
        may_fallback = request.path == "/v1/messages"
        try:
            upstream = self.client.build_request(request.method, self.config["support_base_url"].rstrip("/") + request.path,
                                                  headers=headers, content=raw)
            async with asyncio.timeout(self.config["response_timeout_seconds"]):
                response = await self.client.send(upstream, stream=True)
                if response.status_code >= 400:
                    error = await response.aread()
                else:
                    # Read before committing the response so an initial timeout can fail over safely.
                    iterator = response.aiter_bytes()
                    first = await anext(iterator, b"")
        except (httpx.TransportError, TimeoutError) as exc:
            if response is not None:
                await response.aclose()
            if may_fallback and eligible(self.config, model, transport=True):
                reason = "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "connection_error"
                return await self.fallback(request, body, rid, reason)
            self.record(event="primary_transport_error", id=rid, model=model)
            return api_error("OpenRouter connection failed; no alternate route is configured for this model", 504)
        except asyncio.CancelledError:
            if response is not None:
                await response.aclose()
            raise
        if response.status_code >= 400:
            await response.aclose()
            if may_fallback and eligible(self.config, model, response.status_code, error):
                return await self.fallback(request, body, rid, "http_" + str(response.status_code))
            self.record(event="primary_error", id=rid, model=model, status=response.status_code)
            return web.Response(body=error, status=response.status_code, headers=self.response_headers(response))
        try:
            downstream = web.StreamResponse(status=response.status_code, headers=self.response_headers(response))
            await downstream.prepare(request)
            await downstream.write(first)
            async for chunk in iterator:
                await downstream.write(chunk)
            await downstream.write_eof()
            self.record(event="complete", id=rid, model=model, route="openrouter")
            return downstream
        except (httpx.TransportError, TimeoutError):
            self.record(event="stream_interrupted", id=rid, model=model, route="openrouter")
            # Never replay after output has begun: the client may already have seen tool calls.
            if "text/event-stream" in response.headers.get("content-type", ""):
                with contextlib.suppress(ConnectionError):
                    await downstream.write(sse({"type": "error", "error": {"type": "api_error",
                        "message": "OpenRouter stream interrupted; not replayed after output began"}}))
                    await downstream.write_eof()
            else:
                downstream.force_close()
            return downstream
        finally:
            await response.aclose()

    @staticmethod
    def response_headers(response):
        allowed = {"content-type", "retry-after", "x-request-id", "request-id"}
        return {k: v for k, v in response.headers.items() if k in allowed or k.startswith("x-ratelimit-")}

    async def fallback(self, request, body, rid, reason, index=0):
        source_route = "deepinfra" if index == 0 else self.config["fallback_chain"][index - 1]["route"]
        if self.config.get("fallback_capacity_checks"):
            # Different checkpoints can tokenize differently: leave a 10% margin.
            budget = FALLBACK_BUDGET.get()
            if budget is None:
                try:
                    counted = await self.count(body)
                    counted.raise_for_status()
                    input_tokens = int(counted.json()["input_tokens"] * 1.1) + 8192
                    basis = "primary_tokenizer_with_margin"
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    # Conservative byte budget, including inline image data, when counting is down.
                    input_tokens = len(json.dumps(body, ensure_ascii=False).encode()) + 8192
                    basis = "serialized_bytes_with_margin"
                FALLBACK_BUDGET.set((input_tokens, basis))
            else:
                input_tokens, basis = budget
            while index < len(self.config["fallback_chain"]):
                candidate = self.config["fallback_chain"][index]
                output = body.get("max_tokens", 0)
                if (input_tokens + output <= candidate["context_limit"] and output <= candidate["output_limit"]):
                    break
                self.record(event="fallback_skipped", id=rid, route=candidate["route"], reason="capacity", input_budget=input_tokens, basis=basis)
                index += 1
        if index >= len(self.config["fallback_chain"]):
            self.record(event="fallback_error", id=rid, status=400, reason="capacity")
            return api_error("No fallback has sufficient verified or conservatively configured context/output capacity. Compact the conversation or reduce max_tokens.", 400, "invalid_request_error")
        target = self.config["fallback_chain"][index]
        self.record(event="chain_start" if reason == "selected_chain" else "fallback", id=rid, model=body["model"], route=target["route"], reason=reason,
                    fallback_model=target["model"], fallback_position=index + 1,
                    fallback_total=len(self.config["fallback_chain"]),
                    from_route=source_route)
        return await self.translated(request, body, rid, target["route"], fallback_index=index)

    async def translated(self, request, body, rid, route, fallback_index=None):
        metrics = Metrics()
        primary = fallback_index is None
        prefix = "primary" if primary else "fallback"
        target = None if primary else self.config["fallback_chain"][fallback_index]
        selected_model = self.config["primary_model"] if primary else target["model"]
        base_url = self.config["primary_base_url"] if primary else target["base_url"]
        key = self.keys["primary"] if primary else self.keys[target["key"]]
        timeout = self.config["response_timeout_seconds"] if primary else self.config["fallback_timeout_seconds"]
        accepted = {"messages", "max_tokens", "stream", "system", "stop_sequences", "temperature",
                    "thinking", "tool_choice", "tools", "top_k", "top_p"}
        args = {k: v for k, v in body.items() if k in accepted}
        args.update(model="openai/" + selected_model, api_base=base_url,
                    api_key=key, timeout=httpx.Timeout(self.config.get("stream_idle_timeout_seconds"), connect=self.config.get("connect_timeout_seconds")),
                    num_retries=0, drop_params=True)
        if primary:
            # DeepInfra effort must not be replaced by Anthropic's
            # enabled/disabled thinking -> OpenAI string effort translation.
            args.pop("thinking", None)
            args.setdefault("temperature", self.config.get("primary_temperature", 1.0))
            args.setdefault("top_p", self.config.get("primary_top_p", 0.95))
            extra = {}
            if self.config.get("primary_service_tier"):
                extra["service_tier"] = self.config["primary_service_tier"]
            if self.config.get("primary_reasoning_effort") is not None:
                extra["reasoning_effort"] = self.config["primary_reasoning_effort"]
            session_key = cache_key(RUN_ID.get(), body)
            if session_key:
                extra["prompt_cache_key"] = session_key
            if extra:
                args["extra_body"] = extra
        else:
            # Resolve client-side tool discovery references into portable history.
            def portable_blocks(content):
                if not isinstance(content, list):
                    return content
                blocks = []
                for block in content:
                    if block.get("type") == "tool_reference":
                        block = {"type": "text", "text": "Available tool: " + str(block.get("tool_name", ""))}
                    elif block.get("type") == "tool_result":
                        block = dict(block, content=portable_blocks(block.get("content", "")))
                    blocks.append(block)
                return blocks
            args["messages"] = [dict(message, content=portable_blocks(message.get("content", "")))
                                for message in args.get("messages", [])]
            if "tools" in args:
                args["tools"] = [{k:v for k,v in tool.items() if k != "defer_loading"} for tool in args["tools"]]
        result = None
        try:
            async with asyncio.timeout(timeout):
                result = await self.translate(**args)
                if not body.get("stream"):
                    if hasattr(result, "model_dump"):
                        result = result.model_dump(exclude_none=True)
                    metrics.event(result)
                    self.record(event="complete", id=rid, route=route, model=selected_model, **metrics.values())
                    return web.json_response(result, headers={"x-claude-deepseek-route": route})
                iterator = result.__aiter__()
                first = await anext(iterator)
        except asyncio.CancelledError:
            if hasattr(result, "aclose"):
                await result.aclose()
            self.record(event="cancelled", id=rid, route=route, **metrics.values())
            raise
        except Exception as exc:
            if hasattr(result, "aclose"):
                await result.aclose()
            status = getattr(exc, "status_code", None)
            transport = isinstance(exc, (httpx.TransportError, TimeoutError, litellm.Timeout, litellm.APIConnectionError))
            next_index = 0 if primary else fallback_index + 1
            if next_index < len(self.config["fallback_chain"]) and eligible(self.config, body["model"], status, str(exc).encode(), transport=transport):
                reason = ("timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException, litellm.Timeout))
                          else "connection_error") if transport else "http_" + str(status)
                return await self.fallback(request, body, rid, reason, index=next_index)
            self.record(event=prefix + "_error", id=rid, route=route, error_type=type(exc).__name__, status=status)
            safe_status = status if isinstance(status, int) and 400 <= status <= 599 else 502
            return web.json_response(safe_error(safe_status, str(exc)), status=safe_status)
        downstream = web.StreamResponse(headers={"content-type": "text/event-stream", "cache-control": "no-cache",
                                                "x-claude-deepseek-route": route})
        try:
            await downstream.prepare(request)
            metrics.feed(sse(first))
            await downstream.write(sse(first))
            while True:
                try:
                    event = await asyncio.wait_for(anext(iterator), timeout=self.config["stream_idle_timeout_seconds"])
                except StopAsyncIteration:
                    break
                metrics.feed(sse(event))
                await downstream.write(sse(event))
            await downstream.write_eof()
            self.record(event="complete", id=rid, route=route, model=selected_model, **metrics.values())
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception as exc:
            self.record(event="stream_interrupted", id=rid, route=route, error_type=type(exc).__name__)
            with contextlib.suppress(ConnectionError):
                await downstream.write(sse({"type": "error", "error": {"type": "api_error",
                    "message": f"{route} stream interrupted; not replayed after output began"}}))
                await downstream.write_eof()
        finally:
            if hasattr(result, "aclose"):
                await result.aclose()
        return downstream

    def app(self):
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{path:.*}", self.handle)
        async def close(_app):
            await self.client.aclose()
        app.on_cleanup.append(close)
        return app


if __name__ == "__main__":
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    if config["listen_host"] != "127.0.0.1":
        raise RuntimeError("This gateway must bind to loopback")
    audit = logging.getLogger("route_metadata")
    audit.setLevel(logging.INFO)
    audit.propagate = False
    handler = RotatingFileHandler(ROOT / f"routes-{config['listen_port']}.jsonl", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit.addHandler(handler)
    keys = {"local": read_protected_key(ROOT.parent / "gateway-key.dpapi"),
            "primary": read_protected_key(ROOT.parent / "deepinfra-key.dpapi"),
            "support": read_protected_key(ROOT.parent / "openrouter-key.dpapi"),
            "fallback": read_protected_key(ROOT.parent / "outrun-key.dpapi"),
            "unlid": read_protected_key(ROOT.parent / "unlid-key.dpapi"),
            "allmodels": read_protected_key(ROOT.parent / "allmodels-key.dpapi")}
    bridge = Bridge(config, keys, status=RouteStatus(), audit=lambda **record: audit.info(json.dumps(record, separators=(",", ":"))))
    web.run_app(bridge.app(), host="127.0.0.1", port=config["listen_port"], access_log=None, print=None, handler_cancellation=True)
