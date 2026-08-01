"""Gemini adapter — active provider for both generation and embeddings (design.md Section 5.1)."""

from __future__ import annotations

from typing import Iterator

from google import genai
from google.genai import types

from scai.providers.base import EmbeddingProvider, GenerateResponse, LLMProvider, Message


class GeminiProvider(LLMProvider, EmbeddingProvider):
    def __init__(self, api_key: str, generation_model: str, embedding_model: str, embedding_dims: int):
        self._client = genai.Client(api_key=api_key)
        self._generation_model = generation_model
        self._embedding_model = embedding_model
        self._embedding_dims = embedding_dims

    def _to_contents_and_config(self, messages: list[Message], **kwargs) -> tuple[list[types.Content], types.GenerateContentConfig]:
        system_instruction = next((m.content for m in messages if m.role == "system"), None)
        contents = [
            types.Content(role=("model" if m.role == "assistant" else "user"), parts=[types.Part(text=m.content)])
            for m in messages
            if m.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=kwargs.get("temperature", 0.2),
        )
        return contents, config

    def generate(self, messages: list[Message], **kwargs) -> GenerateResponse:
        contents, config = self._to_contents_and_config(messages, **kwargs)
        response = self._client.models.generate_content(
            model=self._generation_model, contents=contents, config=config
        )
        return GenerateResponse(text=response.text, provider="gemini", model=self._generation_model)

    def generate_stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        contents, config = self._to_contents_and_config(messages, **kwargs)
        for chunk in self._client.models.generate_content_stream(
            model=self._generation_model, contents=contents, config=config
        ):
            if chunk.text:
                yield chunk.text

    def embed(self, texts: list[str]) -> list[list[float]]:
        config = types.EmbedContentConfig(output_dimensionality=self._embedding_dims)
        response = self._client.models.embed_content(
            model=self._embedding_model, contents=texts, config=config
        )
        return [e.values for e in response.embeddings]
