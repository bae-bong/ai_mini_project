"""PostgreSQL stores vectors, metadata, model settings and addenda together.

Exact squared L2 preserves the previous FAISS distance contract. The MVP has
hundreds of law chunks; approximate indexes can follow measured growth.
"""
import hashlib
import json
import math
import os

import psycopg
from psycopg.types.json import Jsonb
from langchain_core.documents import Document

DEFAULT_MODEL = "text-embedding-ada-002"

# Group notice overview and attachments together; laws retain file-level identity.
DOCUMENT_KEY_SQL = """CASE
    WHEN collection='web' AND NULLIF(metadata->>'notice_id', '') IS NOT NULL
        THEN 'notice:' || (metadata->>'notice_id')
    WHEN NULLIF(metadata->>'source_file', '') IS NOT NULL
        THEN 'file:' || (metadata->>'source_file')
    ELSE 'chunk:' || chunk_id END"""


def connect():
    # libpq also accepts PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.
    return psycopg.connect(os.getenv("RAG_DATABASE_URL", ""), connect_timeout=5)


def vector_literal(values):
    values = [float(v) for v in values]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError("임베딩은 비어 있지 않은 유한 숫자 배열이어야 합니다.")
    return json.dumps(values, allow_nan=False)


class PgVectorStore:
    def __init__(self, collection="pdf", *, model=None, dimensions=None, embedding_client=None):
        if collection not in {"pdf", "web"}:
            raise ValueError("collection은 pdf 또는 web이어야 합니다.")
        self.collection = collection
        self.model = model or os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
        self.dimensions = dimensions
        if dimensions is None and model is None and os.getenv("EMBEDDING_DIMENSIONS"):
            self.dimensions = int(os.environ["EMBEDDING_DIMENSIONS"])
        self.embedding_client = embedding_client

    def _client(self, dimensions=None):
        if self.embedding_client is not None:
            return self.embedding_client
        from law_pipeline.index import embeddings
        return embeddings(self.model, self.dimensions if dimensions is None else dimensions)

    def info(self):
        with connect() as conn:
            row = conn.execute(
                "SELECT embedding_model, vector_dimension, manifest, supplements, "
                "(SELECT count(*) FROM rag_chunks WHERE collection = name) "
                "FROM rag_collections WHERE name = %s", (self.collection,)
            ).fetchone()
        if row is None:
            return None
        return dict(embedding_model=row[0], vector_dimension=row[1], manifest=row[2],
                    supplements=row[3], vectors=row[4])

    def duplicate_upload(self, upload_hash):
        with connect() as conn:
            row = conn.execute(
                "SELECT metadata->>'source_file' FROM rag_chunks WHERE collection=%s "
                "AND metadata->>'upload_hash'=%s LIMIT 1", (self.collection, upload_hash)).fetchone()
        return row[0] if row else None

    def list_documents(self):
        with connect() as conn:
            rows = conn.execute(f"""
                SELECT {DOCUMENT_KEY_SQL} AS document_key,
                       min(COALESCE(NULLIF(metadata->>'law_name',''), NULLIF(metadata->>'title',''),
                                    metadata->>'source_file', chunk_id)),
                       count(*), array_remove(array_agg(DISTINCT metadata->>'source_file'), NULL),
                       max(metadata->>'ingested_at')
                FROM rag_chunks WHERE collection=%s GROUP BY document_key ORDER BY 2, 1
                """, (self.collection,)).fetchall()
        return [dict(key=r[0], title=r[1], chunks=r[2], files=r[3], ingested_at=r[4]) for r in rows]

    def preview_document(self, key):
        with connect() as conn:
            rows = conn.execute(f"""
                SELECT left(content, 1200), metadata->>'source_file', metadata->>'page',
                       metadata->>'article_no'
                FROM rag_chunks WHERE collection=%s AND ({DOCUMENT_KEY_SQL})=%s
                ORDER BY metadata->>'source_file', chunk_id LIMIT 3
                """, (self.collection, key)).fetchall()
        return [dict(text=r[0], source_file=r[1], page=r[2], article=r[3]) for r in rows]

    def delete_document(self, key):
        """Delete one document atomically, using the same lock as uploads/builds."""
        with connect() as conn:
            current = conn.execute("SELECT manifest, supplements FROM rag_collections "
                                   "WHERE name=%s FOR UPDATE", (self.collection,)).fetchone()
            if not current:
                return 0
            deleted = conn.execute(f"DELETE FROM rag_chunks WHERE collection=%s "
                                   f"AND ({DOCUMENT_KEY_SQL})=%s RETURNING metadata",
                                   (self.collection, key)).fetchall()
            if not deleted:
                return 0
            manifest, supplements = current
            count = conn.execute("SELECT count(*) FROM rag_chunks WHERE collection=%s",
                                 (self.collection,)).fetchone()[0]
            law_ids = {row[0].get("law_id") for row in deleted} - {None}
            remaining_ids = {row[0] for row in conn.execute(
                "SELECT DISTINCT metadata->>'law_id' FROM rag_chunks WHERE collection=%s",
                (self.collection,)).fetchall()}
            supplements = [item for item in supplements if item.get("law_id") not in law_ids - remaining_ids]
            if manifest.get("managed_by") == "law_pipeline":
                removed_files = {row[0].get("source_file") for row in deleted}
                manifest["sources"] = [item for item in manifest.get("sources", [])
                                       if item.get("file") not in removed_files]
                manifest.pop("chunks_sha256", None)
                manifest["partially_deleted"] = True
            manifest["vectors"] = count
            conn.execute("UPDATE rag_collections SET manifest=%s, supplements=%s WHERE name=%s",
                         (Jsonb(manifest), Jsonb(supplements), self.collection))
        return len(deleted)

    def add_documents(self, docs, *, source_file=None, source_files=None):
        info = self.info()
        if info and info["manifest"].get("managed_by") == "law_pipeline":
            raise ValueError("법령 컬렉션은 law_pipeline build로 관리됩니다.")
        if info and (info["embedding_model"] != self.model or
                     info["manifest"].get("requested_dimensions") != self.dimensions):
            raise ValueError("기존 컬렉션의 임베딩 설정과 다릅니다.")
        upload_hash = docs[0].metadata.get("upload_hash") if docs else None
        if upload_hash and self.duplicate_upload(upload_hash):
            return {"skipped": True, "message": "이미 등록된 동일 자료입니다."}
        vectors = self._client().embed_documents([d.page_content for d in docs])
        return self.write_vectors(docs, vectors, mode="upload", source_file=source_file,
                                  source_files=source_files)

    def write_vectors(self, docs, vectors, *, mode="create", manifest=None,
                      supplements=None, source_file=None, source_files=None):
        """Commit a whole collection or one uploaded file atomically.

        Embedding/validation finishes before existing rows are touched. A row
        lock serializes writes; readers see either the old or the new dataset.
        """
        if mode not in {"create", "replace", "upload"}:
            raise ValueError("지원하지 않는 적재 모드입니다.")
        if not docs or len(docs) != len(vectors):
            raise ValueError("청크와 임베딩 개수가 일치해야 합니다.")
        literals = [vector_literal(v) for v in vectors]
        dimension = len(vectors[0])
        if any(len(v) != dimension for v in vectors):
            raise ValueError("임베딩 차원이 일치하지 않습니다.")
        if self.dimensions is not None and dimension != self.dimensions:
            raise ValueError("임베딩 차원이 요청 설정과 다릅니다.")
        metadata = []
        for doc in docs:
            m = dict(doc.metadata)
            m.setdefault("chunk_id", hashlib.sha256(
                (json.dumps(m, ensure_ascii=False, sort_keys=True) + doc.page_content).encode()
            ).hexdigest())
            metadata.append(m)
        if len({m["chunk_id"] for m in metadata}) != len(docs):
            raise ValueError("청크 ID가 중복됩니다.")
        result = dict(manifest or {})
        result.update(embedding_model=self.model, requested_dimensions=self.dimensions,
                      vector_dimension=dimension, storage="pgvector")
        with connect() as conn:
            conn.execute(
                "INSERT INTO rag_collections(name, embedding_model, vector_dimension) "
                "VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING",
                (self.collection, self.model, dimension))
            current = conn.execute(
                "SELECT embedding_model, vector_dimension, manifest FROM rag_collections "
                "WHERE name=%s FOR UPDATE", (self.collection,)).fetchone()
            count = conn.execute("SELECT count(*) FROM rag_chunks WHERE collection=%s",
                                 (self.collection,)).fetchone()[0]
            if mode == "create" and (count or current[2]):
                raise ValueError("기존 컬렉션이 있습니다. 백업 후 --replace를 사용하세요.")
            if mode == "upload":
                if current[2].get("managed_by") == "law_pipeline":
                    raise ValueError("법령 컬렉션은 law_pipeline build로 관리됩니다.")
                if (current[0], current[1]) != (self.model, dimension):
                    raise ValueError("기존 컬렉션의 임베딩 모델·차원과 다릅니다.")
                upload_hash = docs[0].metadata.get("upload_hash")
                if upload_hash and conn.execute(
                    "SELECT 1 FROM rag_chunks WHERE collection=%s AND metadata->>'upload_hash'=%s LIMIT 1",
                    (self.collection, upload_hash)).fetchone():
                    return {"skipped": True, "message": "이미 등록된 동일 자료입니다."}
                # Preserve collection metadata and supplements on incremental uploads.
                result = {**current[2], **result}
                if supplements is None:
                    supplements = conn.execute("SELECT supplements FROM rag_collections WHERE name=%s",
                                               (self.collection,)).fetchone()[0]
                keys = source_files if source_files is not None else ([source_file] if source_file is not None else [])
                if keys:
                    conn.execute("DELETE FROM rag_chunks WHERE collection=%s "
                                 "AND metadata->>'source_file'=ANY(%s)", (self.collection, keys))
            else:
                conn.execute("DELETE FROM rag_chunks WHERE collection=%s", (self.collection,))
            conn.execute("UPDATE rag_collections SET embedding_model=%s, vector_dimension=%s, "
                         "manifest=%s, supplements=%s WHERE name=%s",
                         (self.model, dimension, Jsonb(result), Jsonb(supplements or []), self.collection))
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO rag_chunks(collection, chunk_id, content, metadata, dimensions, embedding) "
                    "VALUES (%s,%s,%s,%s,%s,%s::vector) "
                    "ON CONFLICT (collection,chunk_id) DO UPDATE SET content=EXCLUDED.content, "
                    "metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding",
                    [(self.collection, m["chunk_id"], d.page_content, Jsonb(m), dimension, literal)
                     for d, m, literal in zip(docs, metadata, literals)])
            result["vectors"] = conn.execute("SELECT count(*) FROM rag_chunks WHERE collection=%s",
                                              (self.collection,)).fetchone()[0]
            conn.execute("UPDATE rag_collections SET manifest=%s WHERE name=%s",
                         (Jsonb(result), self.collection))
        return result

    def retrieve(self, question, k=5, *, hybrid=False):
        if not question.strip() or not 1 <= k <= 20:
            raise ValueError("질문은 비어 있을 수 없고 k는 1~20이어야 합니다.")
        # ponytail: exact search + Python BM25 for the small MVP corpus;
        # add a candidate index if measured corpus growth requires it.
        with connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            row = conn.execute("SELECT embedding_model, vector_dimension, manifest, supplements "
                               "FROM rag_collections WHERE name=%s", (self.collection,)).fetchone()
            if row is None:
                return {"ranked": [], "manifest": {}, "supplements": []}
            if row[0] != self.model:
                raise ValueError("검색 모델이 저장된 임베딩 모델과 다릅니다.")
            query = self._client(row[2].get("requested_dimensions")).embed_query(question)
            if len(query) != row[1]:
                raise ValueError("검색 임베딩 차원이 저장된 차원과 다릅니다.")
            rows = conn.execute(
                "SELECT content, metadata, power(embedding <-> %s::vector, 2) AS distance "
                "FROM rag_chunks WHERE collection=%s ORDER BY distance, chunk_id LIMIT %s",
                (vector_literal(query), self.collection, None if hybrid else k)).fetchall()
        ranked = [(Document(page_content=r[0], metadata=r[1]), float(r[2])) for r in rows]
        fusion = {}
        if hybrid:
            from law_pipeline.index import rank_hybrid
            ranked, fusion = rank_hybrid(ranked, question)
        return {"ranked": ranked[:k], "manifest": row[2], "supplements": row[3], "fusion": fusion}

    def similarity_search_with_score(self, question, k=5):
        return self.retrieve(question, k)["ranked"]

    def similarity_search(self, question, k=5):
        return [doc for doc, _ in self.similarity_search_with_score(question, k)]
