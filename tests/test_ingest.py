"""Chunker tests — the ingestion step where a bug silently degrades every future retrieval."""

from scai.ingest import chunk_text


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_short_text_is_a_single_chunk():
    assert chunk_text("A short regulatory note.") == ["A short regulatory note."]


def test_paragraphs_are_grouped_up_to_the_target():
    text = "\n\n".join(["para " + str(i) + " " + "x" * 100 for i in range(20)])
    chunks = chunk_text(text, target=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 500 + 50 for c in chunks), [len(c) for c in chunks]


def test_oversized_paragraph_is_hard_split():
    chunks = chunk_text("y" * 5000, target=1000, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_no_content_is_dropped_across_chunk_boundaries():
    """Overlap may duplicate text, but nothing may go missing."""
    paragraphs = [f"Fact number {i} about steel freight." for i in range(40)]
    chunks = chunk_text("\n\n".join(paragraphs), target=300, overlap=40)
    joined = " ".join(chunks)
    for i in range(40):
        assert f"Fact number {i} " in joined, f"paragraph {i} was lost during chunking"
