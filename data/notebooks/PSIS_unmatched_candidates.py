"""

NCPMS 병/해충 후보 + Supabase 적재된 식물들 중
PSIS 검색 결과가 없는 식물 목록을 unmatched_alias_candidates.jsonl에 저장합니다.

이후 해당 jsonl의 candidates에 후보군을 저장하여, 다른 이름으로 식물을 검색합니다.
검색 결과가 있는 식물은 jsonl에서 자동으로 삭제됩니다

반드시 --search를 이용하여 실행해야 .jsonl에 수정한 사항이 반영됩니다.

실행 방법:

    python data/notebooks/PSIS_unmatched_candidates.py `
  --search `
  --page-size 99 `
  --delay 0
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PSIS_from_NCPMS import (
    CROP_PATH,
    DEFAULT_ENDPOINT,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SERVICE_TYPE,
    LIST_SERVICE,
    LIST_PATH,
    NCPMS_MAPPING_PATH,
    RELATION_PATH,
    PsisClient,
    build_crop_outputs,
    dedupe,
    extract_list_items,
    find_first,
    load_env,
    normalize_text,
    normalized_list_row,
    normalized_name,
    now_iso,
    read_jsonl,
)


DATA_DIR = Path(__file__).resolve().parents[1]
NOTEBOOKS_DIR = Path(__file__).resolve().parent
DEFAULT_ALL_PSIS_DIR = DATA_DIR / "interim" / "all_PSIS"
DEFAULT_QUEUE_NAME = "unmatched_alias_candidates.jsonl"
DEFAULT_RESULTS_NAME = "unmatched_alias_search_results.jsonl"
TARGETS_NAME = "ncpms_target_plants.jsonl"
MAPPINGS_NAME = "ncpms_psis_crop_mapping.jsonl"

# One deliberately simple search suggestion per currently known unmatched
# plant. These are discovery candidates, not approved crop equivalences.
# A PSIS hit must still be reviewed before it is promoted to search_aliases in
# the main collector, especially for broader crop groups such as 감귤 or 고추.
AUTO_CANDIDATES: dict[str, str] = {
    # Conservative spelling variants or well-established names for the same
    # plant. Broader crop groups, related species, plant parts, and ambiguous
    # commercial names are intentionally excluded.
    "갯기름 나물(식방풍)": "개기름나물",
    "곰취": "웅소",
    "과꽃": "차이나아스터",
    "관음죽": "관음죽나무",
    "군자란": "클리비아",
    "금어초": "스냅드래곤",
    "금잔화": "카렌듈라",
    "기타관엽식물류": "기타관엽식물",
    "다알리아": "달리아",
    "동양심비": "동양심비디움",
    "들깨": "잎들깨",
    "디펜바키아": "디펜바히아",
    "메리골드": "메리골드꽃",
    "면화": "면화나무",
    "모란(목단)": "모란(목단피)",
    "목화": "목화나무",
    "몬스테라": "전신란",
    "문주란": "문주화",
    "백일홍": "지니아",
    "봉숭아(봉선화)": "봉선화(봉숭아)",
    "붓꽃": "붓꽃류",
    "산마늘": "명이나물",
    "삽주": "백출",
    "샐러리": "셀러리",
    "샐비어": "사루비아",
    "석류나무": "석류",
    "소철": "소철나무",
    "쉐프렐라": "쉐플레라 홍콩",
    "스토크": "비단향꽃무",
    "시써스": "시서스",
    "아나나스": "아나나스류",
    "아레카야자": "황야자",
    "아마릴리스": "아마릴리스꽃",
    "아스파라가스": "아스파라거스",
    "아이비(헤데라)": "서양송악",
    "안개꽃": "안개초",
    "유채": "채종유채",
    "은행나무": "은행",
    "접시꽃": "촉규화",
    "제라늄": "제라늄꽃",
    "종려죽": "종려죽나무",
    "쥐오줌풀(길초근)": "길초",
    "진달래": "참꽃",
    "참나물": "참나물류",
    "참당귀(당귀)": "참당귀",
    "천일홍": "천일초",
    "초피": "초피나무",
    "칸나": "홍초",
    "켄차야자": "켄티아야자",
    "클레로덴드럼": "클레로덴드론",
    "택사": "택사풀",
    "튜울립": "튤립",
    "파슬리": "파세리",
    "팔손이": "팔손이나무",
    "패랭이꽃": "석죽",
    "팬지": "팬지꽃",
    "페튜니아": "페추니아",
    "피닉스야자": "피닉스팜",
    "하수오": "적하수오",
    "협죽도": "유도화",
    "호두나무": "호두",
    "황금": "속썩은풀",
    "가울테리아": "파스향나무",
    "개운죽": "드라세나산데리아나",
    "결구상추": "양상추",
    "골드크레스트": "율마",
    "공작야자": "미티스야자",
    "구문초": "모기풀",
    "구즈마니아": "구즈매니아",
    "금전수": "자미오쿨카스",
    "꽃도라지": "리시안셔스",
    "꽃해바라기": "해바라기",
    "꽈리고추": "고추",
    "덴드로비움": "덴드로비움란",
    "두릅": "두릅나무",
    "두충": "두충나무",
    "베고니아": "베고니아류",
    "벤자민고무나무": "벤자민",
    "벵갈고무나무": "뱅갈고무나무",
    "보스톤고사리": "보스턴고사리",
    "부레옥잠": "워터히아신스",
    "부지화": "한라봉",
    "뿌리치커리": "치커리",
    "산세베리아": "산세비에리아",
    "수박페페로미아": "수박페페",
    "스킨답서스": "포토스",
    "스투키": "산세베리아 스투키",
    "시클라멘": "시크라멘",
    "싱고니움": "싱고늄",
    "아이리스": "아이리스꽃",
    "아프리칸바이올렛": "세인트폴리아",
    "안스리움": "안투리움",
    "양앵두": "양앵두(체리)",
    "양유": "더덕",
    "연근": "연",
    "오렌지쟈스민": "오렌지자스민",
    "온시디움": "온시디움란",
    "올리브나무": "올리브",
    "인도고무나무": "러버플랜트",
    "잎치커리": "치커리",
    "참취": "참취나물",
    "천마": "천마초",
    "치콘": "치커리",
    "카라": "칼라꽃",
    "칼라": "카라꽃",
    "칼라데아마코야나": "공작칼라데아",
    "테이블야자": "테이블팜",
    "파키라": "파키라나무",
    "팽이버섯": "팽이버섯류",
    "프리지아": "후리지아",
    "필로덴드론콩고": "콩고필로덴드론",
    "한라봉": "부지화",
    "호야": "왁스플랜트",
    "호접란": "팔레놉시스란",
    "홍콩야자": "쉐플레라 홍콩",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a compact unmatched-plant alias queue and search every "
            "candidate name in that queue against PSIS SVC01."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--build",
        action="store_true",
        help="Create/update the editable queue from unmatched target plants.",
    )
    action.add_argument(
        "--search",
        action="store_true",
        help="Search all candidate names currently present in the queue.",
    )
    action.add_argument(
        "--list",
        action="store_true",
        help="Show queue and result counts without API calls.",
    )
    action.add_argument(
        "--collect-hits",
        action="store_true",
        help=(
            "Re-query recorded hits, merge their SVC01 rows into all_PSIS, "
            "and rebuild crops.jsonl and crop_pesticide_relations.jsonl."
        ),
    )
    action.add_argument(
        "--export-sample",
        action="store_true",
        help=(
            "Restore every attempted candidate, including resolved hits, "
            "into a read-only sample JSONL for reproducibility."
        ),
    )
    parser.add_argument(
        "--all-psis-dir",
        type=Path,
        default=DEFAULT_ALL_PSIS_DIR,
        help="Directory containing all_PSIS outputs.",
    )
    parser.add_argument(
        "--queue-file",
        type=Path,
        help="Editable JSONL. Defaults inside --all-psis-dir.",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        help="Search history JSONL. Defaults inside --all-psis-dir.",
    )
    parser.add_argument(
        "--sample-file",
        type=Path,
        default=NOTEBOOKS_DIR / "unmatched_alias_candidates_sample.jsonl",
        help="Output path used by --export-sample.",
    )
    parser.add_argument("--api-key", default="")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--service-type", default=DEFAULT_SERVICE_TYPE)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=100_000)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    queue_path = args.queue_file or NOTEBOOKS_DIR / DEFAULT_QUEUE_NAME
    results_path = args.results_file or args.all_psis_dir / DEFAULT_RESULTS_NAME
    return queue_path, results_path


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Checkpoint JSONL without exposing a partly written queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp_path.replace(path)


def clean_candidates(value: Any, original_name: str) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    original_key = normalized_name(original_name)
    for item in value:
        name = normalize_text(item)
        key = normalized_name(name)
        if not name or not key or key == original_key or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def compact_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    name = normalize_text(row.get("name") or row.get("ncpms_crop_name"))
    key = normalize_text(row.get("key") or row.get("ncpms_plant_key"))
    origin = normalize_text(row.get("origin") or row.get("target_origin"))
    if not origin:
        origin = "supabase_supplement" if key.startswith("supabase:") else "ncpms"
    return {
        "key": key,
        "name": name,
        "origin": origin,
        "candidates": clean_candidates(row.get("candidates"), name),
    }


def build_queue(
    all_psis_dir: Path,
    queue_path: Path,
    results_path: Path,
) -> int:
    targets_path = all_psis_dir / TARGETS_NAME
    mappings_path = all_psis_dir / MAPPINGS_NAME
    if not targets_path.exists():
        raise FileNotFoundError(f"Target file is missing: {targets_path}")
    if not mappings_path.exists():
        raise FileNotFoundError(f"Mapping file is missing: {mappings_path}")

    targets = read_jsonl(targets_path)
    mappings = read_jsonl(mappings_path)
    verified_keys = {
        normalize_text(row.get("ncpms_plant_key"))
        for row in mappings
        if row.get("match_status") == "verified"
    }
    # A candidate hit remains out of the queue even before it is promoted into
    # the main collector's alias configuration.
    hit_keys = {
        normalize_text(row.get("target_key"))
        for row in read_jsonl(results_path)
        if row.get("status") == "hit"
    }
    existing = {
        row["key"]: row
        for raw in read_jsonl(queue_path)
        if (row := compact_queue_row(raw)).get("key")
    }

    queue: list[dict[str, Any]] = []
    for target in targets:
        key = normalize_text(target.get("ncpms_plant_key"))
        if not key or key in verified_keys or key in hit_keys:
            continue
        name = normalize_text(target.get("ncpms_crop_name"))
        prior = existing.get(key, {})
        prior_candidates = clean_candidates(prior.get("candidates"), name)
        if key in existing:
            candidates = prior_candidates
        else:
            suggested = normalize_text(AUTO_CANDIDATES.get(name))
            candidates = clean_candidates([suggested], name)
        queue.append(
            {
                "key": key,
                "name": name,
                "origin": normalize_text(target.get("target_origin"))
                or (
                    "supabase_supplement"
                    if key.startswith("supabase:")
                    else "ncpms"
                ),
                "candidates": candidates,
            }
        )

    queue.sort(key=lambda row: (row["origin"], row["name"], row["key"]))
    write_jsonl_atomic(queue_path, queue)
    print(f"[BUILD] unmatched={len(queue)} queue={queue_path}")
    print('후보는 "candidates": ["후보1", "후보2"] 형태로 수정하세요.')
    return 0


def query_candidate(
    client: PsisClient,
    candidate: str,
    *,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], int | None]:
    rows: list[dict[str, Any]] = []
    total_count: int | None = None
    previous_fingerprint = ""
    for page in range(1, max_pages + 1):
        print(f"  [SEARCH] {candidate} page={page}", flush=True)
        payload = client.request(
            LIST_SERVICE,
            {
                "cropName": candidate,
                "cropCheck": "Y",
                "displayCount": page_size,
                "startPoint": page,
            },
        )
        page_rows = extract_list_items(payload)
        fingerprint = json.dumps(page_rows, ensure_ascii=False, sort_keys=True)
        if not page_rows or fingerprint == previous_fingerprint:
            break
        previous_fingerprint = fingerprint
        rows.extend(page_rows)
        total_text = find_first(payload, "totalCount")
        if total_text:
            try:
                total_count = int(total_text.replace(",", ""))
            except ValueError:
                pass
        if total_count is not None and len(rows) >= total_count:
            break
        if len(page_rows) < page_size:
            break
    return rows, total_count


def summarize_returned_crops(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        counts[
            (
                normalize_text(row.get("cropCd")),
                normalize_text(row.get("cropName")),
            )
        ] += 1
    return [
        {"crop_code": code or None, "crop_name": name or None, "records": count}
        for (code, name), count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0][1], item[0][0])
        )
    ]


def target_index(all_psis_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        normalize_text(row.get("ncpms_plant_key")): row
        for row in read_jsonl(all_psis_dir / TARGETS_NAME)
        if normalize_text(row.get("ncpms_plant_key"))
    }


def scoped_registration_rows(
    raw_rows: list[dict[str, Any]],
    *,
    target: dict[str, Any],
    candidate: str,
) -> list[dict[str, Any]]:
    scoped: list[dict[str, Any]] = []
    candidate_key = normalized_name(candidate)
    for raw in raw_rows:
        row = normalized_list_row(raw)
        # cropCheck=Y is intended to be exact, but retain only a normalized
        # exact response so a provider-side behavior change cannot silently
        # attach another crop to this plant.
        if normalized_name(row.get("crop_name")) != candidate_key:
            continue
        scoped.append(
            {
                **row,
                "ncpms_plant_key": target.get("ncpms_plant_key"),
                "ncpms_crop_code": target.get("ncpms_crop_code"),
                "ncpms_crop_name": target.get("ncpms_crop_name"),
                "crop_match_status": "verified",
                "crop_match_method": "candidate_alias_exact",
                "crop_match_score": 1.0,
                "query_crop_name": candidate,
                "query_crop_check": "Y",
            }
        )
    return scoped


def merge_hit_rows(
    all_psis_dir: Path,
    *,
    target: dict[str, Any],
    candidate: str,
    raw_rows: list[dict[str, Any]],
) -> int:
    new_rows = scoped_registration_rows(
        raw_rows,
        target=target,
        candidate=candidate,
    )
    if not new_rows:
        return 0

    list_path = all_psis_dir / LIST_PATH
    existing_rows = read_jsonl(list_path)
    merged_rows = dedupe(
        [*existing_rows, *new_rows],
        ("ncpms_plant_key", "pesti_code", "disease_use_seq"),
    )
    write_jsonl_atomic(list_path, merged_rows)

    mappings = read_jsonl(all_psis_dir / NCPMS_MAPPING_PATH)
    mapping_counts: Counter[tuple[str, str]] = Counter(
        (
            normalize_text(row.get("crop_code")),
            normalize_text(row.get("crop_name")),
        )
        for row in new_rows
    )
    for (crop_code, crop_name), count in mapping_counts.items():
        mappings.append(
            {
                "ncpms_plant_key": target.get("ncpms_plant_key"),
                "ncpms_crop_code": target.get("ncpms_crop_code"),
                "ncpms_crop_name": target.get("ncpms_crop_name"),
                "psis_crop_code": crop_code or None,
                "psis_crop_name": crop_name or None,
                "match_status": "verified",
                "match_method": "candidate_alias_exact",
                "query_crop_name": candidate,
                "registration_count": count,
                "collected_at": now_iso(),
            }
        )
    mappings = dedupe(
        mappings,
        (
            "ncpms_plant_key",
            "psis_crop_code",
            "psis_crop_name",
            "match_method",
        ),
    )
    write_jsonl_atomic(all_psis_dir / NCPMS_MAPPING_PATH, mappings)

    # Rebuild both derived files from the complete merged list so all existing
    # records are retained and the new candidate-hit rows become available to
    # later bulk processing immediately.
    build_crop_outputs(merged_rows, all_psis_dir)
    return len(new_rows)


def make_client(args: argparse.Namespace) -> PsisClient:
    if not 1 <= args.page_size <= 99:
        raise ValueError("--page-size must be between 1 and 99.")
    if args.max_pages < 1:
        raise ValueError("--max-pages must be positive.")
    env = load_env()
    api_key = args.api_key or env.get("PSIS_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "PSIS_API_KEY is required. Put it in the repository root .env."
        )
    return PsisClient(
        api_key=api_key,
        endpoint=args.endpoint,
        service_type=args.service_type,
        timeout=args.timeout,
        delay=args.delay,
        retries=args.retries,
    )


def search_queue(
    queue_path: Path,
    results_path: Path,
    args: argparse.Namespace,
) -> int:
    if not queue_path.exists():
        raise FileNotFoundError(
            f"Queue file is missing: {queue_path}. Run --build first."
        )
    queue = [compact_queue_row(row) for row in read_jsonl(queue_path)]
    queue = [row for row in queue if row["key"] and row["name"]]
    pending_count = sum(len(row["candidates"]) for row in queue)
    if not pending_count:
        print(f"[SEARCH] pending candidates=0 queue={queue_path}")
        return 0

    client = make_client(args)
    results = read_jsonl(results_path)
    hits = 0
    no_hits = 0
    errors = 0

    for target in list(queue):
        candidates = list(target["candidates"])
        if not candidates:
            continue
        target_hit = False
        retry_candidates: list[str] = []
        print(
            f"[TARGET] {target['name']} candidates={len(candidates)}",
            flush=True,
        )
        for candidate in candidates:
            searched_at = now_iso()
            try:
                rows, reported_total = query_candidate(
                    client,
                    candidate,
                    page_size=args.page_size,
                    max_pages=args.max_pages,
                )
                status = "hit" if rows else "no_hit"
                target_hit = target_hit or bool(rows)
                hits += int(bool(rows))
                no_hits += int(not rows)
                collected_count = 0
                if rows:
                    targets = target_index(args.all_psis_dir)
                    full_target = targets.get(target["key"])
                    if full_target:
                        collected_count = merge_hit_rows(
                            args.all_psis_dir,
                            target=full_target,
                            candidate=candidate,
                            raw_rows=rows,
                        )
                result = {
                    "target_key": target["key"],
                    "target_name": target["name"],
                    "target_origin": target["origin"],
                    "candidate": candidate,
                    "status": status,
                    "result_count": len(rows),
                    "reported_total_count": reported_total,
                    "returned_crops": summarize_returned_crops(rows),
                    "collected_records": collected_count,
                    "collected_into_all_psis_at": (
                        now_iso() if collected_count else None
                    ),
                    "searched_at": searched_at,
                }
                print(f"    [{status.upper()}] {candidate}: {len(rows)}건")
            except Exception as exc:
                errors += 1
                retry_candidates.append(candidate)
                result = {
                    "target_key": target["key"],
                    "target_name": target["name"],
                    "target_origin": target["origin"],
                    "candidate": candidate,
                    "status": "error",
                    "error": str(exc),
                    "searched_at": searched_at,
                }
                print(f"    [ERROR] {candidate}: {exc}")
            results.append(result)
            write_jsonl_atomic(results_path, results)

        if target_hit:
            # A hit is now fully documented in the results file, so remove the
            # plant from the human-editable unresolved queue.
            queue = [row for row in queue if row["key"] != target["key"]]
        else:
            # Tried names disappear; only API errors stay for an easy retry.
            target["candidates"] = retry_candidates
        write_jsonl_atomic(queue_path, queue)

    print(
        f"[DONE] hit_candidates={hits} no_hit_candidates={no_hits} "
        f"errors={errors} unresolved_plants={len(queue)}"
    )
    print(f"[QUEUE] {queue_path}")
    print(f"[RESULTS] {results_path}")
    return 0


def collect_recorded_hits(
    results_path: Path,
    args: argparse.Namespace,
) -> int:
    results = read_jsonl(results_path)
    hit_pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in results:
        if row.get("status") != "hit":
            continue
        key = (
            normalize_text(row.get("target_key")),
            normalize_text(row.get("candidate")),
        )
        if all(key):
            hit_pairs[key] = row
    if not hit_pairs:
        print(f"[COLLECT HITS] recorded hits=0 results={results_path}")
        return 0

    targets = target_index(args.all_psis_dir)
    client = make_client(args)
    total_collected = 0
    missing_targets = 0
    for (target_key, candidate), history in hit_pairs.items():
        if history.get("collected_into_all_psis_at"):
            print(
                f"[SKIP COLLECTED] {history.get('target_name')} -> "
                f"{candidate}"
            )
            continue
        target = targets.get(target_key)
        if not target:
            missing_targets += 1
            print(f"[MISSING TARGET] {target_key}")
            continue
        print(f"[COLLECT HIT] {target.get('ncpms_crop_name')} -> {candidate}")
        raw_rows, _ = query_candidate(
            client,
            candidate,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
        collected = merge_hit_rows(
            args.all_psis_dir,
            target=target,
            candidate=candidate,
            raw_rows=raw_rows,
        )
        total_collected += collected
        history["collected_records"] = collected
        history["collected_into_all_psis_at"] = now_iso()
        write_jsonl_atomic(results_path, results)
        print(f"    [MERGED] {collected}건")

    list_count = len(read_jsonl(args.all_psis_dir / LIST_PATH))
    relation_count = len(read_jsonl(args.all_psis_dir / RELATION_PATH))
    crop_count = len(read_jsonl(args.all_psis_dir / CROP_PATH))
    print(
        f"[DONE] hit_targets={len(hit_pairs)} collected={total_collected} "
        f"missing_targets={missing_targets} list={list_count} "
        f"relations={relation_count} crops={crop_count}"
    )
    return 0


def list_status(queue_path: Path, results_path: Path) -> int:
    queue = [compact_queue_row(row) for row in read_jsonl(queue_path)]
    results = read_jsonl(results_path)
    status_counts = Counter(normalize_text(row.get("status")) for row in results)
    pending = sum(len(row["candidates"]) for row in queue)
    print(
        f"unresolved_plants={len(queue)} pending_candidates={pending} "
        f"hits={status_counts['hit']} no_hits={status_counts['no_hit']} "
        f"errors={status_counts['error']}"
    )
    for row in queue:
        candidates = ", ".join(row["candidates"]) or "-"
        print(f"{row['name']}\t[{candidates}]")
    return 0


def export_candidate_sample(
    queue_path: Path,
    results_path: Path,
    sample_path: Path,
) -> int:
    """Export a reproducible snapshot without changing the active queue."""
    queue_rows = read_jsonl(queue_path)
    result_rows = read_jsonl(results_path)
    by_key: dict[str, dict[str, Any]] = {}

    for row in queue_rows:
        key = normalize_text(row.get("key"))
        if not key:
            continue
        by_key[key] = {
            "key": key,
            "name": normalize_text(row.get("name")),
            "origin": normalize_text(row.get("origin")),
            "candidates": clean_candidates(
                row.get("candidates"),
                normalize_text(row.get("name")),
            ),
        }

    for row in result_rows:
        key = normalize_text(row.get("target_key"))
        if not key:
            continue
        item = by_key.setdefault(
            key,
            {
                "key": key,
                "name": normalize_text(row.get("target_name")),
                "origin": normalize_text(row.get("target_origin")),
                "candidates": [],
            },
        )
        candidate = normalize_text(row.get("candidate"))
        if (
            candidate
            and normalized_name(candidate)
            != normalized_name(item["name"])
            and candidate not in item["candidates"]
        ):
            item["candidates"].append(candidate)

    sample_rows = sorted(
        by_key.values(),
        key=lambda row: (
            normalized_name(row["name"]),
            row["key"],
        ),
    )
    write_jsonl_atomic(sample_path, sample_rows)

    hit_keys = {
        normalize_text(row.get("target_key"))
        for row in result_rows
        if normalize_text(row.get("status")).lower() == "hit"
    }
    print(
        f"sample={len(sample_rows)} "
        f"resolved_hits={len(hit_keys)} "
        f"output={sample_path}"
    )
    return 0


def main() -> int:
    args = parse_args()
    queue_path, results_path = resolve_paths(args)
    if args.build:
        return build_queue(args.all_psis_dir, queue_path, results_path)
    if args.search:
        return search_queue(queue_path, results_path, args)
    if args.collect_hits:
        return collect_recorded_hits(results_path, args)
    if args.export_sample:
        return export_candidate_sample(
            queue_path,
            results_path,
            args.sample_file,
        )
    return list_status(queue_path, results_path)


if __name__ == "__main__":
    raise SystemExit(main())
