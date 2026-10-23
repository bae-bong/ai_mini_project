CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_collections (
    name TEXT PRIMARY KEY CHECK (name IN ('pdf', 'web')),
    embedding_model TEXT NOT NULL,
    vector_dimension INTEGER NOT NULL CHECK (vector_dimension > 0),
    manifest JSONB NOT NULL DEFAULT '{}',
    supplements JSONB NOT NULL DEFAULT '[]',
    UNIQUE (name, vector_dimension)
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    collection TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding VECTOR NOT NULL,
    PRIMARY KEY (collection, chunk_id),
    FOREIGN KEY (collection, dimensions)
        REFERENCES rag_collections(name, vector_dimension),
    CHECK (vector_dims(embedding) = dimensions)
);
