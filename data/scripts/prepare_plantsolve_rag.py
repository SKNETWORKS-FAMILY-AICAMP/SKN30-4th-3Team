from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from common import INTERIM_DIR, RAW_DIR, now_iso, read_jsonl, write_jsonl
from config import ENV, OPENAI_API_KEY


INDEX_URL = "https://www.plantsolve.com/api/v1/plants/index.json"
SOURCE_KEY = "plantsolve_care"
DEFAULT_INPUT = RAW_DIR / "plantsolve_growth_care.jsonl"
DEFAULT_MASTER = RAW_DIR / "total_growth_care_187plants.jsonl"
DEFAULT_OUTPUT = INTERIM_DIR / "plantsolve_care.ko.jsonl"
DEFAULT_REPORT = INTERIM_DIR / "plantsolve_care.validation.json"

CARE_FIELDS = ("difficulty", "placement", "watering", "soil", "pruning", "bestFor")
LIGHT_FIELDS = ("description", "sunlightHours", "intensity", "indoorWindow", "shadeTolerance")
GROWTH_FIELDS = (
    "growthRate",
    "matureHeight",
    "matureSpread",
    "lifeCycle",
    "floweringSeason",
    "containerFriendly",
    "indoorCapable",
)

# The local master currently contains two known Korean-name/scientific-name
# inconsistencies. Do not let those aliases become retrieval keywords.
REJECTED_ALIASES_BY_SCIENTIFIC = {
    "dracaena sanderiana": {"행운목"},
    "lavandula angustifolia": {"스위트 라벤더"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate PlantSolve records, exact-match scientific names, and translate safe RAG documents."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--master", default=str(DEFAULT_MASTER))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--model", default=ENV.get("CHAT_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--skip-translation", action="store_true")
    parser.add_argument(
        "--reuse-existing-translations",
        action="store_true",
        help="Reuse text from the current --output file and re-run deterministic cleanup/validation.",
    )
    return parser.parse_args()


def fetch_index() -> list[dict[str, Any]]:
    request = Request(INDEX_URL, headers={"User-Agent": "Farmhani-RAG-data-validation/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("PlantSolve index response must be a JSON array.")
    return payload


def normalized_scientific_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def clean_value(value: Any) -> str:
    if isinstance(value, dict):
        parts = [f"{key}: {clean_value(item)}" for key, item in value.items() if clean_value(item)]
        return "; ".join(parts)
    if isinstance(value, list):
        return "; ".join(clean_value(item) for item in value if clean_value(item))
    text = str(value or "").strip()
    return text.replace("째F", "°F").replace("째C", "°C")


def selected_payload(row: dict[str, Any]) -> dict[str, Any]:
    care = row.get("care") if isinstance(row.get("care"), dict) else {}
    lighting = row.get("lighting") if isinstance(row.get("lighting"), dict) else {}
    growth = row.get("growthCharacteristics") if isinstance(row.get("growthCharacteristics"), dict) else {}
    parameters = row.get("parameters") if isinstance(row.get("parameters"), dict) else {}
    return {
        "care": {key: clean_value(care.get(key)) for key in CARE_FIELDS if clean_value(care.get(key))},
        "lighting": {key: clean_value(lighting.get(key)) for key in LIGHT_FIELDS if clean_value(lighting.get(key))},
        "environment": {
            key: clean_value(parameters.get(key))
            for key in ("temperature", "humidity", "soilPH")
            if clean_value(parameters.get(key))
        },
        "growth": {key: clean_value(growth.get(key)) for key in GROWTH_FIELDS if clean_value(growth.get(key))},
    }


def is_useful(payload: dict[str, Any]) -> bool:
    care = payload["care"]
    has_core_care = bool(care.get("watering") or care.get("placement") or care.get("soil"))
    field_count = sum(len(section) for section in payload.values())
    return has_core_care and field_count >= 5


def build_candidates(
    raw_rows: list[dict[str, Any]], master_rows: list[dict[str, Any]], index_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    aliases_by_scientific: dict[str, list[str]] = defaultdict(list)
    display_scientific: dict[str, str] = {}
    for row in master_rows:
        scientific = str(row.get("scientific_name") or "").strip()
        key = normalized_scientific_name(scientific)
        if not key:
            continue
        display_scientific.setdefault(key, scientific)
        for alias in row.get("crop_or_plant") or []:
            alias = str(alias).strip()
            rejected_aliases = REJECTED_ALIASES_BY_SCIENTIFIC.get(key, set())
            if alias and alias not in rejected_aliases and alias not in aliases_by_scientific[key]:
                aliases_by_scientific[key].append(alias)

    index_by_slug = {str(row.get("slug")): row for row in index_rows if row.get("slug")}
    raw_by_slug = {str(row.get("slug")): row for row in raw_rows if row.get("slug")}
    candidates: list[dict[str, Any]] = []
    rejected_missing_master = 0
    rejected_low_content = 0

    for slug, index_row in sorted(index_by_slug.items()):
        raw = raw_by_slug.get(slug)
        if not raw:
            continue
        scientific_key = normalized_scientific_name(index_row.get("scientificName"))
        aliases = aliases_by_scientific.get(scientific_key, [])
        if not aliases:
            rejected_missing_master += 1
            continue
        payload = selected_payload(raw)
        if not is_useful(payload):
            rejected_low_content += 1
            continue
        candidates.append(
            {
                "slug": slug,
                "scientific_name": display_scientific[scientific_key],
                "aliases_ko": aliases,
                "common_name": str(raw.get("common_name") or slug.replace("-", " ").title()),
                "updated_at": index_row.get("updatedAt"),
                "payload": payload,
            }
        )

    stats = {
        "raw_records": len(raw_rows),
        "official_index_records": len(index_rows),
        "accepted_exact_scientific_matches": len(candidates),
        "rejected_no_exact_master_match": rejected_missing_master,
        "rejected_low_care_content": rejected_low_content,
    }
    return candidates, stats


def translate_batch(client: Any, model: str, batch: list[dict[str, Any]]) -> dict[str, str]:
    inputs = [
        {
            "id": row["slug"],
            "korean_names": row["aliases_ko"],
            "scientific_name": row["scientific_name"],
            "english_common_name": row["common_name"],
            "care_data": row["payload"],
        }
        for row in batch
    ]
    prompt = (
        "다음 JSON의 식물 생육 관리 정보만 한국어 RAG 문서로 번역하세요. "
        "원문에 없는 내용을 추가하거나 진단·치료·독성 정보를 만들지 마세요. "
        "학명과 수치 범위는 보존하고, 영미 단위가 있으면 원 단위를 남긴 채 괄호에 미터법 환산값을 덧붙이세요. "
        "각 문서는 식물명/학명, 난이도, 배치와 빛, 물주기, 토양, 가지치기, 온습도와 pH, 생장 특성을 "
        "해당 데이터가 있을 때만 짧은 소제목으로 구성하세요. "
        "반드시 {\"documents\":[{\"id\":\"...\",\"text_ko\":\"...\"}]} 형태의 JSON만 반환하세요.\n\n"
        + json.dumps(inputs, ensure_ascii=False)
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "당신은 원문 충실성을 우선하는 농업 데이터 번역가입니다."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    result = json.loads(content)
    return {str(item["id"]): str(item["text_ko"]).strip() for item in result.get("documents", [])}


def translate_candidates(candidates: list[dict[str, Any]], model: str, batch_size: int) -> dict[str, str]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required unless --skip-translation is used.")
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=120.0, max_retries=2)
    translated: dict[str, str] = {}
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                translated.update(translate_batch(client, model, batch))
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        else:
            raise RuntimeError(f"Translation failed for batch starting at {start}: {last_error}")
    return translated


def valid_translation(text: str) -> bool:
    korean_chars = len(re.findall(r"[가-힣]", text))
    forbidden = ("placeholder summary", "toxicity", "독성", "치료법", "진단:")
    return len(text) >= 180 and korean_chars >= 80 and not any(term.casefold() in text.casefold() for term in forbidden)


def normalize_korean_translation(text: str) -> str:
    replacements = {
        "beginner": "초급",
        "intermediate": "중급",
        "advanced": "고급",
        "hard": "어려움",
        "fast": "빠름",
        "moderate": "보통",
        "slow": "느림",
        "perennial": "다년생",
        "annual": "일년생",
        "non-flowering": "비개화성",
        "year-round": "연중",
        "spring": "봄",
        "summer": "여름",
        "autumn": "가을",
        "fall": "가을",
        "winter": "겨울",
        "yes": "가능",
        "no": "불가",
        "min": "최저",
        "max": "최고",
        "value": "값",
        "unit": "단위",
        "celsius": "섭씨",
    }
    cleaned = text
    for source, target in replacements.items():
        cleaned = re.sub(rf"\b{re.escape(source)}\b", target, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+to\s+", "~", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def main() -> None:
    args = parse_args()
    raw_rows = read_jsonl(Path(args.input))
    master_rows = read_jsonl(Path(args.master))
    index_rows = fetch_index()
    candidates, stats = build_candidates(raw_rows, master_rows, index_rows)

    if args.reuse_existing_translations:
        existing_rows = read_jsonl(Path(args.output))
        translated = {
            str(row.get("doc_id", "")).removeprefix("plantsolve-care:"): str(row.get("text") or "")
            for row in existing_rows
        }
    elif args.skip_translation:
        translated = {row["slug"]: json.dumps(row["payload"], ensure_ascii=False) for row in candidates}
    else:
        translated = translate_candidates(candidates, args.model, args.batch_size)

    collected_at = now_iso()
    documents: list[dict[str, Any]] = []
    rejected_translation = 0
    for row in candidates:
        text = normalize_korean_translation(translated.get(row["slug"], ""))
        if not args.skip_translation and not valid_translation(text):
            rejected_translation += 1
            continue
        documents.append(
            {
                "doc_id": f"plantsolve-care:{row['slug']}",
                "source_id": SOURCE_KEY,
                "source_key": SOURCE_KEY,
                "title": f"{row['aliases_ko'][0]} 생육 관리 ({row['scientific_name']})",
                "publisher": "PlantSolve",
                "url": f"https://www.plantsolve.com/plants/{row['slug']}",
                "license": "CC BY 4.0",
                "collected_at": collected_at,
                "category": "indoor_care",
                "priority": 2,
                "usage_scope": "rag",
                "section": "생육 관리",
                "crop_or_plant": row["aliases_ko"],
                "symptom_keywords": ["general_care"],
                "safety_tags": ["not_diagnosis", "source_terms_required"],
                "text": text,
                "scientific_name": row["scientific_name"],
                "source_updated_at": row["updated_at"],
            }
        )

    stats.update(
        {
            "translated_documents": len(translated),
            "rejected_translation_quality": rejected_translation,
            "output_documents": len(documents),
            "excluded_fields": ["summary", "troubleshooting", "care.toxicity", "care.toxicityNote"],
            "license": "CC BY 4.0 with source attribution and canonical backlink",
            "generated_at": collected_at,
        }
    )
    write_jsonl(Path(args.output), documents)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
