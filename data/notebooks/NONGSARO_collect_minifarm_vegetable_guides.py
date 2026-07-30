"""농사로 채소 목록에서 텍스트가 충분한 가정 재배 문서를 수집한다.

대상 목록
---------
https://www.nongsaro.go.kr/portal/ps/psz/psza/contentMain.ps
    ?menuId=PS03172&sSeCode=335001

모든 페이지의 모든 문서를 확인합니다. 제목에 따른 필터링은 하지 않으며,
정제된 본문의 텍스트 품질만으로 최종 저장 여부를 결정합니다.

본문 처리
---------
과수용 `NONGSARO_collect_minifarm_guides.py`와 동일한 로직을 사용합니다.

- `contsBox` 내부만 수집
- 조회수까지의 헤더 제외
- 콘텐츠 담당자 이후 영역 제외
- 첨부파일·공통 푸터 제외
- HTML 태그·주석·엔티티·불필요한 공백 정리

이미지 중심 문서 제외
---------------------
다음 중 하나에 해당하면 최종 JSONL에 저장하지 않습니다.

- 정제 본문이 300자 미만
- 이미지가 3개 이상이면서 이미지당 텍스트가 80자 미만

실행
----
python data/notebooks/NONGSARO_collect_minifarm_vegetable_guides.py

일부 검증
---------
python data/notebooks/NONGSARO_collect_minifarm_vegetable_guides.py --limit 3

기준 변경
---------
python data/notebooks/NONGSARO_collect_minifarm_vegetable_guides.py `
  --min-text-length 500 `
  --min-text-per-image 100

산출물
------
data/interim/nongsaro_minifarm/
    nongsaro_minifarm_vegetable_documents.jsonl
    vegetable_collection_summary.json

과수 결과 파일은 덮어쓰지 않으며 기존 RAG와 Supabase도 수정하지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from NONGSARO_collect_minifarm_guides import (
    BASE_URL,
    DATA_DIR,
    DETAIL_URL,
    RateLimitedClient,
    clean_body_html,
    collect_target_rows,
    extract_conts_box,
    parse_detail_metadata,
    write_jsonl,
)


MENU_ID = "PS03172"
SECTION_CODE = "335001"
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "nongsaro_minifarm"
DOCUMENTS_FILE = "nongsaro_minifarm_vegetable_documents.jsonl"
SUMMARY_FILE = "vegetable_collection_summary.json"

EXCLUDED_TITLES = {
    "텃밭작물 재배 캘린더(모종을 이용한 텃밭재배)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="농사로 채소 가정 재배 문서 수집"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-text-length", type=int, default=300)
    parser.add_argument("--min-image-count", type=int, default=3)
    parser.add_argument("--min-text-per-image", type=float, default=80.0)
    return parser.parse_args()


def compact_text_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def count_images(fragment: str) -> int:
    return len(re.findall(r"<img\b", fragment, flags=re.IGNORECASE))


def exclusion_reason(
    *,
    text_length: int,
    images: int,
    min_text_length: int,
    min_image_count: int,
    min_text_per_image: float,
) -> tuple[str, str] | None:
    if text_length < min_text_length:
        return (
            "short_text",
            f"정제 본문 {text_length}자: 최소 {min_text_length}자 미만",
        )

    text_per_image = text_length / images if images else float("inf")
    if images >= min_image_count and text_per_image < min_text_per_image:
        return (
            "image_heavy",
            (
                f"이미지 {images}개, "
                f"이미지당 텍스트 {text_per_image:.1f}자"
            ),
        )
    return None


def validate_args(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit는 1 이상이어야 합니다.")
    if args.min_text_length < 0:
        raise ValueError("--min-text-length는 0 이상이어야 합니다.")
    if args.min_image_count < 1:
        raise ValueError("--min-image-count는 1 이상이어야 합니다.")
    if args.min_text_per_image < 0:
        raise ValueError("--min-text-per-image는 0 이상이어야 합니다.")


def make_document(
    *,
    row: dict[str, Any],
    metadata: dict[str, str],
    body_text: str,
    text_length: int,
    images: int,
) -> dict[str, Any]:
    detail_params = {
        "menuId": MENU_ID,
        "sSeCode": SECTION_CODE,
        "cntntsNo": row["content_id"],
    }
    detail_url = f"{DETAIL_URL}?{urlencode(detail_params)}"
    return {
        "doc_id": f"nongsaro_minifarm_vegetable:{row['content_id']}",
        "source_id": f"nongsaro_content_{row['content_id']}",
        "content_id": row["content_id"],
        "title": metadata["detail_title"] or row["title"],
        "author": metadata["detail_author"] or row["author"],
        "registered_at": metadata["registered_at"],
        "text": body_text,
        "text_length": text_length,
        "image_count": images,
        "text_per_image": (
            round(text_length / images, 2) if images else None
        ),
        "url": detail_url,
        "source_url": detail_url,
        "publisher": "농촌진흥청 농사로",
        "category": "plant_growth",
        "source_key": "nongsaro_minifarm_vegetable",
        "menu_id": MENU_ID,
        "section_code": SECTION_CODE,
        "list_page": row["list_page"],
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "license": "농사로 이용 조건 준수",
    }


def main() -> None:
    args = parse_args()
    validate_args(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    client = RateLimitedClient(args.delay, args.timeout)
    target_rows, page_count = collect_target_rows(
        client,
        menu_id=MENU_ID,
        section_code=SECTION_CODE,
        filter_target_titles=False,
    )
    target_rows.sort(key=lambda row: int(row["list_number"]), reverse=True)
    if args.limit is not None:
        target_rows = target_rows[: args.limit]

    documents: list[dict[str, Any]] = []
    excluded = {"explicit_title": 0, "short_text": 0, "image_heavy": 0}

    for index, row in enumerate(target_rows, start=1):
        print(f"[{index}/{len(target_rows)}] {row['title']}")
        if row["title"] in EXCLUDED_TITLES:
            excluded["explicit_title"] += 1
            print("  제외: 명시적 제외 제목")
            continue
        page = client.get(
            DETAIL_URL,
            {
                "menuId": MENU_ID,
                "sSeCode": SECTION_CODE,
                "cntntsNo": row["content_id"],
            },
        )
        metadata = parse_detail_metadata(page)
        fragment = extract_conts_box(page)
        body_text = clean_body_html(fragment)
        text_length = compact_text_length(body_text)
        images = count_images(fragment)
        reason = exclusion_reason(
            text_length=text_length,
            images=images,
            min_text_length=args.min_text_length,
            min_image_count=args.min_image_count,
            min_text_per_image=args.min_text_per_image,
        )
        if reason:
            reason_key, message = reason
            excluded[reason_key] += 1
            print(f"  제외: {message}")
            continue

        documents.append(
            make_document(
                row=row,
                metadata=metadata,
                body_text=body_text,
                text_length=text_length,
                images=images,
            )
        )

    write_jsonl(args.output_dir / DOCUMENTS_FILE, documents)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": (
            f"{BASE_URL}/portal/ps/psz/psza/contentMain.ps?"
            + urlencode({"menuId": MENU_ID, "sSeCode": SECTION_CODE})
        ),
        "list_pages_visited": page_count,
        "listed_document_count": len(target_rows),
        "collected_document_count": len(documents),
        "excluded_explicit_title_count": excluded["explicit_title"],
        "excluded_short_text_count": excluded["short_text"],
        "excluded_image_heavy_count": excluded["image_heavy"],
        "filters": {
            "min_text_length": args.min_text_length,
            "min_image_count": args.min_image_count,
            "min_text_per_image": args.min_text_per_image,
        },
        "title_filter_applied": False,
        "output": str(args.output_dir / DOCUMENTS_FILE),
    }
    (args.output_dir / SUMMARY_FILE).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("수집 완료")
    print(f"- 목록 페이지: {page_count}")
    print(f"- 목록 문서: {len(target_rows)}")
    print(f"- 저장: {len(documents)}")
    print(f"- 명시적 제목 제외: {excluded['explicit_title']}")
    print(f"- 짧은 본문 제외: {excluded['short_text']}")
    print(f"- 이미지 중심 제외: {excluded['image_heavy']}")
    print(f"- 결과: {args.output_dir / DOCUMENTS_FILE}")


if __name__ == "__main__":
    main()
