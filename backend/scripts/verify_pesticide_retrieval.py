"""농약 데이터 RAG 연동 검증 (계획보고서 AC-1 ~ AC-4).

UNION 마이그레이션(supabase/migrations/20260730120000_*.sql) 적용 전후에 같은
명령으로 실행해 결과를 비교하는 용도입니다. LLM 심판을 쓰지 않고 검색 결과와
안전 고지만 결정적으로 검사하므로 저렴하고 재현 가능합니다.

  AC-1  작물 오매칭 차단 — 지정 작물과 무관한 문서가 통과하지 않는지
  AC-2  농약 근거 사용 시 안전 고지 포함 여부
  AC-3  "토마토 + 응애 + 농약" 질의에서 토마토 문서 검색 (마이그레이션 후 통과)
  AC-4  실내식물(몬스테라 등) 농약 질의 회귀

실행:
    cd backend
    python scripts/verify_pesticide_retrieval.py
    python scripts/verify_pesticide_retrieval.py --json   # CI/리포트용
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.rag import nodes_generation  # noqa: E402
from app.services.rag.plant_terms import resolve_target_crop_terms  # noqa: E402
from app.services.rag.vectorstore import search_documents  # noqa: E402


# (케이스 ID, 식물명, 품종, 질문, 이 작물 문서로 인정할 태그, 농약 데이터 기대 여부)
CASES = [
    ("AC3_tomato_mite", "토마토", "Solanum lycopersicum",
     "토마토 잎에 응애가 보이는데 어떤 농약을 써야 하나요", True),
    ("AC1_tomato_blight", "토마토", "Solanum lycopersicum",
     "토마토 역병이 의심되는데 방제 방법을 알려주세요", True),
    ("AC4_monstera_mite", "몬스테라", "Monstera deliciosa",
     "몬스테라 잎 뒷면에 응애가 보여요. 약제를 써야 할까요", True),
    ("AC4_monstera_water", "몬스테라", "Monstera deliciosa",
     "몬스테라 물주기는 얼마나 자주 해야 하나요", False),
    ("REG_strawberry", "딸기", "Fragaria ananassa",
     "딸기 잎에 흰가루 같은 것이 생겼어요", False),
]


def run_case(plant_name, species, question, top_k=8):
    target_terms = resolve_target_crop_terms(plant_name, species)
    results = search_documents(question, top_k=top_k, target_crop_terms=target_terms)
    docs = [{"content": r.content, "metadata": r.metadata, "score": r.score} for r in results]

    draft = {
        "summary": "관찰이 필요합니다.",
        "possibleCauses": ["원인 후보"],
        "todayActions": ["잎 상태를 확인합니다."],  # 위험 키워드 없음 — 태그 기반 고지만 검사
        "observationChecklist": ["경과를 관찰합니다."],
        "citations": [],
    }
    final = nodes_generation.safety_review({
        "draft_answer": draft,
        "retrieved_docs": docs,
        "response_mode": "expert",
    })["final_answer"]

    tags = nodes_generation.collect_document_safety_tags(docs)
    target_lower = {t.lower() for t in target_terms}
    mismatched = []
    own_crop_docs = 0
    for doc in docs:
        crop_tags = [str(c) for c in (doc["metadata"].get("crop_or_plant") or [])]
        if not crop_tags:
            continue
        if {c.lower() for c in crop_tags} & target_lower:
            own_crop_docs += 1
        else:
            mismatched.append({"title": doc["metadata"].get("title"), "crop_or_plant": crop_tags})

    return {
        "target_crop_terms": target_terms,
        "retrieved": len(docs),
        "own_crop_docs": own_crop_docs,
        "mismatched_docs": mismatched,
        "safety_tags": tags,
        "has_pesticide_doc": "pesticide_caution" in tags,
        "pesticide_notice_present": nodes_generation.PESTICIDE_CAUTION_NOTICE in final["safetyNotice"],
        "titles": [d["metadata"].get("title") for d in docs],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    # 파이프라인(search_documents)과 동일하게 환경변수와 settings 양쪽을 확인한다.
    if not (os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY):
        print("OPENAI_API_KEY가 없어 벡터 검색을 수행할 수 없습니다.", file=sys.stderr)
        return 2

    report = {}
    for case_id, name, species, question, expect_pesticide in CASES:
        outcome = run_case(name, species, question, top_k=args.top_k)
        outcome["question"] = question
        outcome["plant"] = f"{name} / {species}"
        outcome["expect_pesticide_data"] = expect_pesticide
        report[case_id] = outcome

    # ---- 판정 -------------------------------------------------------------
    ac1 = all(not r["mismatched_docs"] for r in report.values())
    ac2 = all(r["pesticide_notice_present"] for r in report.values() if r["has_pesticide_doc"])
    ac3 = report["AC3_tomato_mite"]["own_crop_docs"] >= 1
    ac4 = report["AC4_monstera_mite"]["retrieved"] >= 1

    verdicts = {
        "AC-1 작물 오매칭 차단": ac1,
        "AC-2 농약 안전고지 포함": ac2,
        "AC-3 토마토 농약 문서 검색": ac3,
        "AC-4 실내식물 회귀": ac4,
    }

    if args.json:
        print(json.dumps({"cases": report, "verdicts": verdicts}, ensure_ascii=False, indent=2))
        return 0 if all(verdicts.values()) else 1

    for case_id, r in report.items():
        print(f"── {case_id}  ({r['plant']})")
        print(f"   질의            : {r['question']}")
        print(f"   target_crop_terms: {r['target_crop_terms']}")
        print(f"   검색 {r['retrieved']}건 / 해당 작물 문서 {r['own_crop_docs']}건 / 오매칭 {len(r['mismatched_docs'])}건")
        print(f"   농약 근거       : {'있음' if r['has_pesticide_doc'] else '없음'}"
              f"  → 안전고지 {'포함' if r['pesticide_notice_present'] else '미포함'}")
        for title in r["titles"][:4]:
            print(f"     · {title}")
        for bad in r["mismatched_docs"]:
            print(f"     ⚠️ 오매칭: {bad['title']} {bad['crop_or_plant']}")
        print()

    print("=" * 60)
    for label, ok in verdicts.items():
        print(f"  {'✅ PASS' if ok else '❌ FAIL'}  {label}")
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
