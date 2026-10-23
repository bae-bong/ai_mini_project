import os, json, re

IDS = [
    "PBLN_000000000119356", "PBLN_000000000125426", "PBLN_000000000117956",
    "PBLN_000000000119850", "PBLN_000000000117568", "PBLN_000000000123363",
    "PBLN_000000000127092", "PBLN_000000000125225", "PBLN_000000000126809",
    "PBLN_000000000117016", "PBLN_000000000126965",
]  # 이미지 PDF(126296 출산급여, 127359 AX 컨설팅) 제외

RULES = [
    ("여성기업", ["여성기업"]),
    ("장애인기업", ["장애인기업"]),
    ("사회적기업", ["사회적기업", "사회적경제", "사회연대경제", "협동조합"]),
    ("청년창업", ["청년창업"]),
    ("창업", ["창업"]),
    ("소상공인", ["소상공인"]),
    ("벤처·이노비즈", ["벤처기업", "이노비즈"]),
    ("소·중기업", ["중소기업"]),
]

# 공고를 직접 읽고 고친 값. 자동 분류가 틀린 공고만 적는다. 예: {"PBLN_...": ["소상공인"]}
OVERRIDES = {"PBLN_000000000119850": ["청년창업", "창업", "소·중기업"]}

def company_types(it):
    text = " ".join([it.get("pblancNm", ""), it.get("hashtags", ""), it.get("trgetNm", "")])
    text = text.replace("중소벤처기업부", "")
    return [name for name, kws in RULES if any(k in text for k in kws)] or None

with open("data/bizinfo/raw.json", encoding="utf-8") as f:
    raw = {it["pblancId"]: it for it in json.load(f)}

os.makedirs("data/bizinfo/meta", exist_ok=True)
for pid in IDS:
    it = raw[pid]
    types = OVERRIDES.get(pid, company_types(it))
    meta = {
        "notice_id": pid,
        "title": it["pblancNm"],
        "agency": it.get("jrsdInsttNm") or None,
        "executor": it.get("excInsttNm") or None,
        "period_raw": it.get("reqstBeginEndDe") or None,
        "target": it.get("trgetNm") or None,
        "field": it.get("pldirSportRealmLclasCodeNm") or None,
        "company_types": types,
        "url": it.get("pblancUrl"),
    }
    with open(f"data/bizinfo/meta/{pid}.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"{pid[-6:]} | {it['pblancNm'][:30]}")
    print(f"        대상: {it.get('trgetNm')} | 라벨: {types}")
    print(f"        태그: {(it.get('hashtags') or '')[:70]}")
print("\n저장: data/bizinfo/meta/*.json")
