"""Optional migration check: install requirements-migration.txt in a disposable container."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from test_pgvector_api import TestEmbeddings
from core.vector_store import PgVectorStore, connect


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1" and importlib.util.find_spec("faiss"),
                     "disposable PostgreSQL and optional FAISS dependency required")
class MigrationTests(unittest.TestCase):
    def test_existing_vectors_scores_and_source_files_are_preserved(self):
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document
        from law_pipeline.extract import digest
        from law_pipeline.migrate_faiss import migrate
        with connect() as conn:
            conn.execute("DELETE FROM rag_chunks")
            conn.execute("DELETE FROM rag_collections")
        client = TestEmbeddings()
        docs = [Document(page_content=text, metadata={"chunk_id": str(i), "doc_type": "law",
                "law_id": "sample", "law_name": "예시법", "article_no": f"제{i+1}조", "page": 1})
                for i, text in enumerate(["연차 근거", "퇴직 근거"])]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "pdf"
            original = FAISS.from_documents(docs, client)
            original.save_local(str(directory))
            (directory / "supplements.jsonl").write_text("")
            meta = {"embedding_model": "test-only", "requested_dimensions": None,
                    "vector_dimension": 8, "vectors": 2,
                    "files": {name: digest((directory / name).read_bytes()) for name in
                              ["index.faiss", "index.pkl", "supplements.jsonl"]}}
            (directory / "manifest.json").write_text(json.dumps(meta))
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            with self.assertRaises(ValueError):
                migrate(directory)  # pickle loading always requires explicit trust
            migrated = migrate(directory, trusted=True)
            self.assertEqual(migrated["vectors"], 2)
            target = PgVectorStore("pdf", model="test-only", embedding_client=client)
            expected = original.similarity_search_with_score("연차", k=2)
            actual = target.similarity_search_with_score("연차", k=2)
            self.assertEqual([d.metadata for d, _ in actual], [d.metadata for d, _ in expected])
            for (_, old_score), (_, new_score) in zip(expected, actual):
                self.assertAlmostEqual(float(old_score), new_score, places=5)
            self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
            with self.assertRaises(ValueError):
                migrate(directory, trusted=True)  # default cannot overwrite a collection
