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
    client = _client()
    messages = [{"role": "user", "content": content}]
    last = None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, temperature=0, system=system, messages=messages,
                tools=[{"name": tool_name, "description": "Record the result.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": tool_name})
        except Exception as e:  # network or overload: back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
            continue
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


def image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(png).decode()}}
