-- Schema per design.md Section 3. Single Postgres datastore: RAG store (pgvector),
-- domain/structured data, and conversation/session state.

CREATE EXTENSION IF NOT EXISTS vector;

-- === RAG store ===============================================================

CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type TEXT NOT NULL CHECK (source_type IN ('regulatory', 'logistics_network', 'supplier', 'general')),
  title TEXT NOT NULL,
  source_url TEXT,
  region TEXT,
  material_category TEXT,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_text TEXT NOT NULL,
  embedding VECTOR(768), -- gemini-embedding-001, truncated to 768 dims (design.md Section 5.1)
  chunk_index INT NOT NULL
);

CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
  ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- === Domain / structured data ================================================

CREATE TABLE IF NOT EXISTS sku_master (
  sku_code TEXT PRIMARY KEY,
  material_name TEXT NOT NULL,
  material_category TEXT NOT NULL,
  hsn_code TEXT,
  unit_of_measure TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  location_city TEXT,
  location_state TEXT,
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  materials_supplied TEXT[],
  certifications TEXT[]
);

CREATE TABLE IF NOT EXISTS logistics_nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  node_type TEXT NOT NULL CHECK (node_type IN ('port', 'rail_terminal', 'warehouse', 'icd')),
  name TEXT NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  lon DOUBLE PRECISION NOT NULL,
  state TEXT
);

CREATE TABLE IF NOT EXISTS transport_benchmarks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  origin_node_id UUID NOT NULL REFERENCES logistics_nodes(id),
  destination_node_id UUID NOT NULL REFERENCES logistics_nodes(id),
  mode TEXT NOT NULL CHECK (mode IN ('road', 'rail', 'air', 'sea', 'multimodal')),
  distance_km NUMERIC,
  typical_transit_days NUMERIC,
  source TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS transport_benchmarks_route_idx
  ON transport_benchmarks (origin_node_id, destination_node_id, mode);

-- === Conversation / session state =============================================

CREATE TABLE IF NOT EXISTS sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
  content TEXT NOT NULL,
  slots_filled JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_session_id_idx ON messages (session_id);
