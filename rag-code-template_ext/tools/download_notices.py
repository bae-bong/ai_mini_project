import os, csv, json, re, html, time, requests

IDS = [
    "PBLN_000000000119356", "PBLN_000000000125426", "PBLN_000000000117956",
    "PBLN_000000000119850", "PBLN_000000000117568", "PBLN_000000000123363",
    "PBLN_000000000127359", "PBLN_000000000127092", "PBLN_000000000125225",
    "PBLN_000000000126809", "PBLN_000000000126296", "PBLN_000000000117016",
    "PBLN_000000000126965",
]

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

with open("data/bizinfo/raw.json", encoding="utf-8") as f:
    by_id = {it["pblancId"]: it for it in json.load(f)}

os.makedirs("data/bizinfo/pdf", exist_ok=True)
rows = []
for pid in IDS:
    it = by_id.get(pid)
    if not it:
        print("목록에 없음:", pid); continue
    path = f"data/bizinfo/pdf/{pid}.pdf"
    ok = False
    try:
        r = requests.get(it["printFlpthNm"], timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        ok = r.content[:4] == b"%PDF"
        if ok:
            with open(path, "wb") as f:
                f.write(r.content)
        print(("OK " if ok else "PDF 아님 "), pid, f"{len(r.content)//1024}KB", it["pblancNm"][:40])
    except Exception as e:
        print("실패", pid, e)
    rows.append({
        "notice_id": pid, "title": it["pblancNm"], "org": it.get("jrsdInsttNm"),
        "period": it.get("reqstBeginEndDe"), "target": it.get("trgetNm"),
        "url": it.get("pblancUrl"), "summary": clean(it.get("bsnsSumryCn")),
        "file": path if ok else "", "downloaded": ok,
    })
    time.sleep(0.5)

with open("data/bizinfo/metadata.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
print("\nmetadata.csv 저장:", len(rows), "건 / 다운로드 성공:", sum(r["downloaded"] for r in rows))
