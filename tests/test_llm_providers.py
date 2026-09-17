"""Provider wire formats, retries and error hints — against httpx.MockTransport (no network)."""

import json
import unittest

import httpx

from tests.helpers import ROOT  # noqa: F401
from unifaculty.config import LLMSettings
from unifaculty.llm.base import LLMError
from unifaculty.llm.factory import ProviderConfigError, build_provider
from unifaculty.llm.providers import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider


def mock_client(handler, headers=None):
    return httpx.Client(transport=httpx.MockTransport(handler), headers=headers or {})


class GeminiTests(unittest.TestCase):
    def test_request_shape_and_parsing(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["url"] = str(request.url)
            seen["key"] = request.headers.get("x-goog-api-key")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 8}})

        p = GeminiProvider(api_key="fake-gemini-key", model="gemini-3.5-flash-lite", sleep=lambda s: None)
        p.client = mock_client(handler, {"x-goog-api-key": "fake-gemini-key"})
        result = p.complete_json("sys", "user")
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual((result.input_tokens, result.output_tokens), (120, 8))
        self.assertNotIn("key=", seen["url"])                      # key never in the URL
        self.assertEqual(seen["key"], "fake-gemini-key")
        self.assertEqual(seen["body"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertTrue(seen["url"].endswith("/models/gemini-3.5-flash-lite:generateContent"))

    def test_location_error_has_hint_and_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(403, json={"error": {"message": "User location is not supported for the API use."}})

        p = GeminiProvider(api_key="k" * 20, model="m", sleep=lambda s: None)
        p.client = mock_client(handler)
        with self.assertRaises(LLMError) as ctx:
            p.complete_json("s", "u")
        self.assertIn("location", ctx.exception.hint)
        self.assertEqual(len(calls), 1)


class RetryTests(unittest.TestCase):
    def test_429_is_retried_with_retry_after(self):
        responses = [httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}}),
                     httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                               "usage": {"prompt_tokens": 5, "completion_tokens": 1}})]
        sleeps = []
        p = OpenAICompatibleProvider(model="openrouter/free", base_url="https://openrouter.ai/api/v1", api_key="sk-or-v1-x",
                                     name="openrouter", sleep=sleeps.append)
        p.client = mock_client(lambda r: responses.pop(0))
        result = p.complete_json("s", "u")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(sleeps, [3.0])

    def test_json_mode_fallback(self):
        bodies = []

        def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            if "response_format" in body:
                return httpx.Response(400, text='{"error": "response_format is not supported by this model"}')
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"a":1}'}}]})

        p = OpenAICompatibleProvider(model="some/free-model", base_url="http://localhost:11434/v1", name="ollama",
                                     sleep=lambda s: None)
        p.client = mock_client(handler)
        self.assertEqual(p.complete_json("s", "u").text, '{"a":1}')
        self.assertIn("response_format", bodies[0])
        self.assertNotIn("response_format", bodies[1])


class AnthropicTests(unittest.TestCase):
    def test_parsing(self):
        def handler(request):
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            return httpx.Response(200, json={"content": [{"type": "text", "text": '{"x": 1}'}], "model": "claude-haiku-4-5-20251001",
                                             "usage": {"input_tokens": 10, "output_tokens": 3}, "stop_reason": "end_turn"})

        p = AnthropicProvider(api_key="sk-ant-" + "x" * 30, model="claude-haiku-4-5-20251001", sleep=lambda s: None)
        p.client = mock_client(handler, {"anthropic-version": "2023-06-01"})
        r = p.complete_json("s", "u")
        self.assertEqual((r.text, r.output_tokens, r.finish_reason), ('{"x": 1}', 3, "end_turn"))


class FactoryTests(unittest.TestCase):
    def test_missing_key_is_a_clear_config_error(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
        try:
            with self.assertRaises(ProviderConfigError) as ctx:
                build_provider(LLMSettings(provider="gemini"))
            self.assertIn("GEMINI_API_KEY", str(ctx.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_ollama_needs_no_key(self):
        p = build_provider(LLMSettings(provider="ollama", model="llama3.1:8b"))
        self.assertEqual(p.name, "ollama")
        self.assertEqual(p.base_url, "http://localhost:11434/v1")


if __name__ == "__main__":
    unittest.main()
