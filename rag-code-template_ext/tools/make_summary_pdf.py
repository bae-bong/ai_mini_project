import os, re, json, html
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader

FONTS = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
font_path = next((p for p in FONTS if os.path.exists(p)), None)
if not font_path:
    raise SystemExit("한글 폰트를 못 찾았어요. 알려 주세요: ls /System/Library/Fonts/Supplemental | head -50")
pdfmetrics.registerFont(TTFont("Kor", font_path))

IDS = [f[:-5] for f in sorted(os.listdir("data/bizinfo/meta")) if f.endswith(".json")]
raw = {it["pblancId"]: it for it in json.load(open("data/bizinfo/raw.json", encoding="utf-8"))}

def clean(s):
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return "\n".join(re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in s.splitlines() if ln.strip())

body = ParagraphStyle("b", fontName="Kor", fontSize=10, leading=15, wordWrap="CJK")
head = ParagraphStyle("h", parent=body, fontSize=13, leading=19, spaceAfter=8)

os.makedirs("data/bizinfo/pdf_summary", exist_ok=True)
for pid in IDS:
    it = raw[pid]
    rows = [
        ("공고명", it.get("pblancNm")),
        ("소관기관", it.get("jrsdInsttNm")),
        ("수행기관", it.get("excInsttNm")),
        ("신청기간", it.get("reqstBeginEndDe")),
        ("지원대상", it.get("trgetNm")),
        ("사업개요", clean(it.get("bsnsSumryCn"))),
        ("신청방법", clean(it.get("reqstMthPapersCn"))),
        ("공고 링크", it.get("pblancUrl")),
    ]
    story = [Paragraph(escape("공고 요약: " + (it.get("pblancNm") or "")), head)]
    for k, v in rows:
        if not v:
            continue
        story.append(Paragraph("<b>" + escape(k) + "</b>", body))
        for line in str(v).splitlines():
            story.append(Paragraph(escape(line), body))
        story.append(Spacer(1, 6))
    path = f"data/bizinfo/pdf_summary/{pid}_요약.pdf"
    SimpleDocTemplate(path, pagesize=A4).build(story)
    r = PdfReader(path)
    n = sum(len((p.extract_text() or "").strip()) for p in r.pages)
    print(("OK " if n > 200 else "글자 부족 "), pid[-6:], f"{len(r.pages)}쪽, 글자 {n}자")
