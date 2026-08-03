"""농사로 과수 목록에서 가정 재배·텃밭가꾸기 문서를 수집한다.

**출처 표기 및 상업적 용도가 아니기에 사용 조건에 부합합니다**

수집 대상
---------
아래 농사로 과수 목록의 모든 페이지를 순회합니다.

https://www.nongsaro.go.kr/portal/ps/psz/psza/contentMain.ps
    ?menuId=PS03173&sSeCode=335002

다음 제목 패턴 중 하나에 해당하는 문서만 수집합니다.

- `~ 텃밭가꾸기`
- `가정에서 ~ 기르기`

예:
- 모과 텃밭가꾸기
- 사과를 이용한 텃밭가꾸기
- 가정에서 한라봉 기르기

본문 범위
---------
상세 페이지의 `contsBox` 영역만 수집합니다. 따라서 다음 영역은 포함하지
않습니다.

- `조회수`를 포함한 제목·등록일·작성자·조회수 영역까지의 앞부분
- `* 콘텐츠 담당자 : 국립원예특작과학원 ...`부터 시작하는 뒷부분
- 첨부파일, 만족도 조사, 공통 메뉴와 푸터

전처리
------
- script, style, HTML 주석 제거
- HTML 태그 제거
- `&nbsp;`, `&amp;` 등의 HTML 엔티티 변환
- 제어문자, 불필요한 공백과 빈 줄 정리

실행
----
python data/notebooks/NONGSARO_collect_minifarm_guides.py

일부 상세 문서만 검증
---------------------
python data/notebooks/NONGSARO_collect_minifarm_guides.py --limit 2

산출물
------
data/interim/nongsaro_minifarm/
    nongsaro_minifarm_documents.jsonl
    collection_summary.json

주의
----
- 원문 HTML과 이미지는 저장하지 않고 정제된 텍스트만 저장합니다.
- 기존 RAG 파일과 Supabase는 수정하지 않습니다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "nongsaro_minifarm"

BASE_URL = "https://www.nongsaro.go.kr"
LIST_URL = f"{BASE_URL}/portal/ps/psz/psza/contentMain.ps"
DETAIL_URL = f"{BASE_URL}/portal/ps/psz/psza/contentSub.ps"

MENU_ID = "PS03173"
SECTION_CODE = "335002"
PAGE_SIZE = 10
USER_AGENT = "SKN3rd-NongsaroMinifarmCollector/1.0"

DOCUMENTS_FILE = "nongsaro_minifarm_documents.jsonl"
SUMMARY_FILE = "collection_summary.json"

TITLE_PATTERNS = (
    re.compile(r"^.+\s+텃밭가꾸기$"),
    re.compile(r"^가정에서\s+.+\s+기르기$"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="농사로 가정 재배·텃밭가꾸기 문서 수집"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="HTTP 요청 사이 대기 시간(초, 기본 0.5)",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="목록 순회 후 수집할 상세 문서 수 제한",
    )
    return parser.parse_args()


def normalize_inline_text(value: str) -> str:
    value = html.unescape(value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip()


def matches_target_title(title: str) -> bool:
    title = normalize_inline_text(title)
    return any(pattern.fullmatch(title) for pattern in TITLE_PATTERNS)


class RateLimitedClient:
    def __init__(self, delay: float, timeout: float) -> None:
        if delay < 0:
            raise ValueError("--delay는 0 이상이어야 합니다.")
        self.delay = delay
        self.timeout = timeout
        self.last_request_at = 0.0

    def get(self, url: str, params: dict[str, Any]) -> str:
        elapsed = time.monotonic() - self.last_request_at
        if self.last_request_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        request_url = f"{url}?{urlencode(params)}"
        request = Request(
            request_url,
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


def parse_last_page(page: str) -> int:
    match = re.search(
        r'class="current_page"[^>]*>.*?/\s*<b>\s*(\d+)\s*</b>',
        page,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        return max(1, int(match.group(1)))

    script_match = re.search(
        r"lastPageNo\s*=\s*Number\(['\"](\d+)['\"]\)",
        page,
        flags=re.IGNORECASE,
    )
    return max(1, int(script_match.group(1))) if script_match else 1


def parse_list_rows(
    page: str,
    page_index: int,
    *,
    filter_target_titles: bool = True,
) -> list[dict[str, Any]]:
    row_pattern = re.compile(
        r"<tr>\s*"
        r'<td[^>]*class="bT_num"[^>]*>(.*?)</td>\s*'
        r'<td[^>]*class="bT_subject"[^>]*>\s*'
        r'<a[^>]*onclick="fncContentSub\(\'(\d+)\'\);return false;"[^>]*>'
        r"(.*?)</a>\s*</td>\s*"
        r'<td[^>]*class="bT_name"[^>]*>(.*?)</td>\s*'
        r'<td[^>]*class="bT_count"[^>]*>(.*?)</td>',
        flags=re.DOTALL | re.IGNORECASE,
    )

    rows: list[dict[str, Any]] = []
    for number, content_id, title, author, view_count in row_pattern.findall(page):
        title = normalize_inline_text(re.sub(r"<[^>]+>", "", title))
        if filter_target_titles and not matches_target_title(title):
            continue
        rows.append(
            {
                "list_number": normalize_inline_text(
                    re.sub(r"<[^>]+>", "", number)
                ),
                "content_id": content_id,
                "title": title,
                "author": normalize_inline_text(
                    re.sub(r"<[^>]+>", "", author)
                ),
                # 목록 검증용으로만 보관하며 상세 본문에는 포함하지 않는다.
                "view_count": normalize_inline_text(
                    re.sub(r"<[^>]+>", "", view_count)
                ),
                "list_page": page_index,
            }
        )
    return rows


class DivFragmentExtractor(HTMLParser):
    """지정한 class를 가진 div의 내부 HTML만 추출한다."""

    def __init__(self, target_class: str) -> None:
        super().__init__(convert_charrefs=False)
        self.target_class = target_class
        self.capturing = False
        self.depth = 0
        self.parts: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for key, value in attrs:
            if key.lower() == "class" and value:
                return set(value.split())
        return set()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if not self.capturing:
            if tag.lower() == "div" and self.target_class in self._classes(attrs):
                self.capturing = True
                self.depth = 1
            return

        if tag.lower() == "div":
            self.depth += 1
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self.capturing:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if not self.capturing:
            return
        if tag.lower() == "div":
            self.depth -= 1
            if self.depth == 0:
                self.capturing = False
                return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.capturing:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.capturing:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.capturing:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        # HTML 주석은 의도적으로 수집하지 않는다.
        return

    @property
    def fragment(self) -> str:
        return "".join(self.parts)


def extract_conts_box(page: str) -> str:
    parser = DivFragmentExtractor("contsBox")
    parser.feed(page)
    parser.close()
    if not parser.fragment.strip():
        raise ValueError("상세 페이지에서 contsBox 본문을 찾지 못했습니다.")
    return parser.fragment


def clean_plain_text_artifacts(text: str) -> str:
    """이미지 잔여값과 분류용 불릿만 제거하고 단위 기호는 보존한다."""
    # 깨진 HTML에서 속성이 평문으로 남는 경우까지 제거한다.
    text = re.sub(
        r"""(?ix)
        \b(?:data-)?src\s*=\s*
        (?:
            "[^"]*"
            |'[^']*'
            |[^\s>]+
        )
        """,
        " ",
        text,
    )
    text = re.sub(
        r"(?i)\bhttps?://[^\s<>\"]+?\."
        r"(?:jpg|jpeg|png|gif|webp|svg|bmp)"
        r"(?:\?[^\s<>\"]*)?",
        " ",
        text,
    )
    text = re.sub(
        r"(?i)\b[^\s<>\"']+\.(?:jpg|jpeg|png|gif|webp|svg|bmp)\b",
        " ",
        text,
    )

    # 문장 분류용 기호만 제거한다. ℃, °C, %, ㎡, ㎏ 등 단위는 건드리지 않는다.
    text = re.sub(r"[•●○▶▷■□▪◦◆◇★☆]+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean_body_html(fragment: str) -> str:
    # 실행 코드와 화면 스타일은 텍스트 변환 전에 제거한다.
    fragment = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        fragment,
        flags=re.DOTALL | re.IGNORECASE,
    )
    fragment = re.sub(r"<!--.*?-->", "", fragment, flags=re.DOTALL)
    fragment = re.sub(
        r"<(?:img|source)\b[^>]*>",
        "",
        fragment,
        flags=re.IGNORECASE,
    )
    fragment = re.sub(
        r"<picture\b[^>]*>.*?</picture>",
        "",
        fragment,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 문단 성격의 태그는 줄바꿈으로, 나머지는 공백으로 치환한다.
    fragment = re.sub(
        r"<br\s*/?>|</(?:p|li|ul|ol|h[1-6]|table|tr|div|section)>",
        "\n",
        fragment,
        flags=re.IGNORECASE,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(fragment)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = "".join(
        character
        for character in text
        if character in "\n\t" or ord(character) >= 32
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # 상세 구조가 바뀌더라도 요청한 뒷부분이 들어오지 않도록 한 번 더 방어한다.
    text = re.split(
        r"\*?\s*콘텐츠\s*담당자\s*:\s*국립원예특작과학원",
        text,
        maxsplit=1,
    )[0].strip()
    return clean_plain_text_artifacts(text)


def parse_detail_metadata(page: str) -> dict[str, str]:
    def find(pattern: str) -> str:
        match = re.search(pattern, page, flags=re.DOTALL | re.IGNORECASE)
        return (
            normalize_inline_text(re.sub(r"<[^>]+>", "", match.group(1)))
            if match
            else ""
        )

    return {
        "detail_title": find(
            r'<h3[^>]*class="[^"]*vTitle[^"]*"[^>]*>\s*'
            r"<strong>(.*?)</strong>"
        ),
        "registered_at": find(
            r"<li>\s*<span>\s*등록일\s*</span>\s*(.*?)</li>"
        ),
        "detail_author": find(
            r"<li>\s*<span>\s*작성자\s*</span>\s*(.*?)</li>"
        ),
    }


def collect_target_rows(
    client: RateLimitedClient,
    *,
    menu_id: str = MENU_ID,
    section_code: str = SECTION_CODE,
    filter_target_titles: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    first_page = client.get(
        LIST_URL,
        {
            "menuId": menu_id,
            "sSeCode": section_code,
            "pageIndex": 1,
            "pageSize": PAGE_SIZE,
        },
    )
    last_page = parse_last_page(first_page)
    rows = parse_list_rows(
        first_page,
        1,
        filter_target_titles=filter_target_titles,
    )

    for page_index in range(2, last_page + 1):
        page = client.get(
            LIST_URL,
            {
                "menuId": menu_id,
                "sSeCode": section_code,
                "pageIndex": page_index,
                "pageSize": PAGE_SIZE,
            },
        )
        rows.extend(
            parse_list_rows(
                page,
                page_index,
                filter_target_titles=filter_target_titles,
            )
        )

    unique = {row["content_id"]: row for row in rows}
    return list(unique.values()), last_page


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit는 1 이상이어야 합니다.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = RateLimitedClient(args.delay, args.timeout)
    target_rows, page_count = collect_target_rows(client)
    target_rows.sort(key=lambda row: int(row["list_number"]), reverse=True)
    if args.limit is not None:
        target_rows = target_rows[: args.limit]

    documents: list[dict[str, Any]] = []
    for index, row in enumerate(target_rows, start=1):
        print(f"[{index}/{len(target_rows)}] {row['title']}")
        detail_url = (
            f"{DETAIL_URL}?"
            + urlencode(
                {
                    "menuId": MENU_ID,
                    "sSeCode": SECTION_CODE,
                    "cntntsNo": row["content_id"],
                }
            )
        )
        page = client.get(
            DETAIL_URL,
            {
                "menuId": MENU_ID,
                "sSeCode": SECTION_CODE,
                "cntntsNo": row["content_id"],
            },
        )
        metadata = parse_detail_metadata(page)
        body_text = clean_body_html(extract_conts_box(page))
        if not body_text:
            raise ValueError(f"정제 후 본문이 비었습니다: {row['content_id']}")

        documents.append(
            {
                "doc_id": f"nongsaro_minifarm:{row['content_id']}",
                "source_id": f"nongsaro_content_{row['content_id']}",
                "content_id": row["content_id"],
                "title": metadata["detail_title"] or row["title"],
                "author": metadata["detail_author"] or row["author"],
                "registered_at": metadata["registered_at"],
                "text": body_text,
                "url": detail_url,
                "source_url": detail_url,
                "publisher": "농촌진흥청 농사로",
                "category": "plant_growth",
                "source_key": "nongsaro_minifarm",
                "menu_id": MENU_ID,
                "section_code": SECTION_CODE,
                "list_page": row["list_page"],
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "license": "농사로 이용 조건 준수",
            }
        )

    write_jsonl(args.output_dir / DOCUMENTS_FILE, documents)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "list_pages_visited": page_count,
        "matched_title_count": len(target_rows),
        "collected_document_count": len(documents),
        "title_patterns": ["~ 텃밭가꾸기", "가정에서 ~ 기르기"],
        "excluded_sections": [
            "조회수까지의 상세 페이지 헤더",
            "콘텐츠 담당자 이후 영역",
            "첨부파일 및 공통 푸터",
        ],
        "output": str(args.output_dir / DOCUMENTS_FILE),
    }
    (args.output_dir / SUMMARY_FILE).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("수집 완료")
    print(f"- 목록 페이지: {page_count}")
    print(f"- 수집 문서: {len(documents)}")
    print(f"- 결과: {args.output_dir / DOCUMENTS_FILE}")


if __name__ == "__main__":
    main()
