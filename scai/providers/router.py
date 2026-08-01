"""Provider router: primary -> fallback chain for generation; embeddings always use the
configured embedding provider directly, no fallback (design.md Section 5/5.1/7)."""

from __future__ import annotations

import logging
from typing import Iterator

from scai.config import Config
from scai.providers.base import GenerateResponse, LLMProvider, Message
from scai.providers.gemini_provider import GeminiProvider
from scai.providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ProviderRouter:
    def __init__(self, config: Config):
        self._gemini = GeminiProvider(
            api_key=config.gemini_api_key,
            generation_model=config.generation.model,
            embedding_model=config.embedding.model,
            embedding_dims=config.embedding.dimensions,
        )
        self._fallback: LLMProvider = OllamaProvider(
            model=config.fallback.model, base_url=config.fallback.base_url
        )
        self._primary_provider_name = config.generation.provider

    def generate(self, messages: list[Message], **kwargs) -> GenerateResponse:
        try:
            return self._gemini.generate(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001 - provider failure triggers fallback, not a crash
            logger.warning("Primary provider (gemini) failed, falling back to ollama: %s", exc)
            try:
                return self._fallback.generate(messages, **kwargs)
            except Exception as fallback_exc:
                raise RuntimeError(
                    "All LLM providers exhausted (gemini, ollama) — "
                    f"gemini error: {exc}; ollama error: {fallback_exc}"
                ) from fallback_exc

    def generate_stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        """Same primary->fallback chain as generate(), but only swaps providers before any
        text has reached the caller — once streaming has started, a mid-stream failure is
        surfaced as an error rather than silently splicing two providers' output together."""
        yielded_any = False
        try:
            for chunk in self._gemini.generate_stream(messages, **kwargs):
                yielded_any = True
                yield chunk
            return
        except Exception as exc:  # noqa: BLE001
            if yielded_any:
                raise RuntimeError(f"gemini stream interrupted after partial output: {exc}") from exc
            logger.warning("Primary provider (gemini) failed before streaming any output, falling back to ollama: %s", exc)

        try:
            for chunk in self._fallback.generate_stream(messages, **kwargs):
                yield chunk
        except Exception as fallback_exc:
            raise RuntimeError(
                f"All LLM providers exhausted while streaming (gemini, ollama) — ollama error: {fallback_exc}"
            ) from fallback_exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._gemini.embed(texts)
