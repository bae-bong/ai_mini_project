import os, csv, json, re
from dotenv import load_dotenv
from pypdf import PdfReader
load_dotenv()

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
try:
    from langchain_community.vectorstores import FAISS
except ImportError:
    from langchain.vectorstores import FAISS
from langchain_core.documents import Document
from embed_util import get_embeddings, index_dir

EXCLUDE = {"PBLN_000000000127359"}      # 내용이 얇아 제외
OUT_DIR = index_dir()

with open("data/bizinfo/raw.json", encoding="utf-8") as f:
    raw = {it["pblancId"]: it for it in json.load(f)}

def clean(s):
    import html
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def end_date(p):
    ds = re.findall(r"\d{4}-\d{2}-\d{2}", p or "")
    return ds[-1] if ds else ""

docs = []
with open("data/bizinfo/metadata.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        pid = r["notice_id"]
        if pid in EXCLUDE:
            continue
        base = {
            "type": "notice", "notice_id": pid, "title": r["title"], "org": r["org"],
            "period": r["period"], "end_date": end_date(r["period"]),
            "target": r["target"], "url": r["url"],
        }
        apply_how = clean(raw[pid].get("reqstMthPapersCn"))
        overview = (f"공고명: {r['title']}\n소관기관: {r['org']}\n신청기간: {r['period']}\n"
                    f"지원대상: {r['target']}\n사업개요: {r['summary']}\n신청방법: {apply_how}")
        docs.append(Document(page_content=overview, metadata={**base, "page": 0, "section": "overview"}))

        pdf_pages = []
        if r["file"]:
            for i, p in enumerate(PdfReader(r["file"]).pages, 1):
                pdf_pages.append((i, (p.extract_text() or "").strip()))
        if sum(len(t) for _, t in pdf_pages) >= 200:
            for i, t in pdf_pages:
                if t:
                    docs.append(Document(page_content=t, metadata={**base, "page": i, "section": "body"}))
        else:
            print("본문 글자 부족 → 개요 문서만 사용:", pid, r["title"][:30])

splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
chunks = splitter.split_documents(docs)
print(f"문서 {len(docs)}개 → 청크 {len(chunks)}개")

db = FAISS.from_documents(chunks, get_embeddings())
os.makedirs("faiss_db", exist_ok=True)
db.save_local(OUT_DIR)
print("저장 완료:", OUT_DIR)
