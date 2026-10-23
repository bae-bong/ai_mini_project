"""PDF는 증거로 보존하고, 애매한 시간 적용은 검색에서 격리한다."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

DATE = r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\."
HEADER = re.compile(r"\[시행\s+" + DATE + r"\]\s*\[법률\s+제(\d+)호")
EFFECTIVE = re.compile(r"\[시행일:\s*" + DATE + r"\]\s*([^\n]*)")
ARTICLE = re.compile(r"^(제\d+조(?:의\d+)?)(?:\s*\(([^)]*)\)|\s+(삭제))")
CHAPTER = re.compile(r"^제\d+(?:장|절|관)\s+")
ADDENDUM = re.compile(r"^부\s*칙\s*<")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iso(parts) -> str:
    return date(*map(int, parts)).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def clean_lines(text: str, law_name: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        # pypdf places the printed footer at the start of the extracted page.
        if re.fullmatch(r"법제처\s+\d+\s+국가법령정보센터", line):
            continue
        if line in {law_name, "법제처", "국가법령정보센터"}:
            continue
        lines.append(line)
    return lines


def parse_pages(pages: list[dict], source: dict, pdf_hash: str, as_of: str) -> list[dict]:
    """Recognize *headings*, not inline references; carry records across pages."""
    records, active = [], None
    appendix = None
    appendix_number = 0

    def flush():
        nonlocal active
        if active is None:
            return
        lines = active.pop("lines")
        text = "\n".join(line for _, line in lines).strip()
        if text:
            # Keep the map so chunks can report their actual PDF page range.
            offset, spans = 0, []
            for page, line in lines:
                spans.append({"start": offset, "end": offset + len(line), "page": page})
                offset += len(line) + 1
            active.update(text=text, spans=spans, page_start=lines[0][0], page_end=lines[-1][0])
            active["record_id"] = digest((pdf_hash + str(len(records)) + text).encode())[:24]
            records.append(active)
        active = None

    for page in pages:
        for line in clean_lines(page["text"], source["law_name"]):
            if not line:
                continue
            am = ARTICLE.match(line)
            if ADDENDUM.match(line):
                flush()
                appendix_number += 1
                appendix = line
                active = {"section_type": "addendum", "article": "전문", "article_title": line,
                          "section_id": f"addendum-{appendix_number}", "addendum_label": line, "lines": []}
            elif CHAPTER.match(line) and appendix is None:
                flush()
                active = {"section_type": "heading", "article": "", "article_title": line,
                          "section_id": "body", "lines": []}
            elif am:
                flush()
                active = {"section_type": "addendum" if appendix else "body", "article": am[1],
                          "article_title": am[2] or "삭제", "section_id": f"addendum-{appendix_number}" if appendix else "body",
                          "addendum_label": appendix, "lines": []}
            if active is not None:
                active["lines"].append((page["page"], line))
    flush()

    for rec in records:
        rec.update(law_id=source["law_id"], law_name=source["law_name"], source=source["file"],
                   source_url=source["source_url"], pdf_sha256=pdf_hash, as_of=as_of,
                   document_effective_date=source["effective_date"], law_number=source["law_number"],
                   law_level=source["law_level"], category=source["category"])
        marks = [{"date": iso(m.groups()[:3]), "target": m[4].strip()} for m in EFFECTIVE.finditer(rec["text"])]
        rec["effective_annotations"] = marks
        rec["requires_review"] = False
        if rec["section_type"] == "heading":
            rec.update(status="heading", reason="장·절 제목: 본문 청크에 혼입하지 않음")
        elif rec["section_type"] == "addendum":
            rec.update(status="supplement", reason="적용례·경과조치: 해당 법률 검색 시 별도 근거로 동반", requires_review=True)
        elif marks:
            future = [m for m in marks if m["date"] > as_of]
            whole = all(m["target"] == rec["article"] for m in marks)
            if future and whole:
                rec.update(status="future", reason="기준일 이후 시행되는 조문 버전")
            else:
                # Partial markers may apply to only one item within an otherwise-current article.
                # Never claim that skipping the marker line reconstructs the current version.
                rec.update(status="review_required", reason="부분 시행·복수 날짜 또는 적용 대상 검토 필요", requires_review=True)
        elif rec["article_title"] == "삭제":
            rec.update(status="deleted", reason="삭제된 조문")
        else:
            rec.update(status="current", reason="해당 PDF 현행 본문; 별도 시행일 표기 없음")

    groups = defaultdict(list)
    for rec in records:
        if rec["section_type"] == "body":
            groups[rec["article"]].append(rec)
    for versions in groups.values():
        current = [r for r in versions if r["status"] == "current"]
        if len(current) > 1:
            for rec in current:
                rec.update(status="review_required", reason="시행일만으로 선택할 수 없는 중복 조문", requires_review=True)
    return records


def chunk_records(records: list[dict], size: int = 1000, overlap: int = 200) -> list[dict]:
    if not 0 <= overlap < size or size < 100:
        raise ValueError("chunk_size >= 100, 0 <= overlap < chunk_size가 필요합니다.")
    chunks = []
    document_ordinals = defaultdict(int)
    for rec in records:
        if rec["status"] != "current":
            continue
        text, start, number = rec["text"], 0, 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + max(overlap + 1, size // 2), end)
                if boundary > start:
                    end = boundary + 1
            spans = [s for s in rec["spans"] if s["end"] > start and s["start"] < end]
            pages = sorted({s["page"] for s in spans})
            metadata = {k: v for k, v in rec.items() if k not in {"text", "spans", "effective_annotations"}}
            document_key = (rec["law_name"], rec["source"])
            metadata.update(type="law", doc_type="law", doc_id=f"law:{rec['law_name']}",
                            title=rec["law_name"], source_file=rec["source"],
                            article_no=rec["article"] or None,
                            effective_date=rec["document_effective_date"] or None,
                            ingested_at=None,  # Filled at successful index-build preparation, not extraction.
                            page=rec["page_start"], article_page_start=rec["page_start"],
                            page_start=pages[0], page_end=pages[-1], pages=pages,
                            chunk_index=document_ordinals[document_key], article_chunk_index=number,
                            chunk_id=f"{rec['record_id']}-{number}", char_start=start, char_end=end,
                            supplements_required=True)
            # Include the article label in every embedding, even continuation chunks.
            prefix = f"{rec['law_name']} {rec['article']}({rec['article_title']})\n"
            chunks.append({"page_content": prefix + text[start:end], "metadata": metadata})
            document_ordinals[document_key] += 1
            if end == len(text):
                break
            start, number = end - overlap, number + 1
    return chunks


def preprocess(manifest: Path, raw_dir: Path, output: Path, as_of: str | None = None) -> dict:
    from pypdf import PdfReader

    cfg = json.loads(manifest.read_text())
    as_of = as_of or cfg["snapshot_date"]
    date.fromisoformat(as_of)
    if as_of != cfg["snapshot_date"]:
        raise ValueError("이 PDF 묶음은 snapshot_date의 현행 법령입니다. 다른 기준일에는 원문과 manifest를 갱신하세요.")
    all_pages, all_records, source_reports = [], [], []
    for source in cfg["sources"]:
        if source.get("law_level") not in {"법률", "시행령", "시행규칙"} or source.get("category") not in {"노무", "우대기업"}:
            raise ValueError("sources.json에 유효한 law_level과 category가 필요합니다.")
        path = raw_dir / source["file"]
        pdf_hash = digest(path.read_bytes())
        if source.get("sha256") and source["sha256"] != pdf_hash:
            raise ValueError(f"원본 PDF 해시 불일치: {path.name}")
        reader = PdfReader(path)
        pages = [{"law_id": source["law_id"], "source": path.name, "page": i + 1,
                  "text": p.extract_text() or ""} for i, p in enumerate(reader.pages)]
        if any(not p["text"].strip() for p in pages):
            raise ValueError(f"텍스트가 없는 페이지가 있습니다: {path.name}; OCR/원문 확인 필요")
        header = HEADER.search(pages[0]["text"])
        if not header or iso(header.groups()[:3]) != source["effective_date"] or header[4] != source["law_number"]:
            raise ValueError(f"PDF 표제부와 출처표의 법률번호/시행일 불일치: {path.name}")
        if source["effective_date"] > as_of:
            raise ValueError(f"기준일 이후 시행 문서입니다: {path.name}")
        if source["law_name"] not in pages[0]["text"]:
            raise ValueError(f"법령명 불일치: {path.name}")
        records = parse_pages(pages, source, pdf_hash, as_of)
        if not any(r["status"] == "current" for r in records):
            raise ValueError(f"현행 조문 인식 실패: {path.name}")
        all_pages.extend(pages)
        all_records.extend(records)
        source_reports.append({**source, "sha256": pdf_hash, "pages": len(pages),
                               "statuses": dict(Counter(r["status"] for r in records))})
    chunks = chunk_records(all_records, cfg.get("chunk_size", 1000), cfg.get("chunk_overlap", 200))
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "pages.jsonl", all_pages)
    write_jsonl(output / "articles.jsonl", all_records)
    write_jsonl(output / "chunks.jsonl", chunks)
    write_jsonl(output / "supplements.jsonl", [r for r in all_records if r["status"] == "supplement"])
    write_jsonl(output / "review_required.jsonl", [r for r in all_records if r["status"] == "review_required"])
    summary = {"as_of": as_of, "pages": len(all_pages), "articles_and_sections": len(all_records),
               "chunks": len(chunks), "statuses": dict(Counter(r["status"] for r in all_records)),
               "sources": source_reports, "chunk_size": cfg.get("chunk_size", 1000),
               "chunk_overlap": cfg.get("chunk_overlap", 200)}
    (output / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary
