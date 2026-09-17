"""Build the configured provider from settings + environment variables."""

from __future__ import annotations

import os

from unifaculty.config import LLMSettings
from unifaculty.llm.base import LLMProvider

DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "openrouter": "openrouter/free",
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "llama3.1:8b",
    "openai_compat": "(set llm.model)",
    "mock": "mock-1",
}

KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai_compat": ("OPENAI_API_KEY",),
    "ollama": (),
    "mock": (),
}


class ProviderConfigError(RuntimeError):
    pass


def _key(provider: str) -> str | None:
    for env in KEY_ENV[provider]:
        value = os.environ.get(env)
        if value:
            return value
    return None


def build_provider(settings: LLMSettings) -> LLMProvider:
    provider = settings.provider
    model = settings.model or DEFAULT_MODELS[provider]
    common = {"timeout": settings.timeout_seconds, "max_retries": settings.max_retries}

    if provider == "mock":
        from unifaculty.llm.mock import MockProvider
        return MockProvider(model=model, **common)

    from unifaculty.llm.providers import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider

    if provider == "ollama":
        return OpenAICompatibleProvider(model=model, base_url=settings.base_url or "http://localhost:11434/v1",
                                        name="ollama", **common)
    key = _key(provider)
    if not key:
        envs = " or ".join(KEY_ENV[provider])
        raise ProviderConfigError(f"No API key for '{provider}'. Set {envs} in your environment or .env "
                                  "(see .env.example), or run with --llm mock for a dry run.")
    if provider == "gemini":
        return GeminiProvider(api_key=key, model=model, base_url=settings.base_url, **common)
    if provider == "anthropic":
        return AnthropicProvider(api_key=key, model=model, base_url=settings.base_url, **common)
    if provider == "openrouter":
        return OpenAICompatibleProvider(model=model, base_url=settings.base_url or "https://openrouter.ai/api/v1",
                                        api_key=key, name="openrouter", **common)
    if provider == "openai_compat":
        if not settings.base_url or not settings.model:
            raise ProviderConfigError("openai_compat needs llm.base_url and llm.model in config/settings.yaml")
        return OpenAICompatibleProvider(model=model, base_url=settings.base_url, api_key=key, **common)
    raise ProviderConfigError(f"unknown provider {provider}")
