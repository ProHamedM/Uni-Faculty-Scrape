"""Gemini, OpenAI-compatible (OpenRouter / Ollama / others) and Anthropic over plain HTTPS.

API keys travel in headers only (never in URLs), so they can't leak into logs
through a logged URL; the log redactor catches them anywhere else.
"""

from __future__ import annotations

import httpx

from unifaculty import REPO_URL
from unifaculty.llm.base import LLMError, LLMProvider, LLMResult, error_from_response


class GeminiProvider(LLMProvider):
    name = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str, base_url: str | None = None, **kw):
        super().__init__(model, **kw)
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.client = httpx.Client(timeout=self.timeout, headers={"x-goog-api-key": api_key,
                                                                  "Content-Type": "application/json"})

    def _complete(self, system: str, user: str, temperature: float, max_output_tokens: int) -> LLMResult:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_output_tokens,
                                 "responseMimeType": "application/json"},
        }
        resp = self.client.post(f"{self.base_url}/models/{self.model}:generateContent", json=body)
        if resp.status_code != 200:
            raise error_from_response(resp, self.name)
        data = resp.json()
        feedback = data.get("promptFeedback", {})
        if feedback.get("blockReason"):
            raise LLMError(f"gemini blocked the prompt: {feedback.get('blockReason')}", retryable=False)
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("gemini returned no candidates", retryable=True)
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata", {})
        return LLMResult(text=text, provider=self.name, model=self.model,
                         input_tokens=int(usage.get("promptTokenCount", 0) or 0),
                         output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
                         finish_reason=candidates[0].get("finishReason"), raw={"usage": usage})

    def close(self) -> None:
        self.client.close()


class OpenAICompatibleProvider(LLMProvider):
    """OpenRouter, Ollama (``http://localhost:11434/v1``) or any /chat/completions endpoint."""

    name = "openai_compat"

    def __init__(self, model: str, base_url: str, api_key: str | None = None, name: str | None = None, **kw):
        super().__init__(model, **kw)
        if name:
            self.name = name
        self.base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = REPO_URL
            headers["X-Title"] = "Uni Faculty Scraper"
        self.client = httpx.Client(timeout=self.timeout, headers=headers)
        self._json_mode = True

    def _complete(self, system: str, user: str, temperature: float, max_output_tokens: int) -> LLMResult:
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_output_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = self.client.post(f"{self.base_url}/chat/completions", json=body)
        if resp.status_code == 400 and self._json_mode and "response_format" in resp.text:
            self._json_mode = False  # some models don't support JSON mode; the prompt still asks for JSON
            body.pop("response_format")
            resp = self.client.post(f"{self.base_url}/chat/completions", json=body)
        if resp.status_code != 200:
            raise error_from_response(resp, self.name)
        data = resp.json()
        if data.get("error"):
            raise LLMError(f"{self.name} error: {data['error']}", retryable=True)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.name} returned no choices", retryable=True)
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        return LLMResult(text=message.get("content") or "", provider=self.name,
                         model=data.get("model") or self.model,
                         input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                         output_tokens=int(usage.get("completion_tokens", 0) or 0),
                         finish_reason=choices[0].get("finish_reason"), raw={"usage": usage})

    def close(self) -> None:
        self.client.close()


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    BASE_URL = "https://api.anthropic.com/v1"

    def __init__(self, api_key: str, model: str, base_url: str | None = None, **kw):
        super().__init__(model, **kw)
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.client = httpx.Client(timeout=self.timeout, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    def _complete(self, system: str, user: str, temperature: float, max_output_tokens: int) -> LLMResult:
        body = {"model": self.model, "max_tokens": max_output_tokens, "temperature": temperature,
                "system": system, "messages": [{"role": "user", "content": user}]}
        resp = self.client.post(f"{self.base_url}/messages", json=body)
        if resp.status_code != 200:
            raise error_from_response(resp, self.name)
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        return LLMResult(text=text, provider=self.name, model=data.get("model") or self.model,
                         input_tokens=int(usage.get("input_tokens", 0) or 0),
                         output_tokens=int(usage.get("output_tokens", 0) or 0),
                         finish_reason=data.get("stop_reason"), raw={"usage": usage})

    def close(self) -> None:
        self.client.close()
