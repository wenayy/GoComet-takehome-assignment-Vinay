"""The one place the harness talks to a model: structured output through a forced tool call, cached on disk.

ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL come from the environment (the grader sets them). NOVA_MODEL picks the model;
any Anthropic-compatible endpoint works through ANTHROPIC_BASE_URL while developing.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time

from .store import Store

DEFAULT_MODEL = "claude-sonnet-5"
PROMPT_VERSION = "1"


class LLMError(RuntimeError):
    pass


def model_name() -> str:
    return os.environ.get("NOVA_MODEL", DEFAULT_MODEL)


def _client():
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(max_retries=4, timeout=300)


def call_tool(store: Store, *, system: str, content: list | str, tool_name: str, schema: dict,
              max_tokens: int = 16000, cache_tag: str = "") -> dict:
    """Ask the model to answer by calling one tool; return the tool input. Same request -> cached answer."""
    model = model_name()
    payload = json.dumps([PROMPT_VERSION, model, system, content, tool_name, schema, cache_tag], sort_keys=True)
    key = hashlib.sha256(payload.encode()).hexdigest()
    hit = store.cache_get(key)
    if hit is not None:
        return hit
    import anthropic
    client = _client()
    messages = [{"role": "user", "content": content}]
    last = None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=messages,
                tools=[{"name": tool_name, "description": "Record the result.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": tool_name}, **_model_options(model))
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
            last = e  # transient: back off and retry
            time.sleep(2 * (attempt + 1))
            continue
        _log_usage(store, model, tool_name, resp)
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                if resp.stop_reason == "max_tokens":
                    last = LLMError("answer cut off at max_tokens")
                    break
                store.cache_put(key, block.input)
                return block.input
        else:
            last = LLMError("model did not call the tool")
    raise LLMError(f"{tool_name} failed: {last}")


def _model_options(model: str) -> dict:
    """Reading a document into fields is transcription, not reasoning: on Claude models that think by default, ask for
    low effort. Haiku 4.5 and non-Claude endpoints don't take the option."""
    if model.startswith("claude-") and "haiku" not in model:
        return {"output_config": {"effort": "low"}}
    return {}


def _log_usage(store: Store, model: str, tool: str, resp) -> None:
    """One line per model call, for the cost estimate in DESIGN.md."""
    u = getattr(resp, "usage", None)
    if u is None:
        return
    line = {"model": model, "tool": tool, "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None)}
    path = os.environ.get("NOVA_USAGE_LOG") or store.root / "llm_usage.jsonl"   # .env may point it outside the state dir
    with open(path, "a") as f:
        f.write(json.dumps(line) + "\n")


def image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(png).decode()}}
