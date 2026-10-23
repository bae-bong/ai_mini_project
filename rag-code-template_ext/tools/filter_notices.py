import json, re, html, datetime, collections

with open("data/bizinfo/raw.json", encoding="utf-8") as f:
    items = json.load(f)

CATS = {
    "여성": ["여성"],
    "장애인": ["장애인"],
    "사회적경제": ["사회적기업", "사회적경제", "협동조합", "마을기업", "자활기업"],
    "창업": ["창업"],
}

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def end_date(period):
    ds = re.findall(r"\d{4}-\d{2}-\d{2}", period or "") or \
         [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in re.findall(r"\d{8}", period or "")]
    return datetime.date.fromisoformat(ds[-1]) if ds else None

today = datetime.date.today()
rows = []
for it in items:
    end = end_date(it.get("reqstBeginEndDe"))
    expired = end is not None and end < today
    text = " ".join([it.get("pblancNm",""), it.get("hashtags",""),
                     it.get("trgetNm",""), clean(it.get("bsnsSumryCn"))])
    cats = [c for c, kws in CATS.items() if any(k in text for k in kws)]
    if cats and not expired:
        rows.append((cats, it))

print(f"전체 {len(items)}건 / 키워드 해당 + 마감 전: {len(rows)}건")
cnt = collections.Counter(c for cats, _ in rows for c in cats)
print("분야별:", dict(cnt))

ext = collections.Counter(
    (it.get("printFileNm") or "없음").rsplit(".", 1)[-1].lower() for _, it in rows)
print("본문 파일 형식(printFileNm):", dict(ext))

with open("data/bizinfo/candidates.txt", "w", encoding="utf-8") as f:
    for cats, it in rows:
        f.write(f"{it['pblancId']} | {','.join(cats)} | {it['pblancNm']} | "
                f"{it.get('reqstBeginEndDe')} | {it.get('printFileNm')}\n")
print("목록 저장: data/bizinfo/candidates.txt")

print("\n--- 장애인/사회적경제/여성 후보 제목 (최대 30개) ---")
shown = 0
for cats, it in rows:
    if set(cats) & {"장애인", "사회적경제", "여성"}:
        print("-", it["pblancNm"], "|", it.get("reqstBeginEndDe"))
        shown += 1
        if shown >= 30:
            break
