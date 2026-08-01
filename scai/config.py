"""Loads config.yaml (non-secret settings) layered with .env (secrets); env vars win."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class GenerationConfig:
    provider: str
    model: str


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimensions: int


@dataclass(frozen=True)
class FallbackConfig:
    provider: str
    model: str
    base_url: str


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int
    similarity_threshold: float


@dataclass(frozen=True)
class Config:
    generation: GenerationConfig
    embedding: EmbeddingConfig
    fallback: FallbackConfig
    db_dsn: str
    retrieval: RetrievalConfig
    gemini_api_key: str | None


def _env_override(value: str, env_var: str) -> str:
    return os.environ.get(env_var, value)


def load_config(path: Path | None = None) -> Config:
    path = path or PROJECT_ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text())

    llm = raw["llm"]
    generation = GenerationConfig(
        provider=_env_override(llm["generation"]["provider"], "SCAI_GENERATION_PROVIDER"),
        model=_env_override(llm["generation"]["model"], "SCAI_GENERATION_MODEL"),
    )
    embedding = EmbeddingConfig(
        provider=_env_override(llm["embedding"]["provider"], "SCAI_EMBEDDING_PROVIDER"),
        model=_env_override(llm["embedding"]["model"], "SCAI_EMBEDDING_MODEL"),
        dimensions=int(llm["embedding"]["dimensions"]),
    )
    fallback = FallbackConfig(
        provider=_env_override(llm["fallback"]["provider"], "SCAI_FALLBACK_PROVIDER"),
        model=_env_override(llm["fallback"]["model"], "SCAI_FALLBACK_MODEL"),
        base_url=_env_override(llm["fallback"]["base_url"], "OLLAMA_BASE_URL"),
    )
    retrieval = RetrievalConfig(
        top_k=int(raw["retrieval"]["top_k"]),
        similarity_threshold=float(raw["retrieval"]["similarity_threshold"]),
    )

    return Config(
        generation=generation,
        embedding=embedding,
        fallback=fallback,
        db_dsn=_env_override(raw["db"]["dsn"], "SCAI_DB_DSN"),
        retrieval=retrieval,
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
    )
