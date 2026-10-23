"""검색 평가: 정답 생성이 아니라 기대 조항의 top-k 포함 여부를 측정한다."""
import argparse
import json
from pathlib import Path

from .index import load_index, search

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("연차 유급휴가의 발생 조건은 무엇인가요?", "labor_standards", "제60조"),
    ("근로계약을 체결할 때 명시해야 할 근로조건은 무엇인가요?", "labor_standards", "제17조"),
    ("해고 예고는 어떻게 해야 하나요?", "labor_standards", "제26조"),
    ("연장근로 시간의 제한은 어떻게 되나요?", "labor_standards", "제53조"),
    ("야간근로에 대한 가산임금 규정은 무엇인가요?", "labor_standards", "제56조"),
    ("근로기준법은 어떤 규모의 사업장에 적용되나요?", "labor_standards", "제11조"),
    ("퇴직급여제도를 설정해야 하는 대상과 제외 대상은 누구인가요?", "retirement", "제4조"),
    ("수습 중인 근로자에게 다른 최저임금액을 적용하는 조건은 무엇인가요?", "minimum_wage", "제5조"),
    ("육아휴직 신청 대상과 기간은 무엇인가요?", "equal_employment", "제19조"),
    ("중소기업퇴직연금기금제도의 정의와 적용 인원은 어떻게 되나요?", "retirement", "제2조"),
    ("벤처기업육성에 관한 특별법에서 벤처기업의 정의는 무엇인가요?", "venture", "제2조"),
    ("사회적기업의 인증 요건 및 인증 절차는 무엇인가요?", "social_enterprise", "제8조"),
    ("소상공인기본법에서 소상공인의 정의는 무엇인가요?", "small_business", "제2조"),
    ("여성기업의 확인을 신청하고 확인받는 절차는 무엇인가요?", "women_enterprise", "제20조의2"),
    ("장애인기업활동 촉진법에서 장애인기업의 정의는 무엇인가요?", "disabled_enterprise", "제2조"),
    ("중소기업기본법상 중소기업자의 범위는 무엇인가요?", "sme", "제2조"),
    ("중소기업창업 지원법에서 창업과 창업기업의 정의는 무엇인가요?", "startup", "제2조"),
]


def evaluate(index: Path, output: Path, mode: str = "hybrid"):
    store = load_index(index)
    rows = []
    for question, law, article in CASES:
        result = search(index, question, 5, store=store, mode=mode)
        rank = next((i + 1 for i, hit in enumerate(result["hits"])
                     if hit["metadata"]["law_id"] == law and hit["metadata"]["article"] == article), None)
        rows.append({"question": question, "expected_law": law, "expected_article": article, "rank": rank, "result": result})
    passed = sum(r["rank"] is not None for r in rows)
    model = json.loads((index / "manifest.json").read_text())["embedding_model"]
    report = {"metric": "expected_article_hit_at_5", "embedding_model": model, "retrieval_mode": mode,
              "passed": passed, "total": len(rows), "rate": passed / len(rows),
              "note": "기대 조항 포함 여부만 평가. 답변 정확성·법적 적용 판단·범위 밖 거절 성능을 의미하지 않음.", "results": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return {"model": model, "passed": passed, "total": len(rows), "ranks": [r["rank"] for r in rows]}


if __name__ == "__main__":
    from dotenv import load_dotenv
    from .__main__ import ROOT
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=ROOT / "faiss_db/pdf")
    parser.add_argument("--output", type=Path, default=ROOT / "data/laws/processed/search_evaluation.json")
    parser.add_argument("--mode", choices=["hybrid", "dense"], default="hybrid")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.index, args.output, args.mode), ensure_ascii=False))
