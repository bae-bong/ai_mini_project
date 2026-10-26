import importlib
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from tools.audit_duplicate_documents import duplicate_candidates


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("rag_external-web_pdf_closed_llm")
        self.module.app.dependency_overrides[self.module.get_current_user] = lambda: {"id": 1}
        self.client = TestClient(self.module.app)
        self.store = MagicMock()
        self.store.info.return_value = None
        self.store.duplicate_upload.return_value = None
        self.store.add_documents.return_value = {}

    def tearDown(self):
        self.module.app.dependency_overrides.clear()

    def test_notice_without_meta_uses_notice_store_and_atomic_document_write(self):
        overview = [{"page_content": "공고 개요", "metadata": {"source_file": "n/_meta",
                    "notice_id": "n", "title": "공고", "apply_start": None, "apply_end": None,
                    "company_types": []}}]
        body = [{"page_content": "공고 본문", "metadata": {"source_file": "n/사업.pdf"}}]
        with patch.object(self.module, "VECTORSTORE_WEB", self.store), \
             patch.object(self.module, "build_notice_chunks", return_value=(overview, body)) as chunker, \
             patch.object(self.module, "VECTORSTORE_PDF") as law:
            response = self.client.post("/upload-notice", files={"file": ("사업.pdf", b"%PDF-test")})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(chunker.call_args.args[0]["title"], "사업")
            self.assertEqual(self.store.add_documents.call_count, 1)
            self.assertEqual(len(self.store.add_documents.call_args.args[0]), 2)
            law.add_documents.assert_not_called()

    def test_same_pdf_is_skipped_before_parsing_or_embedding(self):
        self.store.duplicate_upload.return_value = "old.pdf"
        for endpoint, store_name in [("/upload-paper", "VECTORSTORE_PDF"), ("/upload-notice", "VECTORSTORE_WEB")]:
            with patch.object(self.module, store_name, self.store):
                response = self.client.post(endpoint, files={"file": ("renamed.pdf", b"%PDF-test")})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["skipped"])
        self.store.add_documents.assert_not_called()

    def test_legacy_notice_metadata_without_attachment_is_preserved(self):
        with patch.object(self.module, "VECTORSTORE_WEB", self.store):
            response = self.client.post("/upload-notice", data={"meta": json.dumps({
                "notice_id": "official-1234", "title": "청년 채용 지원", "company_types": ["창업기업"],
                "period_raw": "2026-10-01 ~ 2026-10-31"})})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["notice_id"], "official-1234")
            self.assertEqual(response.json()["company_types"], ["창업기업"])
            self.assertEqual(self.store.add_documents.call_args.kwargs["source_files"], ["official-1234/_meta"])

    def test_notice_requires_readable_body_and_upload_limits_are_enforced(self):
        with patch.object(self.module, "VECTORSTORE_WEB", self.store), \
             patch.object(self.module, "build_notice_chunks", return_value=([{}], [])):
            response = self.client.post("/upload-notice", files={"file": ("scan.pdf", b"%PDF-test")})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.client.post("/upload-notice", files={"file": ("a.pdf", b"bad")}).status_code, 400)
            self.assertEqual(self.client.post("/upload-notice", files={"file": ("a.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024))}).status_code, 413)
        self.store.add_documents.assert_not_called()

    def test_duplicate_audit_preserves_collection_and_legal_identity(self):
        rows = [("pdf", "본문", {"source_file": file, "law_id": law})
                for file, law in [("a.pdf", "law1"), ("copy.pdf", "law1"), ("b.pdf", "law2")]]
        rows += [("web", "본문", {"source_file": "a.pdf", "law_id": "law1"})]
        report = duplicate_candidates(rows)
        self.assertEqual(len(report["duplicate_candidates"]), 1)
        self.assertEqual(report["duplicate_candidates"][0]["source_files"], ["a.pdf", "copy.pdf"])

    def test_document_management_requires_login_and_valid_scope(self):
        self.module.app.dependency_overrides.clear()
        self.assertEqual(self.client.get("/documents?source=pdf").status_code, 401)
        self.assertEqual(self.client.delete("/documents?source=pdf&key=file:a.pdf").status_code, 401)
        self.module.app.dependency_overrides[self.module.get_current_user] = lambda: {"id": 1}
        with patch.object(self.module, "VECTORSTORE_WEB", self.store), \
             patch.object(self.module, "VECTORSTORE_PDF") as law:
            self.store.list_documents.return_value = [{"key": "notice:one"}]
            self.store.preview_document.return_value = [{"text": "공고 본문"}]
            self.store.delete_document.return_value = 2
            self.assertEqual(self.client.get("/documents?source=unknown").status_code, 400)
            self.assertEqual(self.client.get("/documents?source=web").json()["items"][0]["key"], "notice:one")
            self.assertEqual(self.client.get("/documents/preview?source=web&key=notice:one").status_code, 200)
            response = self.client.delete("/documents?source=web&key=notice:one")
            self.assertEqual(response.json()["deleted_chunks"], 2)
            self.store.delete_document.assert_called_once_with("notice:one")
            law.delete_document.assert_not_called()
            self.store.delete_document.return_value = 0
            self.assertEqual(self.client.delete("/documents?source=web&key=notice:missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
