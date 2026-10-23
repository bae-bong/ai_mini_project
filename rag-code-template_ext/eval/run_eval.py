"""
평가셋으로 /rag-query 를 돌려 정확성·근거성·환각률을 잰다.

사용법 (rag-code-template_ext 폴더, RAG 서버가 켜진 상태):
  python eval/run_eval.py --label baseline
  python eval/run_eval.py --label hybrid --top-k 5
  python eval/run_eval.py --label persona3 --employees 3

결과:
  eval/results/<시각>_<label>.csv   문항별 결과
  eval/results/<시각>_<label>.json  요약 지표
토큰: .env 의 JWT_SECRET 으로 평가용 JWT를 직접 만들어 Authorization 헤더로 보낸다.
"""
import argparse, csv, json, os, re, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from jose import jwt

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
load_dotenv(ROOT / ".env")
NOT_FOUND = "자료에서 찾을 수 없음"
KST = timezone(timedelta(hours=9))


def make_token(employees, company_types):
    secret = os.getenv("JWT_SECRET")
    if not secret:
        sys.exit("JWT_SECRET 이 .env 에 없습니다.")
    now = datetime.now(timezone.utc)
    claims = {"sub": "0", "username": "eval@local", "iss": "simple-auth-server",
              "iat": now, "exp": now + timedelta(hours=1)}
    if employees is not None:
        claims["employees"] = employees
    if company_types:
        claims["companyTypes"] = company_types
    return jwt.encode(claims, secret, algorithm="HS256")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def judge(row, res):
    answer = res.get("response", "")
    sources = res.get("sources") or []
    not_found = NOT_FOUND in answer or "찾을 수 없" in answer
    out = {"answer_not_found": not_found}

    # 근거성: 기대 법령·조문이 sources 에 있는가
    if row["expected_article"]:
        out["grounded"] = any(
            norm(s.get("article")) == norm(row["expected_article"])
            and norm(row["expected_law"]) in norm(s.get("title"))
            for s in sources)
        ranks = [i + 1 for i, s in enumerate(sources) if norm(s.get("article")) == norm(row["expected_article"])
                 and norm(row["expected_law"]) in norm(s.get("title"))]
        out["grounded_rank"] = ranks[0] if ranks else None
    # 정확성: 기대 키워드가 답에 모두 있는가 (키워드가 비면 판정 안 함)
    # 키워드는 '|'로 AND, 한 키워드 안의 '/'는 같은 뜻의 대체 표현(OR). 예: 100분의 100/100%
    kws = [k for k in row["expected_keywords"].split("|") if k]
    if kws:
        hit = lambda k: any(norm(alt) in norm(answer) for alt in k.split("/") if alt)
        out["accurate"] = all(hit(k) for k in kws)
        out["missing_keywords"] = "|".join(k for k in kws if not hit(k))
    # 환각: 자료에 없는 질문에 '찾을 수 없음'이라고 했는가
    if row["expect_not_found"] == "Y":
        out["hallucinated"] = not not_found
    if row["expect_review"] == "Y":
        out["review_ok"] = bool(res.get("needs_review"))
    return out


def rate(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None and r.get(key) != ""]
    return (round(sum(1 for v in vals if v is True) / len(vals), 3), len(vals)) if vals else (None, 0)


def run_once(cases, args, headers, run_no):
    rows = []
    for c in cases:
        t0 = time.time()
        try:
            r = requests.post(f"{args.base_url}/rag-query", headers=headers, timeout=120,
                              json={"prompt": c["question"], "source": c["source"], "top_k": args.top_k})
            res = r.json() if r.ok else {"response": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            res = {"response": f"ERROR: {e}"}
        ms = int((time.time() - t0) * 1000)
        j = judge(c, res)
        srcs = res.get("sources") or []
        ret = res.get("retrieved") or []
        # 기대 조문이 검색 후보에 있었는가 (답을 못 찾았을 때 검색 실패 vs LLM 판단 구분)
        exp_in_retrieved = None
        if c["expected_article"]:
            exp_in_retrieved = any(norm(x.get("article")) == norm(c["expected_article"])
                                   and norm(c["expected_law"]) in norm(x.get("title")) for x in ret)
        rows.append({"run": run_no, **c, **j, "expected_in_retrieved": exp_in_retrieved,
                     "latency_ms": ms, "needs_review": res.get("needs_review"),
                     "top_source": f"{srcs[0].get('title')} {srcs[0].get('article')}" if srcs else "",
                     "retrieved": " / ".join(f"{x.get('title')} {x.get('article')}" for x in ret),
                     "config": json.dumps(res.get("config") or {}, ensure_ascii=False),
                     "answer": (res.get("response") or "").replace("\n", " ")[:500]})
        ok = j.get("grounded", j.get("accurate", not j.get("hallucinated", False)))
        why = ""
        if ok is False and exp_in_retrieved is not None:
            why = "  (검색O·LLM이 거절)" if exp_in_retrieved else "  (검색 실패)"
        print(f"{'✅' if ok else '❌'} [{run_no}] #{c['id']:>2} {ms:>6}ms  {c['question'][:28]:<30} → {rows[-1]['top_source']}{why}")
    return rows


def summarize(rows):
    g, n_g = rate(rows, "grounded"); a, n_a = rate(rows, "accurate")
    h, n_h = rate(rows, "hallucinated"); rv, n_r = rate(rows, "review_ok")
    lat = sorted(r["latency_ms"] for r in rows)
    return {"grounded_rate": g, "grounded_n": n_g, "accuracy_rate": a, "accuracy_n": n_a,
            "hallucination_rate": h, "hallucination_n": n_h, "review_detect_rate": rv, "review_n": n_r,
            "latency_ms_p50": lat[len(lat) // 2] if lat else None, "latency_ms_max": lat[-1] if lat else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("RAG_BASE_URL", "http://localhost:8001"))
    ap.add_argument("--label", default="run")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--repeat", type=int, default=1, help="같은 설정 반복 횟수 (평균·범위 보고)")
    ap.add_argument("--employees", type=int, default=None)
    ap.add_argument("--company-types", default="")
    ap.add_argument("--only", default="", help="쉼표로 문항 id 지정 (예: 1,2,21)")
    args = ap.parse_args()

    # 서버가 꺼져 있으면 0ms 실패 결과가 저장되므로 시작 전에 확인하고 중단한다
    try:
        requests.get(f"{args.base_url}/health", timeout=5).raise_for_status()
    except Exception as e:
        sys.exit(f"RAG 서버에 연결할 수 없습니다 ({args.base_url}/health): {e}\n서버를 먼저 켜고 'Application startup complete'를 확인하세요.")

    token = make_token(args.employees, [t for t in args.company_types.split(",") if t])
    headers = {"Authorization": f"Bearer {token}"}
    with open(BASE / "eval_set.csv", encoding="utf-8-sig") as f:
        cases = list(csv.DictReader(f))
    if args.only:
        ids = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in ids]

    all_rows, runs = [], []
    for n in range(1, args.repeat + 1):
        rows = run_once(cases, args, headers, n)
        errors = [r for r in rows if str(r.get("answer", "")).startswith(("ERROR:", "HTTP "))]
        if errors:
            sys.exit(f"요청 실패 {len(errors)}건 (예: #{errors[0]['id']} {errors[0]['answer'][:120]}) → 결과를 저장하지 않습니다.")
        all_rows += rows
        runs.append(summarize(rows))
        print(f"--- run {n}: 근거성 {runs[-1]['grounded_rate']}, 정확성 {runs[-1]['accuracy_rate']}, 환각률 {runs[-1]['hallucination_rate']}\n")

    def agg(key):
        vals = [r[key] for r in runs if r[key] is not None]
        if not vals:
            return None
        return {"mean": round(sum(vals) / len(vals), 3), "min": min(vals), "max": max(vals)}

    # 문항별 안정성: 반복 중 결과가 바뀐 문항
    flaky = []
    for c in cases:
        res = [r.get("grounded", r.get("accurate", r.get("hallucinated"))) for r in all_rows if r["id"] == c["id"]]
        if len(set(map(str, res))) > 1:
            flaky.append(c["id"])

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
    out_dir = BASE / "results"; out_dir.mkdir(exist_ok=True)
    csv_path = out_dir / f"{stamp}_{args.label}.csv"
    keys = []
    for r in all_rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(all_rows)

    summary = {"label": args.label, "time": stamp, "top_k": args.top_k, "repeat": args.repeat,
               "employees": args.employees, "n": len(cases),
               "config": all_rows[0]["config"] if all_rows else None,
               "grounded_rate": agg("grounded_rate"), "accuracy_rate": agg("accuracy_rate"),
               "hallucination_rate": agg("hallucination_rate"), "review_detect_rate": agg("review_detect_rate"),
               "latency_ms_p50": agg("latency_ms_p50"), "latency_ms_max": agg("latency_ms_max"),
               "flaky_ids": flaky, "runs": runs}
    (out_dir / f"{stamp}_{args.label}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, ensure_ascii=False, indent=2))
    print(f"\n저장: {csv_path}")


if __name__ == "__main__":
    main()
