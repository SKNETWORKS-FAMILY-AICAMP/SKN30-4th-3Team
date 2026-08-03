"""농사로 생육 보충 normalized JSONL의 본문을 2차 전처리합니다.

[용도]
- 사이트 UI 잔재 제거
- 단독 물음표 구분 기호 제거
- 확정된 학명 오탈자 교정
- 구체적인 농약명·희석배수·살포 지시 제거 및 안전 안내 대체
- 본문 첫 부분의 중복 식물명 제거
- 구조가 깨진 재배 달력의 월/상·중·하 토큰 제거
- 내용이 없는 번호 제목 제거

[선행조건]
아래 파일이 먼저 생성되어 있어야 합니다.
python data/notebooks/normalize/coverage_plantGrow_prepare_nongsaro_vegetable_rag.py

[실행]
python data/notebooks/normalize/coverage_plantGrow_preprocess_v2.py

[기본 입력]
data/interim/nongsaro_minifarm/
    coverage_plantGrow.nongsaro_vegetable.normalized.jsonl

[기본 출력]
data/interim/nongsaro_minifarm/normalizev2/
    coverage_plantGrow.nongsaro_vegetable.normalized.v2.jsonl
    coverage_plantGrow.nongsaro_vegetable.preprocess_report.json

[다음 단계]
python data/scripts/chunk_documents.py `
  --input data/interim/nongsaro_minifarm/normalizev2/coverage_plantGrow.nongsaro_vegetable.normalized.v2.jsonl `
  --output data/processed/rag_chunks.nongsaro_vegetable_supplement.jsonl `
  --sources-output data/processed/rag_sources.nongsaro_vegetable_supplement.jsonl `
  --max-chars 1200 `
  --overlap-chars 140

[주의]
- 원본 normalized JSONL은 수정하지 않습니다.
- 학명은 SCIENTIFIC_NAME_CORRECTIONS에 등록한 확정 오탈자만 교정합니다.
- 병해충명·증상·비화학적 관리법은 유지하고 구체적인 약제 사용 지시만 정리합니다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import normalize_text, read_jsonl, write_jsonl  # noqa: E402


INPUT_DIR = DATA_DIR / "interim" / "nongsaro_minifarm"
DEFAULT_INPUT = (
    INPUT_DIR / "coverage_plantGrow.nongsaro_vegetable.normalized.jsonl"
)
DEFAULT_OUTPUT_DIR = INPUT_DIR / "normalizev2"
DEFAULT_OUTPUT_NAME = (
    "coverage_plantGrow.nongsaro_vegetable.normalized.v2.jsonl"
)
DEFAULT_REPORT_NAME = (
    "coverage_plantGrow.nongsaro_vegetable.preprocess_report.json"
)

PESTICIDE_CAUTION = (
    "약제 사용이 필요한 경우 제품 라벨과 농촌진흥청의 최신 등록정보 및 "
    "안전사용기준을 확인해야 합니다."
)

UI_ARTIFACTS = (
    "목록으로 이동하기",
    "내용 인쇄하기",
    "콘텐츠 담당자",
)

# 원문과 표준 학명을 대조해 확정한 오탈자만 교정합니다.
SCIENTIFIC_NAME_CORRECTIONS = {
    "Rosamarinus officinalis": "Rosmarinus officinalis",
    "Apium gravelens L.": "Apium graveolens L.",
    "P isum Sativum L.": "Pisum sativum L.",
    "P isum sativum L.": "Pisum sativum L.",
    "Calocasia esculenta": "Colocasia esculenta",
    "Calocasia esculenta(L.) sehott": "Colocasia esculenta (L.) Schott",
    "Brassica campestris L. ssp. chinesis Jusl.": (
        "Brassica campestris L. ssp. chinensis Jusl."
    ),
}

# 웹 표의 셀 순서만 남아 의미가 사라진 재배 달력 토큰입니다.
CALENDAR_CELL_VALUES = {
    "구분",
    "월",
    "작물",
    "상",
    "중",
    "하",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "ᅳ",
    "ㅡ",
}

NUMBERED_HEADING_RE = re.compile(r"^\s*\d+[.)]\s*\S.*$")
SCHEDULE_HEADING_RE = re.compile(r"^\s*\d+[.)]\s*재배\s*일정\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="농사로 생육 보충 normalized JSONL을 2차 전처리합니다."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    parser.add_argument("--report-name", default=DEFAULT_REPORT_NAME)
    return parser.parse_args()


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def remove_ui_artifacts(text: str) -> tuple[str, int]:
    changes = 0
    for artifact in UI_ARTIFACTS:
        count = text.count(artifact)
        if count:
            text = text.replace(artifact, "")
            changes += count

    # 페이지 하단의 조회수·담당자 행이 들어온 경우 행 단위로 제거합니다.
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("조회수"):
            changes += 1
            continue
        if stripped.startswith("* 콘텐츠 담당자"):
            changes += 1
            continue
        kept.append(line)
    return "\n".join(kept), changes


def remove_standalone_question_mark(text: str) -> tuple[str, int]:
    # 문자 사이의 깨진 구분 기호만 제거하고 문장 끝 물음표는 보존합니다.
    text, count = re.subn(r"(?<=\s)\?(?=\s)", "", text)
    return text, count


def correct_scientific_names(text: str) -> tuple[str, int]:
    changes = 0
    # 긴 문자열을 먼저 적용해야 부분 교정이 전체 교정을 방해하지 않습니다.
    for wrong, correct in sorted(
        SCIENTIFIC_NAME_CORRECTIONS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        count = text.count(wrong)
        if count:
            text = text.replace(wrong, correct)
            changes += count
    return text, changes


def remove_duplicate_leading_title(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    if len(nonempty_indexes) < 2:
        return text, 0

    first, second = nonempty_indexes[:2]
    if compact(lines[first]) != compact(lines[second]):
        return text, 0
    del lines[second]
    return "\n".join(lines), 1


def remove_broken_calendar_cells(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    removed = 0
    output: list[str] = []
    inside_schedule = False
    inside_detected_calendar = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        # 일부 문서는 재배 달력 위 제목이 잘못 수집되어 "재배일정"이 아니다.
        # 제목 대신 월·숫자·상중하가 반복되는 표 셀 자체를 탐지한다.
        if stripped in {"월", "구분"}:
            window = [candidate.strip() for candidate in lines[index : index + 55]]
            month_count = sum(value in {str(number) for number in range(1, 13)} for value in window)
            period_count = sum(value in {"상", "중", "하"} for value in window)
            if month_count >= 10 and period_count >= 9:
                inside_detected_calendar = True

        if inside_detected_calendar:
            if stripped in CALENDAR_CELL_VALUES:
                removed += 1
                continue

            # 숫자 셀 다음에 남은 짧은 행 이름(예: 비트)도 표의 행 머리이므로
            # 바로 뒤에 파종·정식·수확 범례가 있으면 함께 제거한다.
            next_nonempty = next(
                (
                    candidate.strip()
                    for candidate in lines[index + 1 :]
                    if candidate.strip()
                ),
                "",
            )
            if (
                stripped
                and len(stripped) <= 10
                and any(
                    term in next_nonempty
                    for term in ("씨뿌리기", "아주심기", "수확")
                )
            ):
                removed += 1
                continue

            # 범례 문장은 정보가 있으므로 보존하고 달력 표 구간을 종료한다.
            if any(term in stripped for term in ("씨뿌리기", "아주심기", "수확")):
                inside_detected_calendar = False

        if SCHEDULE_HEADING_RE.match(stripped):
            inside_schedule = True
            output.append(line)
            continue

        if inside_schedule and NUMBERED_HEADING_RE.match(stripped):
            inside_schedule = False

        if inside_schedule and stripped in CALENDAR_CELL_VALUES:
            removed += 1
            continue

        output.append(line)

    return "\n".join(output), removed


def remove_empty_numbered_headings(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    removed = 0
    output: list[str] = []

    for index, line in enumerate(lines):
        if not NUMBERED_HEADING_RE.match(line.strip()):
            output.append(line)
            continue

        next_nonempty = next(
            (
                candidate.strip()
                for candidate in lines[index + 1 :]
                if candidate.strip()
            ),
            "",
        )
        if next_nonempty and NUMBERED_HEADING_RE.match(next_nonempty):
            removed += 1
            continue
        output.append(line)

    return "\n".join(output), removed


def replace_pesticide_instructions(text: str) -> tuple[str, int]:
    """구체적인 약제 사용 지시를 제거하되 일반 예방·관찰 정보는 유지합니다."""
    replacements: tuple[tuple[str, str], ...] = (
        (
            r"예방을 위해서 건전한 종자를 사용하여야 하며\s*"
            r"완두에 등록된 약제로 종자소독을 한다\.?",
            "예방을 위해 건전한 종자를 사용합니다.",
        ),
        (
            r"파종 2주 전에 밑거름 비료를 넣고 평탄작업을 할 때\s*"
            r"입고병 방제약을 살포하여 토양과\s*잘 섞이도록 한다\.?",
            "파종 2주 전에 밑거름 비료를 넣고 토양과 잘 섞이도록 합니다.",
        ),
        (
            r"(?:발생초기에|애벌레가 부화하여 잎의 대공으로 들어가기 전에)\s*"
            r"대파에\s*등록된 약제를\s*구입하여 살포한다\.?",
            "",
        ),
        (
            r"약제에 대한 내성이 강하므로 성분이 다른 약제를\s*"
            r"1주일 간격으로\s*번갈아 살포한다\.?",
            "",
        ),
        (
            r"[∙·\-]\s*씨앗 뿌린 후 제초제를 살포할 경우 "
            r"1개월정도 잡초가 나지 않으나",
            "",
        ),
        (
            r"발생초기 약제살포를 1주일 간격 2~3회 연속 포하면 "
            r"방제가 가능",
            "",
        ),
        (
            r"보르도액과 다이젠 등을 뿌려서 방제한다\.?",
            PESTICIDE_CAUTION,
        ),
        (
            r"장마\(6월 20일경\)가 시작하기 전을 1차로 "
            r"메타실동수화제\s*1000배액 약제로 "
            r"20~30일 간격으로 3차례 살포해주는 것이 좋다\.?"
            r"약제를 치기",
            PESTICIDE_CAUTION,
        ),
        (
            r"반드시 대목이나 종자를 베노람 수화제 등을 사용하여 "
            r"소독 후 파종한다",
            PESTICIDE_CAUTION,
        ),
        (
            r"적절한 수분조건이 되도록 관리하고 벤레이트 등으로 "
            r"종자를 소독한다\.?",
            "적절한 수분조건이 되도록 관리합니다.",
        ),
        (
            r"진딧물은 정식 후 발생 초기에 방제를 하는 것이 좋으며 "
            r"새로 나온 잎의 뒷면에 모여 기생하면 잎이 오그라들고 "
            r"말리기 때문에 약액이 진딧물 몸에 묻도록 살포한다\.?",
            "진딧물은 새로 나온 잎의 뒷면을 중심으로 발생 여부를 관찰합니다. "
            + PESTICIDE_CAUTION,
        ),
    )

    changes = 0
    for pattern, replacement in replacements:
        text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        changes += count

    # 같은 문서에서 안전 안내가 반복되면 한 번만 남깁니다.
    if text.count(PESTICIDE_CAUTION) > 1:
        first = True
        kept: list[str] = []
        for line in text.splitlines():
            if PESTICIDE_CAUTION in line:
                if first:
                    first = False
                else:
                    line = line.replace(PESTICIDE_CAUTION, "").strip()
                    changes += 1
            if line:
                kept.append(line)
        text = "\n".join(kept)

    return text, changes


def preprocess_text(text: str) -> tuple[str, Counter[str]]:
    changes: Counter[str] = Counter()
    processors = (
        ("ui_artifacts", remove_ui_artifacts),
        ("standalone_question_marks", remove_standalone_question_mark),
        ("scientific_name_corrections", correct_scientific_names),
        ("duplicate_leading_titles", remove_duplicate_leading_title),
        ("broken_calendar_cells", remove_broken_calendar_cells),
        ("pesticide_instructions", replace_pesticide_instructions),
        ("empty_numbered_headings", remove_empty_numbered_headings),
    )

    for label, processor in processors:
        text, count = processor(text)
        changes[label] += count

    return normalize_text(text), changes


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    output_path = args.output_dir / args.output_name
    report_path = args.output_dir / args.report_name

    processed: list[dict[str, Any]] = []
    total_changes: Counter[str] = Counter()
    document_reports: list[dict[str, Any]] = []

    for row in rows:
        original_text = str(row.get("text") or "")
        cleaned_text, changes = preprocess_text(original_text)
        total_changes.update(changes)
        processed.append({**row, "text": cleaned_text})
        document_reports.append(
            {
                "doc_id": row.get("doc_id"),
                "title": row.get("title"),
                "crop_or_plant": row.get("crop_or_plant") or [],
                "before_chars": len(original_text),
                "after_chars": len(cleaned_text),
                "changes": dict(changes),
            }
        )

    count = write_jsonl(output_path, processed)
    report = {
        "input": str(args.input),
        "output": str(output_path),
        "document_count": count,
        "total_changes": dict(total_changes),
        "documents": document_reports,
    }
    write_report(report_path, report)

    print(f"Preprocessed {count} documents: {output_path}")
    print(f"Report: {report_path}")
    for label, value in total_changes.items():
        print(f"- {label}: {value}")


if __name__ == "__main__":
    main()
