"""Integration checks; RUN_DB_TESTS=1 only against a disposable test database."""
import hashlib
import importlib
import os
import unittest
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from core.vector_store import PgVectorStore, connect


class TestEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        return [b / 255 for b in hashlib.sha256(text.encode()).digest()[:8]]


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "disposable PostgreSQL required")
class ApiTests(unittest.TestCase):
    def setUp(self):
        with connect() as conn:
            conn.execute("DELETE FROM rag_chunks")
            conn.execute("DELETE FROM rag_collections")

    def test_authenticated_query_uses_pgvector_persona_and_addenda(self):
        from fastapi.testclient import TestClient
        app_module = importlib.import_module("rag_external-web_pdf_closed_llm")
        client = TestClient(app_module.app)
        store = PgVectorStore("pdf", model="test-only", embedding_client=TestEmbeddings())
        metadata = {"chunk_id": "article-1", "doc_type": "law", "law_id": "test-law",
                    "law_name": "예시법", "title": "예시법", "article_no": "제1조", "page": 1}
        doc = Document(page_content="예시법 제1조 검색 근거", metadata=metadata)
        store.write_vectors([doc], store._client().embed_documents([doc.page_content]),
                            manifest={"managed_by": "law_pipeline"},
                            supplements=[{"law_id": "test-law", "text": "부칙 특별 적용례"}])
        persona = {"employees": 3, "company_types": ["창업기업"]}
        app_module.app.dependency_overrides[app_module.get_current_user] = lambda: {"id": 1, "persona": persona}
        try:
            with patch.object(app_module, "VECTORSTORE_PDF", store), patch.object(app_module, "ChatOpenAI") as llm:
                llm.return_value.invoke.return_value.content = "예시법 제1조에 근거한 답변"
                response = client.post("/rag-query", json={"prompt": doc.page_content, "source": "pdf", "top_k": 1})
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data["sources"][0]["article"], "제1조")
                self.assertAlmostEqual(data["sources"][0]["score"], 0)
                self.assertEqual(data["persona_applied"]["employees"], 3)
                self.assertTrue(data["needs_review"])
                prompt = llm.return_value.invoke.call_args.args[0][0].content
                self.assertIn("상시근로자 3명", prompt)
                self.assertIn("부칙 특별 적용례", prompt)
                self.assertEqual(client.post("/rag-query", json={"prompt": "질문", "source": "pdf", "top_k": 21}).status_code, 400)
                # Managed corpus cannot be mixed with direct PDF uploads.
                self.assertEqual(client.post("/upload-paper", files={"file": ("sample.pdf", b"unused", "application/pdf")}).status_code, 409)
        finally:
            app_module.app.dependency_overrides.clear()
        self.assertEqual(client.post("/rag-query", json={"prompt": "질문", "source": "pdf"}).status_code, 401)

    def test_model_dimension_checks_and_atomic_replacement(self):
        store = PgVectorStore("web", model="test-only", embedding_client=TestEmbeddings())
        docs = [Document(page_content="one", metadata={"source_file": "one.pdf"}),
                Document(page_content="two", metadata={"source_file": "two.pdf"})]
        store.add_documents([docs[0]], source_file="one.pdf")
        store.add_documents([docs[1]], source_file="two.pdf")
        store.add_documents([docs[0]], source_file="one.pdf")
        self.assertEqual(store.info()["vectors"], 2)
        self.assertAlmostEqual(store.similarity_search_with_score("one", 1)[0][1], 0)
        with self.assertRaises(ValueError):
            store.write_vectors(docs, [[1, 2], [1]], mode="replace")
        with self.assertRaises(Exception):
            store.write_vectors([Document(page_content="bad", metadata={"bad": "\x00"})], [[0] * 8], mode="replace")
        self.assertEqual(store.info()["vectors"], 2)
        wrong = PgVectorStore("web", model="different", embedding_client=TestEmbeddings())
        with self.assertRaises(ValueError):
            wrong.similarity_search("one")

    def test_upload_hash_skips_renamed_files_and_notice_write_rolls_back_together(self):
        store = PgVectorStore("web", model="test-only", embedding_client=TestEmbeddings())
        docs = [Document(page_content="overview", metadata={"chunk_id": "meta", "source_file": "n/_meta", "upload_hash": "hash1"}),
                Document(page_content="body", metadata={"chunk_id": "body", "source_file": "n/a.pdf", "upload_hash": "hash1"})]
        store.add_documents(docs, source_files=["n/_meta", "n/a.pdf"])
        self.assertTrue(store.add_documents(docs, source_files=["n/_meta", "n/copy.pdf"])["skipped"])
        # Recheck under the collection lock also covers concurrent upload races.
        self.assertTrue(store.write_vectors(docs, [[0] * 8] * 2, mode="upload")["skipped"])
        broken = [Document(page_content="new", metadata={"chunk_id": "new", "source_file": "n/_meta"}),
                  Document(page_content="bad", metadata={"chunk_id": "bad", "source_file": "n/a.pdf", "bad": "\x00"})]
        with self.assertRaises(Exception):
            store.add_documents(broken, source_files=["n/_meta", "n/a.pdf"])
        self.assertEqual(store.info()["vectors"], 2)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT content FROM rag_chunks ORDER BY content").fetchall(),
                             [("body",), ("overview",)])

    def test_document_list_preview_and_delete_keep_other_notices_and_collection(self):
        web = PgVectorStore("web", model="test-only", embedding_client=TestEmbeddings())
        law = PgVectorStore("pdf", model="test-only", embedding_client=TestEmbeddings())
        law.add_documents([Document(page_content="법령", metadata={"source_file": "a.pdf"})], source_file="a.pdf")
        docs = [Document(page_content="공고 개요", metadata={"chunk_id": "meta", "title": "공고1", "notice_id": "n1", "source_file": "n1/_meta"}),
                Document(page_content="본문" * 1000, metadata={"chunk_id": "body", "title": "공고1", "notice_id": "n1", "source_file": "n1/a.pdf"}),
                Document(page_content="다른 공고", metadata={"chunk_id": "other", "notice_id": "n2", "source_file": "n2/a.pdf"})]
        web.add_documents(docs)
        listed = {item["key"]: item for item in web.list_documents()}
        self.assertEqual(listed["notice:n1"]["chunks"], 2)
        self.assertEqual(set(listed["notice:n1"]["files"]), {"n1/_meta", "n1/a.pdf"})
        self.assertTrue(all(len(item["text"]) <= 1200 for item in web.preview_document("notice:n1")))
        self.assertEqual(web.delete_document("notice:n1' OR 1=1 --"), 0)
        self.assertEqual(web.delete_document("notice:n1"), 2)
        self.assertEqual(web.delete_document("notice:n1"), 0)
        self.assertEqual(web.list_documents()[0]["key"], "notice:n2")
        self.assertEqual(law.info()["vectors"], 1)
        self.assertEqual(web.info()["vectors"], 1)

    def test_managed_law_deletion_updates_sources_and_orphan_addenda(self):
        store = PgVectorStore("pdf", model="test-only", embedding_client=TestEmbeddings())
        docs = [Document(page_content=name, metadata={"source_file": name + ".pdf", "law_id": name}) for name in ["a", "b"]]
        store.write_vectors(docs, [[0] * 8] * 2,
                            manifest={"managed_by": "law_pipeline", "chunks_sha256": "old",
                                      "sources": [{"file": "a.pdf"}, {"file": "b.pdf"}]},
                            supplements=[{"law_id": "a", "text": "a 부칙"}, {"law_id": "b", "text": "b 부칙"}])
        self.assertEqual(store.delete_document("file:a.pdf"), 1)
        info = store.info()
        self.assertEqual(info["manifest"]["managed_by"], "law_pipeline")
        self.assertTrue(info["manifest"]["partially_deleted"])
        self.assertNotIn("chunks_sha256", info["manifest"])
        self.assertEqual(info["manifest"]["sources"], [{"file": "b.pdf"}])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT supplements FROM rag_collections WHERE name='pdf'").fetchone()[0],
                             [{"law_id": "b", "text": "b 부칙"}])
