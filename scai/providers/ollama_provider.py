"""Ollama adapter — local generation fallback only (design.md Section 5.1: embeddings are never
auto-fallback, since switching the embedding vector space requires re-embedding the corpus)."""

from __future__ import annotations

from typing import Iterator

import ollama

from scai.providers.base import GenerateResponse, LLMProvider, Message


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, base_url: str):
        self._client = ollama.Client(host=base_url)
        self._model = model

    def generate(self, messages: list[Message], **kwargs) -> GenerateResponse:
        ollama_messages = [{"role": m.role, "content": m.content} for m in messages]
        response = self._client.chat(
            model=self._model,
            messages=ollama_messages,
            options={"temperature": kwargs.get("temperature", 0.2)},
        )
        return GenerateResponse(text=response.message.content, provider="ollama", model=self._model)

    def generate_stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        ollama_messages = [{"role": m.role, "content": m.content} for m in messages]
        for chunk in self._client.chat(
            model=self._model,
            messages=ollama_messages,
            stream=True,
            options={"temperature": kwargs.get("temperature", 0.2)},
        ):
            if chunk.message.content:
                yield chunk.message.content
