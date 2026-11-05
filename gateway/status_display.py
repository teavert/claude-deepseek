"""Local metadata-only status for claude-deepseek. No credentials or API calls."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "status"
STAGES = {"frontier-reviewer": "planning", "finding-validator": "validating"}
MODELS = {"outrun": "DeepSeek V4.1 Flash Abliterated 2", "unlid": "DeepSeek V4.1 Flash Uncensored", "deepinfra": "DeepSeek V4.1 Flash 1017", "allmodels": "DeepSeek V4.1 Flash Abliterated",
          "openrouter": "Kimi K3.1 Pro", "gpt": "GPT Astra high"}
PROVIDERS = {"outrun": "Outrun", "unlid": "Unlid", "deepinfra": "DeepInfra", "allmodels": "AllModels", "openrouter": "OpenRouter", "gpt": "ChatGPT subscription"}


def valid_run(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) else ""


def session_key(value):
    if not isinstance(value, str):
        return ""
    if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
        return value.replace("-", "").lower()
    return valid_run(value.lower())


def request_session(body):
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        return ""
    user_id = metadata.get("user_id", "")
    if isinstance(user_id, str):
        try:
            parsed = json.loads(user_id)
        except ValueError:
            # Earlier Claude Code releases used user_..._session_<uuid>.
            return session_key(user_id.rsplit("_session_", 1)[-1]) if "_session_" in user_id else ""
        if isinstance(parsed, dict):
            return session_key(parsed.get("session_id"))
    return session_key(metadata.get("session_id"))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def session_dir(run, session):
    # Hash caller-controlled IDs; never use transcript paths or raw hook content.
    return STATE / run / hashlib.sha256(str(session).encode()).hexdigest()[:24]


class RouteStatus:
    """One gateway writer; atomic snapshots let concurrent renderers read safely."""
    def __init__(self, root=STATE):
        self.root = root
        self.runs = OrderedDict()

    def record(self, record):
        run = valid_run(record.get("run_id"))
        if not run:
            return
        if run not in self.runs:
            saved = read_json(self.root / run / "routes.json")
            self.runs[run] = {"active": {}, "fallback_count": saved.get("fallback_count", 0),
                              "last_fallback": saved.get("last_fallback"), "last": saved.get("last", {})}
        data = self.runs[run]
        self.runs.move_to_end(run)
        if len(self.runs) > 256:
            self.runs.popitem(last=False)
        rid, event = record.get("id"), record.get("event")
        active = data["active"]
        now = record.get("time", time.time())
        if event == "request" and record.get("path") == "/v1/messages":
            route = record["route"]
            role = "support" if route == "openrouter" else ("text" if record.get("workload") == "text" else "lead")
            item = {"route": route, "model": record["model"], "role": role, "time": now, "state": "active"}
            if record.get("selected_model"):
                item["selected_model"] = record["selected_model"]
            active[rid] = item
            data["last"][role] = item.copy()
        elif event == "request_size" and rid in active:
            for name in ("tool_count", "system_bytes", "tool_schema_bytes", "messages_bytes"):
                active[rid][name] = record.get(name)
        elif event == "chain_start" and rid in active:
            item = active[rid]
            item.update(route=record["route"], model=record["fallback_model"], time=now)
            data["last"][item["role"]] = item.copy()
        elif event == "fallback" and rid in active:
            item = active[rid]
            switch = {"from_route": record.get("from_route", item["route"]),
                      "route": record.get("route", "outrun"), "reason": record["reason"], "time": now}
            item["switches"] = (item.get("switches", []) + [switch])[-3:]
            item.update(route=record.get("route", "outrun"), model=record["fallback_model"], reason=record["reason"], time=now)
            item["fallback_position"] = record.get("fallback_position")
            item["fallback_total"] = record.get("fallback_total")
            data["fallback_count"] += 1
            data["last_fallback"] = switch
            data["last"][item["role"]] = item.copy()
        elif rid in active and (event == "complete" or "error" in event or event == "stream_interrupted"):
            item = active.pop(rid)
            item.update(state="idle" if event == "complete" else "error", time=now)
            for name in ("duration_ms", "ttft_ms", "usage", "stop_reason"):
                if name in record:
                    item[name] = record[name]
            if event != "complete":
                item["failure_reason"] = ("stream_interrupted" if event == "stream_interrupted" else
                    "http_" + str(record["status"]) if record.get("status") else "technical_failure")
            data["last"][item["role"]] = item
        elif event == "request_end" and rid in active:
            item = active.pop(rid)
            item.update(state="interrupted", time=now)
            data["last"][item["role"]] = item
        else:
            return
        data["updated"] = now
        atomic_json(self.root / run / "routes.json", data)


def hook(payload, run):
    folder = session_dir(run, payload.get("session_id", ""))
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        atomic_json(folder / "turn.json", {"time": time.time()})
    elif event == "PostToolUseFailure" and payload.get("tool_name") in {"Agent", "Task"}:
        stage = STAGES.get(payload.get("tool_input", {}).get("subagent_type"))
        if stage:
            atomic_json(folder / ("failure-" + stage + ".json"), {"stage": stage, "time": time.time()})
    elif event in {"SubagentStart", "SubagentStop"} and payload.get("agent_type") in STAGES:
        aid = hashlib.sha256(str(payload.get("agent_id", "")).encode()).hexdigest()[:24]
        atomic_json(folder / ("agent-" + aid + ".json"), {
            "stage": STAGES[payload["agent_type"]], "time": time.time(),
            "state": "active" if event == "SubagentStart" else "finished"})


def age(timestamp):
    seconds = max(0, int(time.time() - timestamp))
    return f"{seconds}s ago" if seconds < 60 else f"{seconds // 60}m ago"


def reason_label(reason):
    labels = {"http_408": "request timeout (HTTP 408)", "http_429": "rate limit (HTTP 429)",
              "http_500": "server error (HTTP 500)", "http_502": "bad gateway (HTTP 502)",
              "http_503": "service unavailable (HTTP 503)", "http_504": "gateway timeout (HTTP 504)",
              "http_529": "provider overloaded (HTTP 529)",
              "http_401": "authentication failed (HTTP 401)", "http_402": "payment/balance error (HTTP 402)",
              "http_403": "access denied (HTTP 403)", "timeout": "upstream timeout",
              "connection_error": "connection failed", "connection_or_timeout": "connection failed or upstream timeout",
              "stream_interrupted": "stream interrupted; not replayed",
              "gpt_tool_name_rejected": "GPT adapter rejected a tool name",
              "gpt_tool_schema_rejected": "GPT adapter rejected a tool schema",
              "gpt_rate_limit": "GPT usage/rate limit",
              "gpt_context_limit": "GPT context limit",
              "gpt_authentication_failed": "GPT authentication failed",
              "gpt_chatgpt_login_required": "Codex ChatGPT login required",
              "gpt_invalid_request": "GPT adapter rejected the request",
              "gpt_unavailable": "GPT upstream error (unspecified)",
              "gpt_process_exited": "Codex process exited",
              "gpt_protocol_error": "Codex protocol/connection error",
              "gpt_model_mismatch": "GPT model/effort mismatch"}
    if reason in labels:
        return labels[reason]
    if re.fullmatch(r"http_\d{3}", reason or ""):
        return "HTTP " + reason[5:]
    return "technical failure"


def switch_label(switch):
    source = PROVIDERS.get(switch.get("from_route"), "previous provider")
    target = PROVIDERS.get(switch.get("route"), "fallback")
    return f"{source} -> {target}: {reason_label(switch.get('reason'))}"


def render(payload, run):
    if not run:
        return "DeepSeek status: restart with claude-deepseek to enable live routing"
    conversation = session_key(payload.get("session_id"))
    route_key = conversation if conversation and (STATE / conversation / "routes.json").exists() else run
    data = read_json(STATE / route_key / "routes.json")
    active = list(data.get("active", {}).values())
    lead = next((x for x in reversed(active) if x["role"] == "lead"), data.get("last", {}).get("lead", {}))
    text_request = next((x for x in reversed(active) if x["role"] == "text"), data.get("last", {}).get("text", {}))
    if not lead:
        lead = text_request
    route = lead.get("route", "deepinfra")
    state = lead.get("state", "ready")
    label = "ACTIVE" if state == "active" else ("last: " + state if lead else "ready")
    first = f"{MODELS.get(route, 'DeepSeek')} @ {PROVIDERS.get(route, route)} [{label}]"
    if lead.get("role") == "lead":
        first = "Tool-capable request: " + first
    fallback = data.get("last_fallback")
    route_details = []
    if route in {"allmodels", "unlid", "outrun"} or (route == "deepinfra" and lead.get("switches")):
        position, total = lead.get("fallback_position"), lead.get("fallback_total")
        first += f" | FALLBACK {position}/{total}" if position and total else " | FALLBACK"
        if lead.get("switches"):
            route_details.append("Switches: " + "; ".join(switch_label(s) for s in lead["switches"]))
        else:
            route_details.append("Reason: " + reason_label(lead.get("reason")))
    elif fallback:
        route_details.append(f"Last switch: {switch_label(fallback)} ({age(fallback['time'])})")
    if lead.get("failure_reason"):
        route_details.append("Stopped: " + reason_label(lead["failure_reason"]) + "; no further automatic switch")
    selected = (payload.get("model") or {}).get("id") or lead.get("selected_model")
    if isinstance(selected, str):
        selected = selected.removesuffix("[1m]")
    if selected == "gpt-6-astra":
        route_details.insert(0, "Selected: GPT Astra high -> DeepSeek -> existing fallback chain")
    elif selected == "deepseek-v4.1-flash-1017-uncensored-chains":
        route_details.insert(0, "Selected: DeepSeek V4.1 Flash 1017 uncensored chains + GPT Astra high as reviewer/second brain")
    elif selected and selected.startswith("deepseek-ai/"):
        route_details.insert(0, "Selected: DeepSeek + GPT Astra high as reviewer/second brain -> existing fallback chain")
    if data.get("fallback_count"):
        first += f" | switches {data['fallback_count']}"
    usage = lead.get("usage", {})
    if usage:
        details = []
        if lead.get("ttft_ms") is not None:
            details.append(f"First output {lead['ttft_ms']/1000:.1f}s")
        if lead.get("duration_ms") is not None:
            details.append(f"total {lead['duration_ms']/1000:.1f}s")
        details.append(f"tokens in {usage.get('input_tokens', 0):,} + cached {usage.get('cache_read_input_tokens', 0):,}; out {usage.get('output_tokens', 0):,}")
        route_details.append(" | ".join(details))
    folder = session_dir(run, payload.get("session_id", ""))
    turn = read_json(folder / "turn.json").get("time", 0)
    agents = [read_json(path) for path in folder.glob("agent-*.json")]
    failures = [read_json(path) for path in folder.glob("failure-*.json")]
    failed_at = {f["stage"]: f["time"] for f in failures if "stage" in f and "time" in f}
    stages = sorted({a.get("stage") for a in agents if a.get("state") == "active" and a.get("stage")
                     and a.get("time", 0) > failed_at.get(a["stage"], 0)})
    done = sorted({a.get("stage") for a in agents if a.get("state") == "finished" and a.get("time", 0) >= turn and a.get("stage")})
    second = "GPT review (Astra high) @ ChatGPT subscription: " + (" + ".join(stages).upper() if stages else "idle")
    if done:
        second += " | " + ", ".join(s + " finished" for s in done)
    recent_failures = sorted(s for s, timestamp in failed_at.items() if timestamp >= turn)
    if recent_failures:
        second += " | " + ", ".join(s + " FAILED" for s in recent_failures)
    # ASCII keeps Windows consoles reliable. Wrap instead of losing fallback/stage text.
    try:
        width = max(40, int(os.environ.get("COLUMNS", "120")))
    except ValueError:
        width = 120
    import textwrap
    lines = [first, *route_details]
    if lead.get("role") == "lead" and text_request:
        lines.append("Text-only/background: " + MODELS.get(text_request.get("route"), "unknown") +
                     " [" + text_request.get("state", "unknown") + "]")
    lines.append(second)
    return "\n".join(textwrap.fill(line, width=width, subsequent_indent="  ") for line in lines)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "render"
    try:
        payload = json.load(sys.stdin)
        run = valid_run(os.environ.get("CLAUDE_DEEPSEEK_RUN_ID", ""))
        if mode == "hook":
            if run:
                hook(payload, run)
        else:
            print(render(payload, run))
    except Exception:
        # Observability never blocks a prompt or agent, and never prints hook input.
        if mode != "hook":
            print("DeepSeek status temporarily unavailable")


if __name__ == "__main__":
    main()
