from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any


class OpenAICompatibleChatProvider:
    """Talks directly to an OpenAI-compatible /chat/completions endpoint over HTTP.

    Unlike CliChatProvider, this has no CLI process to spawn per turn - no hooks,
    plugins, CLAUDE.md discovery, or cold cache-creation overhead - so it's the
    fastest option when a fast hosted or local (vLLM/Ollama/LM Studio/etc.) model
    is available. It never sees a shell or filesystem tool; it only ever receives
    a system prompt and a plain text prompt, so it carries the exact same
    read-only capability boundary as the CLI providers by construction.
    """

    def __init__(self, base_url: str, api_key: str | None, model: str, timeout_seconds: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def stream(self, system_prompt: str, prompt: str) -> Iterator[dict[str, Any]]:
        if not self.available:
            yield {"type": "error", "message": "No external chat LLM endpoint is configured."}
            return
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout_seconds)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            yield {"type": "error", "message": f"External LLM request failed: {exc}"}
            return
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield {"type": "text_delta", "text": text}
                if choices[0].get("finish_reason") == "error":
                    yield {"type": "error", "message": "External LLM returned an error."}
                    return
        except (OSError, TimeoutError) as exc:
            yield {"type": "error", "message": f"External LLM stream interrupted: {exc}"}
            return
        finally:
            response.close()
        yield {"type": "done"}
