"""Knowledge base ingestion: load -> chunk -> embed -> store in Postgres/pgvector.

Usage (from the project root, with the venv active):

    # one file
    python -m scai.ingest --file docs/eway_bill_rules.pdf --type regulatory --title "E-Way Bill Rules"

    # a whole directory (.pdf/.txt/.md), title inferred from each filename
    python -m scai.ingest --dir docs/regulatory --type regulatory

    # a web page
    python -m scai.ingest --url https://example.gov.in/circular --type regulatory --title "Circular 12/2026"

    # optional metadata used as retrieval filters (design.md Section 3)
    ... --region Maharashtra --category steel

    # replace an earlier ingest of the same source instead of duplicating it
    ... --replace

Re-running the same source without --replace is refused, so an accidental double ingest can't
silently double every chunk's weight in retrieval.
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
import requests
from pgvector import Vector

from scai import db
from scai.config import Config, load_config
from scai.providers.router import ProviderRouter

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}
SOURCE_TYPES = ("regulatory", "logistics_network", "supplier", "general")

# Gemini's embed endpoint takes batches; 32 keeps requests small enough to stay well inside
# per-request payload limits while still cutting round-trips by ~30x versus one-at-a-time.
EMBED_BATCH_SIZE = 32

CHUNK_TARGET_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150


@dataclass(frozen=True)
class LoadedDocument:
    title: str
    text: str
    source_url: str | None


# ---------------------------------------------------------------- loading


def _read_pdf(source) -> str:
    """Accepts a path or a binary file-like object (so URL-hosted PDFs reuse this path)."""
    from pypdf import PdfReader

    reader = PdfReader(str(source) if isinstance(source, Path) else source)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def load_file(path: Path) -> LoadedDocument:
    if path.suffix.lower() == ".pdf":
        text = _read_pdf(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return LoadedDocument(title=path.stem.replace("_", " "), text=text, source_url=str(path))


# Elements whose text is site chrome, not content. Left in, they pollute every chunk with menu
# items and bury the actual passage the retriever should match on.
# NOT included: <form> — ASP.NET WebForms (most .gov.in portals) wraps the entire page body in a
# single <form runat="server">, so stripping it discards the whole document.
_CHROME_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript", "select", "svg")

_HTML_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&rsquo;": "’", "&lsquo;": "‘", "&ldquo;": "“", "&rdquo;": "”", "&ndash;": "–", "&mdash;": "—",
}


def load_url(url: str) -> LoadedDocument:
    """Fetches a page, drops site chrome, and strips remaining markup. Still crude — where layout
    carries the meaning (rate tables), download the source and ingest the file instead."""
    resp = requests.get(url, timeout=60, headers={"User-Agent": "scai-ingest/0.1"})
    resp.raise_for_status()

    # Many authoritative sources are PDFs served over http — route those to the PDF reader
    # instead of running HTML stripping over binary content.
    if "application/pdf" in resp.headers.get("Content-Type", "").lower() or url.lower().endswith(".pdf"):
        text = _read_pdf(io.BytesIO(resp.content))
        title = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".pdf").replace("_", " ")
        return LoadedDocument(title=title, text=text, source_url=url)

    resp.encoding = resp.apparent_encoding or resp.encoding
    html = resp.text

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else url

    body = html
    for tag in _CHROME_TAGS:
        body = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    # Block-level tags become paragraph breaks so the chunker has real boundaries to split on.
    body = re.sub(r"</(p|div|section|article|tr|h[1-6]|li|br)\s*>", "\n\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<br\s*/?>", "\n\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)

    for entity, char in _HTML_ENTITIES.items():
        body = body.replace(entity, char)
    body = re.sub(r"&#\d+;", " ", body)
    # Some publishers (PIB among them) double-encode their markup, so decoding entities above
    # turns &lt;p&gt; into a real tag. Strip once more, or that markup lands in the chunk text.
    body = re.sub(r"<[^>]+>", " ", body)

    body = re.sub(r"[ \t\r]+", " ", body)
    body = re.sub(r" ?\n ?", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    # Drop leftover one- or two-word lines — almost always surviving menu labels.
    lines = [ln.strip() for ln in body.split("\n")]
    kept = [ln for ln in lines if not ln or len(ln.split()) > 2]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))

    return LoadedDocument(title=title, text=body.strip(), source_url=url)


# ---------------------------------------------------------------- chunking


def chunk_text(text: str, target: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Splits on paragraph boundaries where possible so a chunk stays a coherent passage, with a
    character overlap to keep a fact that straddles a boundary retrievable from either side."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # A single oversized paragraph gets hard-split rather than blowing past the target.
        while len(para) > target:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:target])
            para = para[target - overlap :]

        if not current:
            current = para
        elif len(current) + 2 + len(para) <= target:
            current = f"{current}\n\n{para}"
        else:
            chunks.append(current)
            current = f"{current[-overlap:]}\n\n{para}" if overlap else para

    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------- storing


def _existing_document_id(conn: psycopg.Connection, source_url: str) -> UUID | None:
    row = conn.execute("SELECT id FROM documents WHERE source_url = %s", (source_url,)).fetchone()
    return row[0] if row else None


def ingest_document(
    conn: psycopg.Connection,
    router: ProviderRouter,
    doc: LoadedDocument,
    source_type: str,
    title_override: str | None = None,
    region: str | None = None,
    material_category: str | None = None,
    replace: bool = False,
) -> tuple[UUID, int]:
    title = title_override or doc.title
    chunks = chunk_text(doc.text)
    if not chunks:
        raise ValueError(f"No extractable text in {doc.source_url!r} — if it's a scanned PDF it needs OCR first.")

    if doc.source_url:
        existing = _existing_document_id(conn, doc.source_url)
        if existing is not None:
            if not replace:
                raise ValueError(
                    f"{doc.source_url!r} is already ingested. Re-run with --replace to overwrite it."
                )
            # ON DELETE CASCADE clears the old chunks with it.
            conn.execute("DELETE FROM documents WHERE id = %s", (existing,))

    document_id = conn.execute(
        """
        INSERT INTO documents (source_type, title, source_url, region, material_category)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (source_type, title, doc.source_url, region, material_category),
    ).fetchone()[0]

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors = router.embed(batch)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO document_chunks (document_id, chunk_text, embedding, chunk_index)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (document_id, chunk_text_, Vector(vector), start + offset)
                    for offset, (chunk_text_, vector) in enumerate(zip(batch, vectors))
                ],
            )
        logger.info("  embedded %d/%d chunks", min(start + EMBED_BATCH_SIZE, len(chunks)), len(chunks))

    return document_id, len(chunks)


# ---------------------------------------------------------------- cli


@dataclass(frozen=True)
class SourceSpec:
    """One entry to ingest, with its own metadata (manifest entries each carry their own type)."""

    loader: str  # 'url' | 'file'
    target: str
    source_type: str
    title: str | None = None
    region: str | None = None
    material_category: str | None = None

    def load(self) -> LoadedDocument:
        return load_url(self.target) if self.loader == "url" else load_file(Path(self.target))


def load_manifest(path: Path, default_type: str | None) -> list[SourceSpec]:
    """A manifest keeps the knowledge base reproducible — provenance is recorded in the repo, and
    a rebuild is one command rather than remembered shell history."""
    import yaml

    raw = yaml.safe_load(path.read_text()) or {}
    entries = raw.get("sources") or []
    if not entries:
        raise SystemExit(f"No 'sources:' entries in {path}")

    specs: list[SourceSpec] = []
    for i, entry in enumerate(entries):
        source_type = entry.get("type", default_type)
        if source_type not in SOURCE_TYPES:
            raise SystemExit(f"{path} entry {i}: 'type' must be one of {SOURCE_TYPES}, got {source_type!r}")
        if bool(entry.get("url")) == bool(entry.get("file")):
            raise SystemExit(f"{path} entry {i}: needs exactly one of 'url' or 'file'")

        specs.append(
            SourceSpec(
                loader="url" if entry.get("url") else "file",
                target=entry.get("url") or entry["file"],
                source_type=source_type,
                title=entry.get("title"),
                region=entry.get("region"),
                material_category=entry.get("category"),
            )
        )
    return specs


def _collect_sources(args: argparse.Namespace) -> list[SourceSpec]:
    common = dict(
        source_type=args.source_type,
        title=args.title,
        region=args.region,
        material_category=args.material_category,
    )
    if args.url:
        return [SourceSpec(loader="url", target=args.url, **common)]
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            raise SystemExit(f"Not a file: {path}")
        return [SourceSpec(loader="file", target=str(path), **common)]

    directory = Path(args.dir)
    if not directory.is_dir():
        raise SystemExit(f"Not a directory: {directory}")
    paths = sorted(p for p in directory.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not paths:
        raise SystemExit(f"No {'/'.join(sorted(SUPPORTED_SUFFIXES))} files under {directory}")
    return [SourceSpec(loader="file", target=str(p), **{**common, "title": None}) for p in paths]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Ingest documents into the pgvector knowledge base.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="single .pdf/.txt/.md file")
    source.add_argument("--dir", help="directory to ingest recursively")
    source.add_argument("--url", help="web page or PDF to fetch and ingest")
    source.add_argument("--manifest", help="YAML file listing sources (see knowledge_base/sources.yaml)")

    parser.add_argument("--type", dest="source_type", choices=SOURCE_TYPES, help="required unless --manifest sets it per entry")
    parser.add_argument("--title", help="overrides the inferred title (single source only)")
    parser.add_argument("--region", help="retrieval filter, e.g. a state name")
    parser.add_argument("--category", dest="material_category", help="retrieval filter, e.g. 'steel'")
    parser.add_argument("--replace", action="store_true", help="overwrite a previously ingested source")
    args = parser.parse_args(argv)

    if not args.manifest and not args.source_type:
        parser.error("--type is required unless using --manifest")
    if args.title and (args.dir or args.manifest):
        parser.error("--title applies to a single source; omit it with --dir/--manifest")

    specs = (
        load_manifest(Path(args.manifest), args.source_type)
        if args.manifest
        else _collect_sources(args)
    )

    config: Config = load_config()
    router = ProviderRouter(config)

    total_chunks = 0
    failures = 0
    with db.connect(config) as conn:
        for spec in specs:
            try:
                doc = spec.load()
                _, count = ingest_document(
                    conn,
                    router,
                    doc,
                    source_type=spec.source_type,
                    title_override=spec.title,
                    region=spec.region,
                    material_category=spec.material_category,
                    replace=args.replace,
                )
            except Exception as exc:  # noqa: BLE001 - one bad source shouldn't abort the batch
                failures += 1
                logger.error("FAILED %s: %s", spec.target, exc)
                continue
            total_chunks += count
            logger.info("ingested %s (%d chunks)", spec.title or doc.title, count)

    logger.info(
        "\nDone: %d/%d sources, %d chunks embedded at %d dims.",
        len(specs) - failures,
        len(specs),
        total_chunks,
        config.embedding.dimensions,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
