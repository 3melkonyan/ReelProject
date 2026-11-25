"""Minimal local Ollama client (JSON output). No paid APIs."""

from __future__ import annotations

import json
import os
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("REEL_LLM", "gemma3:12b")


class LLMError(Exception):
    retryable = True


def chat_json(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    schema: dict | None = None,
    temperature: float = 0.2,
    timeout: float = 600,
) -> dict:
    body = {
        "model": model,
        "stream": False,
        "format": schema or "json",
        "options": {"temperature": temperature, "num_ctx": 16384},
        # Unload ~30 s after the last call instead of Ollama's default 5 min: the ~8 GB model must not
        # sit in memory through render/blur (measured: memory pressure 'warn' with it loaded).
        "keep_alive": "30s",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = json.loads(r.read())["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"ollama request failed: {e}") from e
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON: {content[:200]}") from e
