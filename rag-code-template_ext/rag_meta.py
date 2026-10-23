"""
청크 메타데이터 공통 함수 (노션 '청크 메타데이터 스키마' 기준)
- 법령 PDF: 머리말 제거 → 조 단위 분할 → 긴 조문은 재분할 → 메타데이터 부착
- 공고: 신청기간 파싱, 마감 여부 계산
"""
import re
from datetime import date, datetime, timezone, timedelta
from pypdf import PdfReader

KST = timezone(timedelta(hours=9))

# 매 페이지 머리말: "법제처   2   국가법령정보센터" 다음 줄의 법령명
HEADER_RE = re.compile(r"^\s*법제처\s+\d+\s+국가법령정보센터\s*$")
# 조문 시작: 줄 맨 앞 "제5조(최저임금액)", "제6조의2(…)"
ARTICLE_RE = re.compile(r"^\s*(제\d+조(?:의\d+)?)\s*\(([^)]*)\)", re.M)
# 파일명: "최저임금법(법률)(제17326호)(20200526).pdf"
FILENAME_RE = re.compile(r"^(?P<name>.+?)\((?P<level>법률|대통령령|총리령|부령|[^)]*령|[^)]*규칙)\)(?:\(제\d+호\))?(?:\((?P<date>\d{8})\))?")


def law_info_from_filename(filename: str) -> dict:
    base = filename.rsplit("/", 1)[-1].removesuffix(".pdf")
    m = FILENAME_RE.match(base)
    if not m:
        return {"law_name": base, "law_level": None, "effective_date": None}
    level = m.group("level")
    level = "시행령" if level == "대통령령" else ("시행규칙" if level.endswith(("부령", "규칙")) else level)
    d = m.group("date")
    return {
        "law_name": m.group("name").strip(),
        "law_level": level,
        "effective_date": f"{d[:4]}-{d[4:6]}-{d[6:]}" if d else None,
    }


def load_pdf_pages(path: str, law_name: str | None = None) -> list[tuple[int, str]]:
    """페이지별 텍스트 (머리말 줄 + 바로 다음 법령명 줄 제거). [(page_no, text), ...]"""
    pages = []
    for i, p in enumerate(PdfReader(path).pages, start=1):
        lines = (p.extract_text() or "").splitlines()
        out, skip_next = [], False
        for ln in lines:
            if HEADER_RE.match(ln):
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                if law_name and ln.strip() == law_name:
                    continue
            out.append(ln)
        pages.append((i, "\n".join(out).strip()))
    return pages


def split_law_articles(pages: list[tuple[int, str]]) -> list[dict]:
    """페이지를 이어 붙여 조 단위로 자른다. 조문이 페이지를 넘어가면 시작 페이지를 쓴다."""
    full, offsets = "", []
    for no, txt in pages:
        offsets.append((len(full), no))
        full += txt + "\n"

    def page_at(pos):
        pg = offsets[0][1]
        for start, no in offsets:
            if start <= pos:
                pg = no
            else:
                break
        return pg

    hits = list(ARTICLE_RE.finditer(full))
    if not hits:
        return [{"article_no": None, "article_title": None, "page": 1, "text": full.strip()}]
    arts = []
    if hits[0].start() > 0 and full[: hits[0].start()].strip():   # 제1조 앞 (법령명·부칙 등)
        arts.append({"article_no": None, "article_title": None, "page": 1, "text": full[: hits[0].start()].strip()})
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(full)
        arts.append({
            "article_no": m.group(1),
            "article_title": m.group(2).strip() or None,
            "page": page_at(m.start()),
            "text": full[m.start():end].strip(),
        })
    return arts


def build_law_chunks(path: str, filename: str, category: str, max_chars: int = 1000, overlap: int = 150) -> list[dict]:
    """조 단위 청크 + 메타데이터. 반환: [{"page_content":..., "metadata":{...}}]"""
    info = law_info_from_filename(filename)
    pages = load_pdf_pages(path, info["law_name"])
    arts = split_law_articles(pages)
    now = datetime.now(KST).isoformat(timespec="seconds")
    chunks, idx = [], 0
    for a in arts:
        body = a["text"]
        pieces = [body] if len(body) <= max_chars else [body[s:s + max_chars] for s in range(0, len(body), max_chars - overlap)]
        for piece in pieces:
            head = f"[{info['law_name']} {a['article_no'] or ''}{'(' + a['article_title'] + ')' if a['article_title'] else ''}]".replace(" ]", "]")
            chunks.append({
                # 검색 정확도를 위해 본문 앞에 '법령명 조문(제목)'을 붙인다
                "page_content": f"{head}\n{piece}",
                "metadata": {
                    "doc_type": "law",
                    "doc_id": f"law:{info['law_name']}",
                    "title": info["law_name"],
                    "source_file": filename,
                    "page": a["page"],
                    "chunk_index": idx,
                    "ingested_at": now,
                    "law_name": info["law_name"],
                    "law_level": info["law_level"],
                    "category": category,
                    "article_no": a["article_no"],
                    "article_title": a["article_title"],
                    "effective_date": info["effective_date"],
                },
            })
            idx += 1
    return chunks


def parse_period(raw: str | None):
    """'20260101 ~ 20261231' -> ('2026-01-01', '2026-12-31')"""
    if not raw:
        return None, None
    ds = re.findall(r"(\d{4})[.-]?(\d{2})[.-]?(\d{2})", raw)
    fmt = lambda d: f"{d[0]}-{d[1]}-{d[2]}"
    if len(ds) >= 2:
        return fmt(ds[0]), fmt(ds[1])
    if len(ds) == 1:
        return fmt(ds[0]), None
    return None, None


def notice_status(period_end: str | None, today: date | None = None) -> str:
    """질문 시점 기준 접수 상태. 저장하지 않고 매번 계산한다."""
    if not period_end:
        return "기간 확인 필요"
    today = today or datetime.now(KST).date()
    return "접수중" if date.fromisoformat(period_end) >= today else "마감"
