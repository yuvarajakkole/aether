from __future__ import annotations

import os
from typing import Any

from aether.crypto import decrypt
from aether.models import LLMModel, Provider
from aether.providers.anthropic import AnthropicClient
from aether.providers.base import LLMClient, ModelConfig, ProviderError
from aether.providers.bedrock import BedrockConverseClient
from aether.providers.mock import MockClient
from aether.providers.openai_chat import OpenAIChatClient
from aether.providers.openai_responses import OpenAIResponsesClient

ADAPTERS: dict[str, type[LLMClient]] = {
    "openai_chat": OpenAIChatClient,
    "openai_responses": OpenAIResponsesClient,
    "anthropic": AnthropicClient,
    "bedrock_converse": BedrockConverseClient,
    "mock": MockClient,
}

# Presets shown in the UI. Nothing here is required; every field can be overridden.
PRESETS: list[dict[str, Any]] = [
    {"id": "openai", "label": "OpenAI", "kind": "openai_chat", "base_url": "https://api.openai.com/v1", "options": {}},
    {"id": "openai_responses", "label": "OpenAI (Responses API)", "kind": "openai_responses", "base_url": "https://api.openai.com/v1", "options": {}},
    {"id": "anthropic", "label": "Anthropic", "kind": "anthropic", "base_url": "https://api.anthropic.com", "options": {}},
    {
        "id": "azure_openai",
        "label": "Azure OpenAI",
        "kind": "openai_chat",
        "base_url": "https://<resource>.openai.azure.com/openai/deployments/<deployment>",
        "options": {"auth_style": "api-key", "query": {"api-version": "2024-10-21"}},
    },
    {"id": "bedrock", "label": "Amazon Bedrock (API key)", "kind": "bedrock_converse", "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com", "options": {}},
    {"id": "ollama", "label": "Ollama / vLLM / any OpenAI-compatible server", "kind": "openai_chat", "base_url": "http://localhost:11434/v1", "options": {"auth_style": "none"}},
    {"id": "gateway", "label": "Corporate LLM gateway (OpenAI-compatible)", "kind": "openai_chat", "base_url": "https://<gateway>/v1", "options": {}, "headers": {"x-client-app": "<app-name>"}},
    {"id": "mock", "label": "Mock (offline, no key)", "kind": "mock", "base_url": None, "options": {}},
]


def resolve_api_key(provider: Provider) -> str | None:
    if provider.api_key_enc:
        return decrypt(provider.api_key_enc)
    if provider.api_key_env:
        return os.environ.get(provider.api_key_env)
    return None


def model_config(model: LLMModel, provider: Provider | None = None) -> ModelConfig:
    provider = provider or model.provider
    if provider.kind not in ADAPTERS:
        raise ProviderError(f"unknown provider kind {provider.kind!r}")
    return ModelConfig(
        provider_kind=provider.kind,
        provider_name=provider.name,
        model=model.name,
        base_url=provider.base_url,
        api_key=resolve_api_key(provider),
        headers=dict(provider.headers or {}),
        provider_options=dict(provider.options or {}),
        model_options=dict(model.options or {}),
        input_cost_per_mtok=model.input_cost_per_mtok or 0.0,
        output_cost_per_mtok=model.output_cost_per_mtok or 0.0,
        rpm_limit=model.rpm_limit,
        tpm_limit=model.tpm_limit,
        limiter_key=model.id,
    )


def client_for(model: LLMModel, provider: Provider | None = None) -> LLMClient:
    cfg = model_config(model, provider)
    return ADAPTERS[cfg.provider_kind](cfg)


def client_from_config(cfg: ModelConfig) -> LLMClient:
    if cfg.provider_kind not in ADAPTERS:
        raise ProviderError(f"unknown provider kind {cfg.provider_kind!r}")
    return ADAPTERS[cfg.provider_kind](cfg)
