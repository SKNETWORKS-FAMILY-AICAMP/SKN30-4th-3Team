"""국가생물종지식정보시스템에서 식물 검색 결과와 상세 본문을 수집한다.

용도
----
`re_search_plant_grow_list.json`의 식물명을 하나씩 검색합니다. 검색 결과가
있으면 첫 번째 항목의 상세 페이지 본문을 수집하고, 검색 결과가 전혀 없는
이름은 별도 JSON에 보존합니다.

실행
----
python data/notebooks/NATURE_search_plant_grow.py

일부만 확인
------------
python data/notebooks/NATURE_search_plant_grow.py --limit 3

중단 후 이어서 실행
-------------------
python data/notebooks/NATURE_search_plant_grow.py --resume

특정 식물부터 다시 실행
-----------------------
python data/notebooks/NATURE_search_plant_grow.py --resume --start-from 모과

입력
----
data/notebooks/re_search_plant_grow_list.json

산출물
------
data/interim/coverage_plant_grow_nature/
    plant_growth_collected.jsonl
    unmatched.json
    collection_summary.json

수집 필드
---------
- 검색명, 실제 검색어, 검색 결과명, 비추천명
- 학명, 과명
- 분포, 형태, 특징
- 생육환경, 생육형, 번식방법, 재배특성
- 상세 페이지의 전체 텍스트 본문
- 상세 URL과 수집 시각

주의
----
- 사이트 robots.txt의 일반 접근 정책에 맞춰 요청 간격은 최소 10초입니다.
- 검색 요청과 상세 본문 요청 사이에도 10초 간격을 적용합니다.
- 검색 결과 첫 번째 항목이 원하는 식물과 다를 수 있으므로 `name_match`와
  `needs_review`를 반드시 확인해야 합니다.
- 기존 커버리지 MD와 Supabase는 수정하지 않습니다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = DATA_DIR / "notebooks" / "re_search_plant_grow_list.json"
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "coverage_plant_grow_nature"

BASE_URL = "https://nature.go.kr"
SEARCH_URL = f"{BASE_URL}/kbi/plant/pilbk/selectPlantPilbkGnrlList.do"
DETAIL_URL = f"{BASE_URL}/kbi/plant/pilbk/selectPlantPilbkDtl.do"
ROBOTS_URL = f"{BASE_URL}/robots.txt"

MIN_DELAY_SECONDS = 10.0
USER_AGENT = "SKN3rd-PlantGrowthCollector/1.0"

COLLECTED_FILE = "plant_growth_collected.jsonl"
UNMATCHED_FILE = "unmatched.json"
SUMMARY_FILE = "collection_summary.json"

DETAIL_FIELDS = (
    "분포",
    "형태",
    "특징",
    "생육환경",
    "생육형",
    "번식방법",
    "재배특성",
)

# 농산물명과 식물도감 정식명의 관계가 명확한 경우에는 도감명을 먼저 검색한다.
SEARCH_ALIASES: dict[str, list[str]] = {
    "감": ["감나무"],
    "대추": ["대추나무"],
    "모과": ["모과나무"],
    "밤": ["밤나무"],
    "비파": ["비파나무"],
    "사과": ["사과나무"],
    "석류": ["석류나무"],
    "앵두": ["앵두나무"],
    "오갈피": ["오갈피나무", "오가피나무"],
    "초피": ["초피나무"],
    "호두": ["호두나무"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="국가생물종지식정보시스템 식물 상세 본문 수집"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--delay", type=float, default=MIN_DELAY_SECONDS)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--start-from",
        type=str,
        default=None,
        help="입력 목록에서 지정한 식물부터 끝까지 강제로 다시 실행합니다.",
    )
    return parser.parse_args()


def clean_text(value: str, *, keep_lines: bool = False) -> str:
    separator = "\n" if keep_lines else " "
    value = re.sub(r"<br\s*/?>", separator, value, flags=re.IGNORECASE)
    value = re.sub(
        r"</(?:p|li|h[1-6]|div|section|article)>",
        separator,
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    if keep_lines:
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\s*\n\s*", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()
    return re.sub(r"\s+", " ", value).strip()


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean_text(value))
    return re.sub(r"[\s,./_\-()]+", "", value).casefold()


def build_search_terms(name: str) -> list[str]:
    # 확실한 도감 별칭을 원래 이름보다 우선한다.
    terms = list(SEARCH_ALIASES.get(name, []))
    terms.append(name)

    bracket = re.fullmatch(r"(.+?)\((.+)\)", name)
    if bracket:
        terms.append(bracket.group(1).strip())
        terms.extend(
            part.strip()
            for part in re.split(r"[,/]", bracket.group(2))
            if part.strip()
        )
    if " " in name:
        terms.append(name.replace(" ", ""))
    return list(dict.fromkeys(term for term in terms if term))


def read_input(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("입력 JSON은 식물명 문자열 배열이어야 합니다.")
    return list(dict.fromkeys(item.strip() for item in value))


class RateLimitedClient:
    def __init__(self, delay: float, timeout: float) -> None:
        self.delay = max(delay, MIN_DELAY_SECONDS)
        self.timeout = timeout
        self.last_request_at = 0.0

    def get(self, url: str, params: dict[str, Any] | None = None) -> str:
        elapsed = time.monotonic() - self.last_request_at
        if self.last_request_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        if params:
            # robots.txt에서 제한하는 pageIndex는 사용하지 않는다.
            url = f"{url}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        finally:
            self.last_request_at = time.monotonic()


def verify_robots(client: RateLimitedClient) -> None:
    robots = client.get(ROBOTS_URL)
    if "User-agent: *" not in robots or "Crawl-delay: 10" not in robots:
        raise RuntimeError(
            "사이트 robots.txt 정책이 예상과 다릅니다. 실행을 중단합니다."
        )


def parse_search_results(page: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'<div\s+class="pointer"\s+onclick="fn_detailInfo\(\'(\d+)\'\);">'
        r".*?<span[^>]*title=\"식물도감 상세보기\"[^>]*>(.*?)</span>"
        r"(.*?)</p>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    results: dict[str, dict[str, Any]] = {}
    for plant_id, raw_name, remainder in pattern.findall(page):
        result_name = clean_text(raw_name)
        alias_match = re.search(
            r"\[\s*비추천명\s*:\s*(.*?)\]",
            clean_text(remainder),
            flags=re.DOTALL,
        )
        aliases = (
            [
                value.strip()
                for value in alias_match.group(1).split(",")
                if value.strip()
            ]
            if alias_match
            else []
        )
        results[plant_id] = {
            "plant_id": plant_id,
            "result_name": result_name,
            "aliases": aliases,
            "url": f"{DETAIL_URL}?plantPilbkNo={plant_id}",
        }
    return list(results.values())


def parse_basic_value(page: str, label: str) -> str:
    pattern = re.compile(
        rf"<p>\s*{re.escape(label)}\s*</p>(.*?)(?:</li>)",
        flags=re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(page)
    if not match:
        return ""
    block = match.group(1)
    data_name = re.search(r'data-name="([^"]+)"', block)
    return clean_text(data_name.group(1) if data_name else block)


def parse_detail_field(page: str, label: str) -> str:
    pattern = re.compile(
        rf'<h4[^>]*class="[^"]*sub_bullet[^"]*"[^>]*>\s*'
        rf"{re.escape(label)}\s*</h4>\s*"
        r'<p[^>]*class="[^"]*txt[^"]*"[^>]*>(.*?)</p>',
        flags=re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(page)
    return clean_text(match.group(1), keep_lines=True) if match else ""


def parse_detail_body(page: str) -> str:
    # 본문 영역을 우선 사용하고 구조 변경 시 전체 body 텍스트로 대체한다.
    candidates = [
        r'<div[^>]+class="[^"]*content_wrap[^"]*"[^>]*>(.*?)</div>\s*</div>',
        r'<div[^>]+id="content"[^>]*>(.*?)</div>\s*</div>',
        r"<body[^>]*>(.*?)</body>",
    ]
    for pattern in candidates:
        match = re.search(pattern, page, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = clean_text(match.group(1), keep_lines=True)
            if text:
                return text
    return ""


def name_match(
    query_name: str,
    searched_term: str,
    result_name: str,
    aliases: list[str],
) -> bool:
    targets = {
        normalize_name(query_name),
        normalize_name(searched_term),
    }
    return (
        normalize_name(result_name) in targets
        or any(normalize_name(alias) in targets for alias in aliases)
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def upsert(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    rows[:] = [
        item for item in rows if item.get("query_name") != row["query_name"]
    ]
    rows.append(row)
    rows.sort(key=lambda item: str(item.get("query_name", "")))


def processed_names(output_dir: Path) -> set[str]:
    names = {
        str(row["query_name"])
        for row in read_jsonl(output_dir / COLLECTED_FILE)
        if row.get("query_name")
    }
    unmatched_path = output_dir / UNMATCHED_FILE
    if unmatched_path.exists():
        names.update(
            str(item)
            for item in json.loads(
                unmatched_path.read_text(encoding="utf-8-sig")
            )
        )
    return names


def save_outputs(
    output_dir: Path,
    *,
    input_count: int,
    attempted: int,
    collected: list[dict[str, Any]],
    unmatched: list[str],
) -> None:
    write_jsonl(output_dir / COLLECTED_FILE, collected)
    unmatched = sorted(set(unmatched))
    (output_dir / UNMATCHED_FILE).write_text(
        json.dumps(unmatched, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review_count = sum(bool(row.get("needs_review")) for row in collected)
    with_growth_count = sum(
        any(
            row.get(field)
            for field in (
                "growth_environment",
                "growth_form",
                "propagation_method",
                "cultivation_characteristics",
            )
        )
        for row in collected
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_count": input_count,
        "attempted_this_run": attempted,
        "collected_count": len(collected),
        "with_growth_fields_count": with_growth_count,
        "needs_review_count": review_count,
        "unmatched_count": len(unmatched),
        "coverage_md_modified": False,
        "supabase_modified": False,
    }
    (output_dir / SUMMARY_FILE).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    if args.delay < MIN_DELAY_SECONDS:
        raise ValueError("--delay는 사이트 정책에 따라 10초 이상이어야 합니다.")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit는 1 이상이어야 합니다.")

    names = read_input(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    collected = (
        read_jsonl(args.output_dir / COLLECTED_FILE) if args.resume else []
    )
    unmatched_path = args.output_dir / UNMATCHED_FILE
    unmatched = (
        json.loads(unmatched_path.read_text(encoding="utf-8-sig"))
        if args.resume and unmatched_path.exists()
        else []
    )

    pending = names
    if args.start_from:
        if not args.resume:
            raise ValueError("--start-from은 기존 결과 보존을 위해 --resume과 함께 사용하세요.")
        if args.start_from not in names:
            raise ValueError(f"입력 목록에 없는 식물명입니다: {args.start_from}")
        start_index = names.index(args.start_from)
        pending = names[start_index:]
    elif args.resume:
        done = processed_names(args.output_dir)
        pending = [name for name in names if name not in done]
    if args.limit is not None:
        pending = pending[: args.limit]

    client = RateLimitedClient(args.delay, args.timeout)
    verify_robots(client)
    attempted = 0

    for index, query_name in enumerate(pending, start=1):
        attempted += 1
        print(f"[{index}/{len(pending)}] {query_name}")
        selected: dict[str, Any] | None = None
        searched_term = ""
        error = ""

        try:
            # 첫 번째로 결과가 반환되는 검색어의 첫 번째 항목을 선택한다.
            for term in build_search_terms(query_name):
                page = client.get(
                    SEARCH_URL,
                    {
                        "pageUnit": 100,
                        "searchCnd": 0,
                        "searchWrd": term,
                    },
                )
                results = parse_search_results(page)
                if results:
                    selected = results[0]
                    searched_term = term
                    break

            if selected is not None:
                detail_page = client.get(
                    DETAIL_URL,
                    {"plantPilbkNo": selected["plant_id"]},
                )
                fields = {
                    label: parse_detail_field(detail_page, label)
                    for label in DETAIL_FIELDS
                }
                matched = name_match(
                    query_name,
                    searched_term,
                    selected["result_name"],
                    selected["aliases"],
                )
                upsert(
                    collected,
                    {
                        "query_name": query_name,
                        "searched_term": searched_term,
                        "result_position": 1,
                        "result_name": (
                            parse_basic_value(detail_page, "식물명")
                            or selected["result_name"]
                        ),
                        "aliases": selected["aliases"],
                        "scientific_name": parse_basic_value(
                            detail_page, "학명"
                        ),
                        "family_name": parse_basic_value(detail_page, "과명"),
                        "distribution": fields["분포"],
                        "form": fields["형태"],
                        "characteristics": fields["특징"],
                        "growth_environment": fields["생육환경"],
                        "growth_form": fields["생육형"],
                        "propagation_method": fields["번식방법"],
                        "cultivation_characteristics": fields["재배특성"],
                        "body_text": parse_detail_body(detail_page),
                        "name_match": matched,
                        "needs_review": not matched,
                        "plant_id": selected["plant_id"],
                        "url": selected["url"],
                        "publisher": "산림청 국립수목원",
                        "source": "국가생물종지식정보시스템 식물도감",
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                        "license": "국가생물종지식정보시스템 이용 조건 확인 필요",
                    },
                )
                unmatched = [
                    name for name in unmatched if name != query_name
                ]
            elif query_name not in unmatched:
                unmatched.append(query_name)
        except (HTTPError, URLError, TimeoutError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            if query_name not in unmatched:
                unmatched.append(query_name)

        if error:
            print(f"  요청 실패: {error}")
        save_outputs(
            args.output_dir,
            input_count=len(names),
            attempted=attempted,
            collected=collected,
            unmatched=unmatched,
        )

    print("수집 완료")
    print(f"- 입력: {len(names)}종")
    print(f"- 이번 실행: {attempted}종")
    print(f"- 본문 수집: {len(collected)}종")
    print(
        "- 검토 필요: "
        f"{sum(bool(row.get('needs_review')) for row in collected)}종"
    )
    print(f"- 검색 결과 없음: {len(set(unmatched))}종")
    print(f"- 결과 폴더: {args.output_dir}")


if __name__ == "__main__":
    main()
