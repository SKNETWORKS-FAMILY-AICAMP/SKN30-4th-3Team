"""농사로 수집 문서에서 텃밭 전체에 적용되는 일반 지식을 추출한다.

용도
----
채소별 재배 문서와 텃밭 공통 관리 문서가 섞여 있는 농사로 수집 결과에서,
특정 식물 하나가 아니라 텃밭 전체의 준비·계획·관리·시설에 적용되는 문서만
별도 JSON 배열로 저장합니다.

포함 예
-------
- 올바르게 모종을 심는방법
- 도시농업 농자재 정보_밭 만들기
- 도시농업 농자재 정보_물주기
- 연간계획표 만들기
- 베란다 텃밭이란?
- 채소 재배목적에 맞는 맞춤형 텃밭 설계하세요

제외 예
-------
- 완두
- 토마토
- 감자 텃밭가꾸기
- 딸기 텃밭가꾸기

실행
----
python data/notebooks/NONGSARO_extract_garden_general.py

입력
----
data/interim/nongsaro_minifarm/
    nongsaro_minifarm_vegetable_documents.jsonl

출력
----
data/interim/nongsaro_minifarm/
    garden_general.json

주의
----
- 원본 JSONL은 수정하지 않습니다.
- 제목이 확실히 일반 텃밭 지식인 문서만 포함합니다.
- 새로운 일반 문서 제목이 생기면 `is_general_title()` 규칙을 추가합니다.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    DATA_DIR
    / "interim"
    / "nongsaro_minifarm"
    / "nongsaro_minifarm_vegetable_documents.jsonl"
)
DEFAULT_OUTPUT = (
    DATA_DIR
    / "interim"
    / "nongsaro_minifarm"
    / "garden_general.json"
)

GENERAL_EXACT_TITLES = {
    "실내 텃밭 가꾸는 요령과 재배 달력",
    "연간계획표 만들기",
    "올바르게 모종을 심는방법",
    "베란다 텃밭에서 잘 자라지 못하는 식물은?",
    "베란다 텃밭이란?",
    "식물정보가 포함된 모종포장 디자인",
    "채소 재배목적에 맞는 맞춤형 텃밭 설계하세요",
    "맛있는 텃밭채소원 만들기",
}

GENERAL_PREFIXES = (
    "도시농업 농자재 정보_",
    "텃밭작물 가을재배 캘린더",
    "텃밭작물 재배 캘린더",
)

GENERAL_EXCLUDED_TITLES = {
    "텃밭작물 재배 캘린더(모종을 이용한 텃밭재배)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="농사로 텃밭 공통 관리 문서 추출"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def normalize_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_general_title(title: str) -> bool:
    title = normalize_title(title)
    if title in GENERAL_EXCLUDED_TITLES:
        return False
    return title in GENERAL_EXACT_TITLES or title.startswith(GENERAL_PREFIXES)


def classify_topic(title: str) -> str:
    title = normalize_title(title)
    topic_rules = (
        ("transplanting", ("모종",)),
        ("sowing_seed", ("씨앗", "종자", "씨뿌리기")),
        ("soil_and_bed", ("밭 만들기", "밭 준비", "상토", "용기")),
        ("watering", ("물주기",)),
        ("environment", ("환경관리", "베란다", "실내 텃밭")),
        ("planning", ("계획", "캘린더", "달력", "설계", "채소원")),
        ("support_and_training", ("지주", "유인")),
        ("weed_management", ("잡초",)),
        ("pest_prevention", ("병해충",)),
        ("harvest", ("수확",)),
        ("materials", ("농자재", "재활용품", "식물재배기", "포장")),
    )
    for topic, keywords in topic_rules:
        if any(keyword in title for keyword in keywords):
            return topic
    return "general_garden_management"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{line_number}번째 행이 JSON 객체가 아닙니다.")
            rows.append(value)
    return rows


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    general_rows: list[dict[str, Any]] = []

    for row in rows:
        title = normalize_title(row.get("title"))
        if not is_general_title(title):
            continue
        output_row = dict(row)
        output_row["scope"] = "garden_general"
        output_row["topic"] = classify_topic(title)
        output_row["crop_or_plant"] = None
        general_rows.append(output_row)

    general_rows.sort(key=lambda row: normalize_title(row.get("title")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(general_rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("일반 텃밭 문서 추출 완료")
    print(f"- 입력 문서: {len(rows)}")
    print(f"- 일반 텃밭 문서: {len(general_rows)}")
    print(f"- 출력: {args.output}")


if __name__ == "__main__":
    main()
