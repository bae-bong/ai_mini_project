import os
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from law_pipeline.extract import chunk_records, parse_pages, preprocess, read_jsonl

ROOT = Path(__file__).resolve().parents[2]
SOURCE = {"law_id": "sample", "law_name": "예시법", "file": "sample.pdf", "source_url": "https://example.invalid/law",
          "effective_date": "2026-10-01", "law_number": "1", "law_level": "법률", "category": "노무"}


class ParserTests(unittest.TestCase):
    def parse(self, *texts):
        return parse_pages([{"page": n + 1, "text": t} for n, t in enumerate(texts)], SOURCE, "abc", "2026-10-23")

    def test_cross_page_article_and_future_version(self):
        records = self.parse("제60조(휴가) 현재 본문\n① 조건", "예시법\n② 계속 내용\n제60조(휴가) 미래 본문\n[시행일: 2027. 6. 10.] 제60조\n제61조(기타) 현행")
        self.assertEqual([r["status"] for r in records], ["current", "future", "current"])
        self.assertEqual((records[0]["page_start"], records[0]["page_end"]), (1, 2))
        self.assertEqual(chunk_records(records)[0]["metadata"]["pages"], [1, 2])

    def test_chapter_marker_does_not_contaminate_preceding_article(self):
        records = self.parse("제100조(기타) 본문\n제11장 감독\n제11장 변경 제목\n[시행일: 2027. 1. 1.]\n제101조(기관) 본문")
        self.assertEqual([r["article"] for r in records if r["status"] == "current"], ["제100조", "제101조"])

    def test_partial_and_duplicate_versions_are_quarantined(self):
        records = self.parse("제1조(하나) 내용\n[시행일: 2027. 1. 1.] 제1조제2항\n제2조(둘) A\n제2조(둘) B")
        self.assertTrue(all(r["status"] == "review_required" for r in records))
        self.assertEqual(chunk_records(records), [])

    def test_deleted_future_article_keeps_current_version(self):
        records = self.parse("제3조(현행) 내용\n제3조 삭제 <2026. 4. 7.>\n[시행일: 2026. 12. 8.] 제3조")
        self.assertEqual([r["status"] for r in records], ["current", "future"])

    def test_addendum_and_inline_reference_are_not_body_articles(self):
        records = self.parse("제1조(목적) 본문\n제2조에 따른 사항이다.\n부칙 <제1호,2026. 10. 1.>\n제1조(시행일) 시행한다.\n제2조(적용례) 조건")
        self.assertEqual(len([r for r in records if r["status"] == "current"]), 1)
        self.assertIn("제2조에 따른", records[0]["text"])
        self.assertTrue(all(r["status"] == "supplement" for r in records[1:]))

    def test_definition_article_split_by_item(self):
        records = self.parse("제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.\n1. “근로자”란 사람을 말한다.\n"
                             "2. “사용자”란 사업주를 말한다.\n제3조(기타) 본문")
        chunks = chunk_records(records)
        defs = [c for c in chunks if c["metadata"]["article_no"] == "제2조"]
        self.assertEqual([c["metadata"]["item_no"] for c in defs], ["1", "2"])
        self.assertTrue(all("용어의 뜻은" in c["page_content"] for c in defs))
        self.assertIn("“사용자”", defs[1]["page_content"])
        self.assertNotIn("“근로자”", defs[1]["page_content"])
        self.assertEqual(len({c["metadata"]["chunk_id"] for c in chunks}), len(chunks))
        self.assertEqual(len([c for c in chunk_records(records, split_definitions=False)
                              if c["metadata"]["article_no"] == "제2조"]), 1)

    def test_chunk_coverage_and_actual_page_metadata(self):
        records = self.parse("제1조(긴조문) " + "가" * 150, "나" * 150)
        chunks = chunk_records(records, 100, 20)
        covered = set()
        for c in chunks:
            m = c["metadata"]
            covered.update(range(m["char_start"], m["char_end"]))
            expected = {s["page"] for s in records[0]["spans"] if s["end"] > m["char_start"] and s["start"] < m["char_end"]}
            self.assertEqual(set(m["pages"]), expected)
            self.assertEqual(m["page"], 1)
            self.assertEqual(m["page_start"], min(expected))
            self.assertLessEqual(m["char_end"] - m["char_start"], 100)
        self.assertEqual(covered, set(range(len(records[0]["text"]))))


class SuppliedPDFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT.parent / "upload-paper").exists():
            raise unittest.SkipTest("제공 PDF가 없어 실제 파일 회귀 검사를 건너뜁니다.")
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name)
        cls.report = preprocess(ROOT / "data/laws/sources.json", ROOT.parent / "upload-paper", cls.output)
        cls.records = read_jsonl(cls.output / "articles.jsonl")
        cls.chunks = read_jsonl(cls.output / "chunks.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_pages_and_laws_retained(self):
        self.assertEqual(self.report["pages"], 161)
        self.assertEqual(len({c["metadata"]["law_id"] for c in self.chunks}), 11)
        self.assertTrue(all(c["metadata"]["status"] == "current" for c in self.chunks))

    def test_law_contract_and_document_ordinals(self):
        from collections import defaultdict
        from law_pipeline.schema import validate_law_metadata
        groups = defaultdict(list)
        records = {r["record_id"]: r for r in self.records}
        for c in self.chunks:
            m = c["metadata"]
            validate_law_metadata(m, indexed=False)
            self.assertIsNone(m["ingested_at"])
            self.assertEqual(m["page"], records[m["record_id"]]["page_start"])
            self.assertEqual(m["article_no"], m["article"])
            groups[m["source_file"]].append(m["chunk_index"])
        for values in groups.values():
            self.assertEqual(values, list(range(len(values))))
        categories = {s["law_id"]: s["category"] for s in self.report["sources"]}
        self.assertEqual(sum(v == "노무" for v in categories.values()), 4)
        self.assertEqual(sum(v == "우대기업" for v in categories.values()), 7)

    def test_enterprise_future_versions_are_excluded(self):
        expected = {
            ("venture", "제11조의5"), ("venture", "제16조의9"),
            ("social_enterprise", "제16조의3"), ("social_enterprise", "제16조의4"),
            ("social_enterprise", "제16조의5"), ("small_business", "제3조"),
            ("small_business", "제36조"), ("small_business", "제36조의2"),
            ("small_business", "제37조"), ("small_business", "제38조"),
            ("sme", "제2조"), ("sme", "제20조의2"),
        }
        found = {(r["law_id"], r["article"]) for r in self.records if r["status"] == "future"}
        self.assertTrue(expected <= found, expected - found)
        chunk_records_ids = {c["metadata"]["record_id"] for c in self.chunks}
        self.assertTrue(all(r["record_id"] not in chunk_records_ids for r in self.records
                            if r["status"] == "future"))

    def test_current_annual_leave_and_future_version_are_separated(self):
        recs = [r for r in self.records if r["law_id"] == "labor_standards" and r["article"] == "제60조"]
        self.assertEqual([r["status"] for r in recs], ["current", "future"])
        self.assertEqual((recs[0]["page_start"], recs[0]["page_end"]), (14, 15))
        self.assertNotIn("시간단위", recs[0]["text"])
        self.assertIn("시간단위", recs[1]["text"])

    def test_review_queue_and_supplements_survive(self):
        self.assertTrue(any(r["article"] == "제116조" for r in read_jsonl(self.output / "review_required.jsonl")))
        supplements = read_jsonl(self.output / "supplements.jsonl")
        self.assertTrue(any(r["law_id"] == "retirement" and "50명" in r["text"] and "100명" in r["text"] for r in supplements))
        excluded = {r["record_id"] for r in self.records if r["status"] != "current"}
        self.assertTrue(all(c["metadata"]["record_id"] not in excluded for c in self.chunks))

    def test_reproducible_chunks_and_reference_date_guard(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            preprocess(ROOT / "data/laws/sources.json", ROOT.parent / "upload-paper", out)
            self.assertEqual((out / "chunks.jsonl").read_bytes(), (self.output / "chunks.jsonl").read_bytes())
            with self.assertRaises(ValueError):
                preprocess(ROOT / "data/laws/sources.json", ROOT.parent / "upload-paper", out, "2027-06-10")


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "격리된 PostgreSQL에서 RUN_DB_TESTS=1로 실행")
class IndexTests(unittest.TestCase):
    def test_pgvector_roundtrip_and_model_manifest(self):
        from langchain_core.embeddings import Embeddings
        from law_pipeline.index import build_index, load_index, search
        from law_pipeline.extract import write_jsonl

        class TestEmbeddings(Embeddings):
            def embed_documents(self, texts):
                return [self.embed_query(t) for t in texts]

            def embed_query(self, text):
                return [float(b) / 255 for b in hashlib.sha256(text.encode()).digest()[:8]]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processed = root / "processed"
            processed.mkdir()
            recs = parse_pages([{"page": 1, "text": "제1조(예시) 검색 근거\n부칙 <제1호,2026. 10. 1.>\n제1조(적용례) 예외 조건"}], SOURCE, "abc", "2026-10-23")
            write_jsonl(processed / "chunks.jsonl", chunk_records(recs))
            write_jsonl(processed / "supplements.jsonl", [r for r in recs if r["status"] == "supplement"])
            (processed / "report.json").write_text(json.dumps({"as_of": "2026-10-23", "sources": [], "chunk_size": 1000, "chunk_overlap": 200}))
            client = TestEmbeddings()
            meta = build_index(processed, model="test-only", embedding_client=client)
            self.assertEqual(meta["vector_dimension"], 8)
            store = load_index(embedding_client=client)
            result = search("검색 근거", store=store)
            self.assertEqual(result["hits"][0]["metadata"]["article"], "제1조")
            from law_pipeline.schema import validate_law_metadata
            stored = result["hits"][0]["metadata"]
            validate_law_metadata(stored)
            self.assertEqual(stored["ingested_at"], meta["ingested_at"])
            self.assertEqual(result["sources"][0], {"type": "law", "title": "예시법", "article": "제1조", "page": 1,
                                                  "score": result["hits"][0]["score"]})
            for key in ("doc_type", "doc_id", "source_file", "category", "law_level", "ingested_at"):
                broken = dict(stored)
                broken.pop(key)
                with self.assertRaises(ValueError):
                    validate_law_metadata(broken)
            self.assertIn("예외 조건", str(result["supplementary_context"]))
            with self.assertRaises(ValueError):
                build_index(processed, model="test-only", embedding_client=client)
            # Failed replacement leaves the committed collection searchable.
            from core.vector_store import PgVectorStore
            from langchain_core.documents import Document
            with self.assertRaises(Exception):
                store.write_vectors([Document(page_content="invalid", metadata={"chunk_id": "bad", "value": "\x00"})],
                                    [[0.0] * 8], mode="replace")
            self.assertEqual(search("검색 근거", store=store)["hits"][0]["metadata"], stored)
            # Replacing one collection cannot remove the other source group.
            web = PgVectorStore("web", model="test-only", embedding_client=client)
            doc = Document(page_content="웹 근거", metadata={"source_file": "test.web"})
            web.add_documents([doc], source_file="test.web")
            web.add_documents([doc], source_file="test.web")
            self.assertEqual(web.info()["vectors"], 1)
            self.assertEqual(len(search("검색 근거", store=store)["hits"]), 1)
            with self.assertRaises(ValueError):
                store.add_documents([doc])  # managed law collection rejects ad hoc uploads
            wrong = PgVectorStore("pdf", model="different", embedding_client=client)
            with self.assertRaises(ValueError):
                wrong.similarity_search("검색 근거")
            with self.assertRaises(ValueError):
                store.write_vectors([doc], [[float("nan")]], mode="replace")



if __name__ == "__main__":
    unittest.main()
