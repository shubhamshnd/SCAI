"""LLMProvider interface — every adapter normalizes to this shape (design.md Section 5)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Message:
    role: str  # 'user' | 'assistant' | 'system'
    content: str


@dataclass(frozen=True)
class GenerateResponse:
    text: str
    provider: str
    model: str


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, messages: list[Message], **kwargs) -> GenerateResponse:
        ...

    @abstractmethod
    def generate_stream(self, messages: list[Message], **kwargs) -> Iterator[str]:
        """Yields text deltas as they arrive, for token-by-token UI streaming."""
        ...


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...
