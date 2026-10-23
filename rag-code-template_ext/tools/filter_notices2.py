import json, re, html, datetime

with open("data/bizinfo/raw.json", encoding="utf-8") as f:
    items = json.load(f)

CATS = {
    "여성": ["여성"],
    "장애인": ["장애인"],
    "사회적경제": ["사회적기업", "사회적경제", "사회연대경제", "협동조합", "마을기업", "자활기업"],
    "창업": ["창업"],
}
HIRE = ["고용", "채용", "일자리", "인건비", "근로", "일경험", "인력"]

def end_date(p):
    ds = re.findall(r"\d{4}-\d{2}-\d{2}", p or "")
    return datetime.date.fromisoformat(ds[-1]) if ds else None

today = datetime.date.today()
rows = []
for it in items:
    end = end_date(it.get("reqstBeginEndDe"))
    if end is not None and end < today:
        continue
    ext = (it.get("printFileNm") or "").rsplit(".", 1)[-1].lower()
    if ext != "pdf":
        continue
    text = " ".join([it.get("pblancNm",""), it.get("hashtags",""), it.get("trgetNm","")])
    cats = [c for c, kws in CATS.items() if any(k in text for k in kws)]
    if not cats:
        continue
    hire = any(k in text for k in HIRE)
    rows.append((not hire, cats, it))

rows.sort(key=lambda r: r[0])
print(f"PDF 본문 + 제목/태그/대상에 키워드 + 마감 전: {len(rows)}건")
print(f"그중 고용 관련 단어 포함: {sum(1 for r in rows if not r[0])}건\n")
with open("data/bizinfo/candidates2.txt", "w", encoding="utf-8") as f:
    for i, (nohire, cats, it) in enumerate(rows, 1):
        line = (f"{i:3d}. [{'고용' if not nohire else '  '}] [{','.join(cats)}] "
                f"{it['pblancNm']} | {it.get('reqstBeginEndDe')} | {it['pblancId']}")
        f.write(line + "\n")
        if i <= 50:
            print(line)
print("\n전체 목록: data/bizinfo/candidates2.txt")
