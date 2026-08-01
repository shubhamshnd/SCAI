"""Router fallback-chain contract tests (design.md Section 5/7) — no network calls; provider
generate() methods are monkeypatched so this exercises only the router's own failover logic."""

import pytest

from scai.config import Config, EmbeddingConfig, FallbackConfig, GenerationConfig, RetrievalConfig
from scai.providers.base import GenerateResponse, Message
from scai.providers.router import ProviderRouter


@pytest.fixture
def router() -> ProviderRouter:
    config = Config(
        generation=GenerationConfig(provider="gemini", model="gemini-3.6-flash"),
        embedding=EmbeddingConfig(provider="gemini", model="gemini-embedding-001", dimensions=768),
        fallback=FallbackConfig(provider="ollama", model="llama3.1", base_url="http://localhost:11434"),
        db_dsn="postgresql:///scai",
        retrieval=RetrievalConfig(top_k=5, similarity_threshold=0.5),
        gemini_api_key="dummy-key-not-used-network-mocked",
    )
    return ProviderRouter(config)


def test_generate_uses_primary_when_it_succeeds(router, monkeypatch):
    monkeypatch.setattr(
        router._gemini, "generate", lambda messages, **kw: GenerateResponse("hi", "gemini", "gemini-3.6-flash")
    )
    response = router.generate([Message(role="user", content="hello")])
    assert response.provider == "gemini"


def test_generate_falls_back_when_primary_fails(router, monkeypatch):
    def _raise(messages, **kw):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(router._gemini, "generate", _raise)
    monkeypatch.setattr(
        router._fallback, "generate", lambda messages, **kw: GenerateResponse("hi", "ollama", "llama3.1")
    )
    response = router.generate([Message(role="user", content="hello")])
    assert response.provider == "ollama"


def test_generate_raises_clear_error_when_both_fail(router, monkeypatch):
    monkeypatch.setattr(router._gemini, "generate", lambda messages, **kw: (_ for _ in ()).throw(RuntimeError("gemini down")))
    monkeypatch.setattr(router._fallback, "generate", lambda messages, **kw: (_ for _ in ()).throw(RuntimeError("ollama down")))

    with pytest.raises(RuntimeError, match="All LLM providers exhausted"):
        router.generate([Message(role="user", content="hello")])
