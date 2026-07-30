"""
국립수목원 식물자원 OpenAPI로 기존 식물 검색 결과를 다시 검증합니다.

용도
----
기존 웹 수집 결과 ``plant_growth_collected.jsonl``에서 검색에 성공한 항목의
``searched_term``을 가져와 공식 OpenAPI의 식물도감 목록을 조회합니다.
첫 번째 결과 1건의 식물명이 ``searched_term``과 정확히 같을 때만
식물도감 상세정보 API까지 바로 호출하여 함께 저장합니다.
비추천명이나 유사 이름만 일치하는 결과는 저장하지 않습니다.

동시에 다음 항목을 하나의 미수집 목록으로 정리합니다.

- 기존 ``unmatched.json`` 항목
- 원본 검색 목록에는 있지만 ``plant_growth_collected.jsonl``에 없는 항목

선행 조건
---------
1. 프로젝트 루트의 ``.env``에 아래 값을 설정합니다.
   PUBLIC_DATA_PORTAL_API_KEY=공공데이터포털_일반인증키
2. 공공데이터포털에서 아래 API의 활용신청이 승인되어 있어야 합니다.
   산림청 국립수목원_식물자원 조회 서비스

실행 순서
---------
1. API 호출 없이 대상과 미수집 목록만 확인

   python data/notebooks/KNA_collect_plant_growth_api.py --prepare-only

2. 기존 자동 일치 항목의 검색어로 API 후보 조회

   python data/notebooks/KNA_collect_plant_growth_api.py

3. 중단 후 이어서 조회

   python data/notebooks/KNA_collect_plant_growth_api.py --resume

4. 먼저 3종만 시험

   python data/notebooks/KNA_collect_plant_growth_api.py --limit 3

5. 검증된 needs_review 학명 30종 검색 및 상세정보 수집

   python data/notebooks/KNA_collect_plant_growth_api.py --scientific-review

6. 학명 검색을 먼저 3종만 시험

   python data/notebooks/KNA_collect_plant_growth_api.py --scientific-review --limit 3

산출물
------
data/interim/coverage_plant_grow_kna_api/
    plant_growth_api_collected.jsonl
    plant_growth_not_collected.json
    plant_growth_api_scientific_unmatched.json

주의
----
- 검색 결과명이 ``searched_term``과 정확히 일치하는 경우만 남깁니다.
- 동명이종 가능성이 있으면 ``scientific_name``과 ``plant_id``를 추가로
  검증한 뒤 상세정보 수집 대상으로 사용해야 합니다.
- API 자료는 공공누리 제4유형이므로 출처표시·비상업·변경금지 조건을
  확인한 뒤 전처리, 임베딩 또는 서비스 제공에 사용해야 합니다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = DATA_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config import require_env  # noqa: E402


DEFAULT_SOURCE_DIR = DATA_DIR / "interim" / "coverage_plant_grow_nature"
DEFAULT_COLLECTED = DEFAULT_SOURCE_DIR / "plant_growth_collected.jsonl"
DEFAULT_UNMATCHED = DEFAULT_SOURCE_DIR / "unmatched.json"
DEFAULT_ORIGINAL_INPUT = DATA_DIR / "notebooks" / "re_search_plant_grow_list.json"
DEFAULT_REVIEW_NAMES = (
    DATA_DIR / "notebooks" / "KNA_plant_growth_review_scientific_names.json"
)
DEFAULT_VERIFIED_ALIASES = (
    DATA_DIR / "notebooks" / "KNA_plant_growth_search_aliases.json"
)
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "coverage_plant_grow_kna_api"

SEARCH_URL = (
    "https://apis.data.go.kr/1400119/PlantResource/plantPilbkSearch"
)
DETAIL_URL = "https://apis.data.go.kr/1400119/PlantResource/plantPilbkInfo"
API_SOURCE_URL = "https://www.data.go.kr/data/15143513/openapi.do"
OUTPUT_COLLECTED = "plant_growth_api_collected.jsonl"
LEGACY_OUTPUT_CANDIDATES = "plant_growth_api_candidates.jsonl"
OUTPUT_NOT_COLLECTED = "plant_growth_not_collected.json"
OUTPUT_SCIENTIFIC_UNMATCHED = "plant_growth_api_scientific_unmatched.json"
OUTPUT_ALIAS_UNMATCHED = "kna_verified_aliases.unmatched.json"

# 식물자원 API의 XML 응답 버전에 따라 표기가 달라도 읽을 수 있게 후보 키를 둡니다.
NAME_KEYS = (
    "plantGnrlNm",
    "plantKoreanNm",
    "plantNm",
    "gnrlNm",
    "korNm",
    "koreanNm",
)
SCIENTIFIC_NAME_KEYS = (
    "plantSpecsScnm",
    "plantScnm",
    "scientificNm",
    "scnm",
    "sciNm",
)
ID_KEYS = (
    "plantPilbkNo",
    "plantId",
    "plantSeq",
    "pilbkNo",
)
ALIAS_KEYS = (
    "notRcmmGnrlNm",
    "altName",
    "alias",
    "aliases",
    "notRcmmNm",
    "unrcmmNm",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="기존 식물 검색어를 국립수목원 공식 API로 재검증"
    )
    parser.add_argument("--collected", type=Path, default=DEFAULT_COLLECTED)
    parser.add_argument("--unmatched", type=Path, default=DEFAULT_UNMATCHED)
    parser.add_argument("--original-input", type=Path, default=DEFAULT_ORIGINAL_INPUT)
    parser.add_argument(
        "--review-names",
        type=Path,
        default=DEFAULT_REVIEW_NAMES,
        help="학명 검색용 matched/unmatched JSON",
    )
    parser.add_argument(
        "--aliases-file",
        type=Path,
        default=DEFAULT_VERIFIED_ALIASES,
        help="검증된 검색 별칭 JSON(verified 객체)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-name", default=OUTPUT_COLLECTED)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="기존 name_match 항목뿐 아니라 needs_review 항목도 API로 조회",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="API를 호출하지 않고 대상 수와 미수집 목록만 생성",
    )
    parser.add_argument(
        "--scientific-review",
        action="store_true",
        help="needs_review 검증 결과의 학명으로 검색하고 상세정보 수집",
    )
    parser.add_argument(
        "--verified-aliases",
        action="store_true",
        help="검증된 별칭의 검색명으로 정확 일치 검색하고 상세정보 수집",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


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
                raise ValueError(f"{path}:{line_number} JSON 객체가 아닙니다.")
            rows.append(value)
    return rows


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean(value)).casefold()
    return "".join(char for char in text if char.isalnum())


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def prepare_targets(
    collected_rows: list[dict[str, Any]], *, include_review: bool
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in collected_rows:
        name_match = row.get("name_match") is True
        needs_review = row.get("needs_review") is True
        if not name_match and not (include_review and needs_review):
            continue

        query_name = clean(row.get("query_name"))
        searched_term = clean(row.get("searched_term")) or query_name
        if not query_name or not searched_term:
            continue

        key = (query_name, searched_term)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "query_name": query_name,
                "searched_term": searched_term,
                "previous_result_name": clean(row.get("result_name")),
                "previous_scientific_name": clean(row.get("scientific_name")),
                "previous_plant_id": clean(row.get("plant_id")),
                "previous_name_match": name_match,
                "previous_needs_review": needs_review,
            }
        )
    return targets


def prepare_scientific_targets(review_names: dict[str, Any]) -> list[dict[str, Any]]:
    matched = review_names.get("matched")
    if not isinstance(matched, dict):
        raise ValueError("학명 검증 JSON의 matched가 객체가 아닙니다.")

    targets: list[dict[str, Any]] = []
    for query_name, scientific_name in matched.items():
        query_name = clean(query_name)
        scientific_name = clean(scientific_name)
        if not query_name or not scientific_name:
            raise ValueError("matched의 식물명과 학명은 빈 값일 수 없습니다.")
        targets.append(
            {
                "query_name": query_name,
                "searched_term": scientific_name,
                "search_type": "scientific_name",
            }
        )
    return targets


def prepare_verified_alias_targets(
    aliases_data: dict[str, Any],
) -> list[dict[str, Any]]:
    verified = aliases_data.get("verified")
    if not isinstance(verified, dict):
        raise ValueError("별칭 JSON의 verified가 객체가 아닙니다.")
    targets: list[dict[str, Any]] = []
    for query_name, searched_term in verified.items():
        query_name = clean(query_name)
        searched_term = clean(searched_term)
        if not query_name or not searched_term:
            raise ValueError("verified의 원본 이름과 검색명은 빈 값일 수 없습니다.")
        targets.append(
            {
                "query_name": query_name,
                "searched_term": searched_term,
                "search_type": "verified_alias",
            }
        )
    return targets


def prepare_scientific_unmatched(
    review_names: dict[str, Any],
    api_unmatched: Iterable[str] = (),
) -> dict[str, Any]:
    predefined = review_names.get("unmatched", [])
    if not isinstance(predefined, list):
        raise ValueError("학명 검증 JSON의 unmatched가 배열이 아닙니다.")
    combined = unique_strings([*predefined, *api_unmatched])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "web_unmatched": unique_strings(predefined),
        "api_unmatched": unique_strings(api_unmatched),
        "unmatched_count": len(combined),
        "unmatched": combined,
    }


def prepare_not_collected(
    original_names: list[str],
    collected_rows: list[dict[str, Any]],
    unmatched_names: list[str],
) -> dict[str, Any]:
    collected_queries = {
        normalize_name(row.get("query_name"))
        for row in collected_rows
        if clean(row.get("query_name"))
    }
    absent_from_collected = [
        name for name in original_names if normalize_name(name) not in collected_queries
    ]
    combined = unique_strings([*unmatched_names, *absent_from_collected])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "original_input_count": len(original_names),
        "collected_query_count": len(collected_queries),
        "previous_unmatched_count": len(unique_strings(unmatched_names)),
        "absent_from_collected_count": len(absent_from_collected),
        "not_collected_count": len(combined),
        "previous_unmatched": unique_strings(unmatched_names),
        "absent_from_collected": absent_from_collected,
        "not_collected": combined,
    }


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_to_dict(element: ET.Element) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for child in element:
        key = strip_namespace(child.tag)
        if list(child):
            value: Any = element_to_dict(child)
        else:
            value = clean(child.text)
        if key in result:
            current = result[key]
            result[key] = current + [value] if isinstance(current, list) else [current, value]
        else:
            result[key] = value
    return result


def first_value(raw: dict[str, Any], keys: Iterable[str]) -> str:
    lowered = {key.casefold(): value for key, value in raw.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if isinstance(value, list):
            value = value[0] if value else ""
        text = clean(value)
        if text:
            return text
    return ""


def parse_api_response(xml_text: str) -> tuple[list[dict[str, Any]], int]:
    root = ET.fromstring(xml_text)
    result_code = first_value(
        {strip_namespace(node.tag): node.text for node in root.iter()},
        ("resultCode", "returnReasonCode"),
    )
    result_message = first_value(
        {strip_namespace(node.tag): node.text for node in root.iter()},
        ("resultMsg", "returnAuthMsg"),
    )
    if result_code and result_code not in {"00", "0", "0000"}:
        raise RuntimeError(f"OpenAPI 오류 {result_code}: {result_message}")

    total_text = ""
    for node in root.iter():
        if strip_namespace(node.tag).casefold() == "totalcount":
            total_text = clean(node.text)
            break
    total_count = int(total_text) if total_text.isdigit() else 0

    item_nodes = [
        node for node in root.iter() if strip_namespace(node.tag).casefold() == "item"
    ]
    candidates: list[dict[str, Any]] = []
    for position, item in enumerate(item_nodes, start=1):
        raw = element_to_dict(item)
        candidates.append(
            {
                "result_position": position,
                "result_name": first_value(raw, NAME_KEYS),
                "scientific_name": first_value(raw, SCIENTIFIC_NAME_KEYS),
                "plant_id": first_value(raw, ID_KEYS),
                "aliases": first_value(raw, ALIAS_KEYS),
                "raw": raw,
            }
        )
    return candidates, total_count or len(candidates)


def parse_detail_response(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    flat_nodes = {strip_namespace(node.tag): node.text for node in root.iter()}
    result_code = first_value(flat_nodes, ("resultCode", "returnReasonCode"))
    result_message = first_value(flat_nodes, ("resultMsg", "returnAuthMsg"))
    if result_code and result_code not in {"00", "0", "0000"}:
        raise RuntimeError(f"OpenAPI 오류 {result_code}: {result_message}")

    item = next(
        (
            node
            for node in root.iter()
            if strip_namespace(node.tag).casefold() == "item"
        ),
        None,
    )
    if item is None:
        return {}
    return element_to_dict(item)


def request_xml(
    url: str,
    service_key: str,
    params: dict[str, Any],
    *,
    timeout: float,
) -> str:
    encoded_params = urlencode(params, quote_via=quote)
    encoded_service_key = quote(service_key, safe="%")
    request_url = f"{url}?serviceKey={encoded_service_key}&{encoded_params}"
    request = Request(
        request_url,
        headers={
            "User-Agent": "SKN3rd-KNA-PlantGrowth-API/1.0",
            "Accept": "application/xml,text/xml",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body[:300]}") from error
    except URLError as error:
        raise RuntimeError(f"API 연결 실패: {error.reason}") from error


def request_candidates(
    service_key: str,
    searched_term: str,
    *,
    timeout: float,
    num_of_rows: int = 1,
) -> tuple[list[dict[str, Any]], int]:
    payload = request_xml(
        SEARCH_URL,
        service_key,
        {
            "pageNo": 1,
            "numOfRows": num_of_rows,
            "reqSearchWrd": searched_term,
        },
        timeout=timeout,
    )
    return parse_api_response(payload)


def request_detail(
    service_key: str,
    plant_id: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    payload = request_xml(
        DETAIL_URL,
        service_key,
        {"reqPlantPilbkNo": plant_id},
        timeout=timeout,
    )
    return parse_detail_response(payload)


def load_completed_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    return {
        (clean(row.get("query_name")), clean(row.get("searched_term")))
        for row in read_jsonl(path)
    }


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def initialize_unified_output(
    output_dir: Path,
    output_name: str = OUTPUT_COLLECTED,
) -> Path:
    unified_path = output_dir / output_name
    legacy_path = output_dir / LEGACY_OUTPUT_CANDIDATES
    if (
        output_name == OUTPUT_COLLECTED
        and not unified_path.exists()
        and legacy_path.exists()
    ):
        # 잘못된 과거 검색 결과는 옮기지 않고 정확 일치 결과만 통합합니다.
        legacy_rows = [
            row
            for row in read_jsonl(legacy_path)
            if row.get("status") == "exact_match"
        ]
        write_jsonl(unified_path, legacy_rows)
    return unified_path


def main() -> None:
    args = parse_args()
    if args.scientific_review and args.verified_aliases:
        raise ValueError("--scientific-review와 --verified-aliases는 함께 쓸 수 없습니다.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.scientific_review:
        review_names = read_json(args.review_names)
        if not isinstance(review_names, dict):
            raise ValueError("학명 검증 JSON은 객체여야 합니다.")
        targets = prepare_scientific_targets(review_names)
        candidates_path = initialize_unified_output(
            args.output_dir,
            args.output_name,
        )
        not_collected_path = args.output_dir / OUTPUT_SCIENTIFIC_UNMATCHED
        not_collected = prepare_scientific_unmatched(review_names)
        not_collected_path.write_text(
            json.dumps(not_collected, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"학명 API 조회 대상: {len(targets)}건")
        print(f"웹 검색 미일치: {not_collected['unmatched_count']}건")
        print(f"미수집 목록: {not_collected_path}")
    elif args.verified_aliases:
        aliases_data = read_json(args.aliases_file)
        if not isinstance(aliases_data, dict):
            raise ValueError("별칭 JSON은 객체여야 합니다.")
        targets = prepare_verified_alias_targets(aliases_data)
        candidates_path = initialize_unified_output(
            args.output_dir,
            args.output_name,
        )
        not_collected_path = args.output_dir / OUTPUT_ALIAS_UNMATCHED
        print(f"검증 별칭 API 조회 대상: {len(targets)}건")
    else:
        collected_rows = read_jsonl(args.collected)
        original_names = unique_strings(read_json(args.original_input))
        unmatched_names = unique_strings(read_json(args.unmatched))
        targets = prepare_targets(collected_rows, include_review=args.include_review)
        candidates_path = initialize_unified_output(
            args.output_dir,
            args.output_name,
        )
        not_collected_path = args.output_dir / OUTPUT_NOT_COLLECTED
        not_collected = prepare_not_collected(
            original_names, collected_rows, unmatched_names
        )
        not_collected_path.write_text(
            json.dumps(not_collected, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"API 조회 대상: {len(targets)}건")
        print(f"기존 unmatched: {not_collected['previous_unmatched_count']}건")
        print(
            f"수집 파일에 없는 항목: "
            f"{not_collected['absent_from_collected_count']}건"
        )
        print(f"통합 미수집 목록: {not_collected['not_collected_count']}건")
        print(f"미수집 목록: {not_collected_path}")

    if args.prepare_only:
        print("prepare-only 완료: API는 호출하지 않았습니다.")
        return

    service_key = require_env("PUBLIC_DATA_PORTAL_API_KEY")
    completed = load_completed_keys(candidates_path) if args.resume else set()
    if not args.resume:
        target_names = {target["query_name"] for target in targets}
        preserved_rows = (
            [
                row
                for row in read_jsonl(candidates_path)
                if clean(row.get("query_name")) not in target_names
            ]
            if candidates_path.exists()
            else []
        )
        write_jsonl(candidates_path, preserved_rows)

    pending = [
        target
        for target in targets
        if (target["query_name"], target["searched_term"]) not in completed
    ]
    if args.limit is not None:
        pending = pending[: max(args.limit, 0)]

    api_unmatched_this_run: list[str] = []
    for index, target in enumerate(pending, start=1):
        candidates, total_count = request_candidates(
            service_key,
            target["searched_term"],
            timeout=args.timeout,
        )
        query_normalized = normalize_name(target["query_name"])
        searched_normalized = normalize_name(target["searched_term"])
        match_key = "scientific_name" if args.scientific_review else "result_name"
        exact_matches = [
            candidate
            for candidate in candidates
            if normalize_name(candidate[match_key]) == searched_normalized
        ]
        # 학명 검색에서는 원종보다 변종·품종이 첫 결과로 반환될 수 있다.
        # 첫 결과가 불일치할 때만 전체 후보를 확인하고 정확 일치 한 건만 저장한다.
        if (
            (args.scientific_review or args.verified_aliases)
            and not exact_matches
            and total_count > len(candidates)
        ):
            candidates, total_count = request_candidates(
                service_key,
                target["searched_term"],
                timeout=args.timeout,
                num_of_rows=total_count,
            )
            exact_matches = [
                candidate
                for candidate in candidates
                if normalize_name(candidate[match_key]) == searched_normalized
            ]
        exact_match = exact_matches[0] if exact_matches else None
        if exact_match is not None:
            exact_match["exact_query_name_match"] = (
                normalize_name(exact_match["result_name"]) == query_normalized
            )
            plant_id = clean(exact_match.get("plant_id"))
            if not plant_id:
                raise RuntimeError(
                    f"{target['searched_term']}: 정확 일치했지만 plant_id가 없습니다."
                )
            if args.delay > 0:
                time.sleep(args.delay)
            exact_match["detail"] = request_detail(
                service_key,
                plant_id,
                timeout=args.timeout,
            )

        output = {
            **target,
            "match_field": match_key,
            "status": "exact_match" if exact_match else "no_exact_match",
            "candidate_count": 1 if exact_match else 0,
            "api_total_count": total_count,
            "candidate": exact_match,
            "selected_plant_id": (
                exact_match.get("plant_id") if exact_match is not None else None
            ),
            "review_status": "pending" if exact_match else "no_exact_match",
            "source": "산림청 국립수목원 식물자원 조회 서비스",
            "source_url": API_SOURCE_URL,
            "license": "공공누리 제4유형(출처표시·상업적 이용금지·변경금지)",
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }
        if exact_match is not None:
            append_jsonl(candidates_path, output)
        else:
            api_unmatched_this_run.append(target["query_name"])
        print(
            f"[{index}/{len(pending)}] {target['query_name']} "
            f"({target['searched_term']}): "
            f"{'정확 일치 1건' if exact_match else '정확 일치 없음'}"
        )
        if index < len(pending) and args.delay > 0:
            time.sleep(args.delay)

    if args.scientific_review:
        scientific_unmatched = prepare_scientific_unmatched(
            review_names, api_unmatched_this_run
        )
        not_collected_path.write_text(
            json.dumps(scientific_unmatched, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"최종 미수집: {scientific_unmatched['unmatched_count']}건 "
            f"({not_collected_path})"
        )
    elif args.verified_aliases:
        alias_unmatched = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "unmatched_count": len(api_unmatched_this_run),
            "unmatched": unique_strings(api_unmatched_this_run),
        }
        not_collected_path.write_text(
            json.dumps(alias_unmatched, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"별칭 최종 미수집: {alias_unmatched['unmatched_count']}건 "
            f"({not_collected_path})"
        )
    print(f"후보 조회 완료: {candidates_path}")


if __name__ == "__main__":
    main()
