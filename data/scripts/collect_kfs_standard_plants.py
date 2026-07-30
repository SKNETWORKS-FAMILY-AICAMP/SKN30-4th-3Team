"""국립수목원 국가표준식물목록(공공데이터포털 15142872) 표준 식물명 수집기.

우리가 보유한 데이터(양육/병/해충/농약 커버리지 271종)의 자유 텍스트 한글명을
표준 국명 ↔ 학명 ↔ 이명 ↔ 과/속 ↔ 표준ID로 정규화하기 위한 마스터를 만든다.
이 마스터는 이후 plant_catalog 확장과 RAG 크롭 스코핑(plant_terms) 강화의 근거가 된다.

라이선스: 국가표준식물목록은 KOGL 제4유형(출처표시 · 상업적 이용금지 · 변경금지).
비상업 전제에서 국명/학명/이명 같은 "사실 데이터"만 수집한다. 원문(도감 서술문)은 저장하지 않는다.

──────────────────────────────────────────────────────────────────────────────
★ 실행 전 확인 (Swagger에서 확정) ─ 아래 CONFIG 두 가지만 맞으면 나머지는 자동:
    1) LIST_ENDPOINT_URL      : "요청 주소"(base + operation). 15142872 Swagger '미리보기' 참고.
    2) SEARCH_TERM_PARAM 등    : 국명 검색 파라미터명(국립수목원 계열은 보통 st/sw).
  확정 방법 →  python collect_kfs_standard_plants.py --probe 토마토
              실제 XML 원문과 발견된 태그 구조를 그대로 출력한다. 태그가 다르면
              FIELD_CANDIDATES에 추가하거나 --endpoint-url 로 주소만 교체하면 된다.

인증키: .env 의 PUBLIC_DATA_PORTAL_API_KEY 사용. **Decoding 키(원문)** 권장.
        (Encoding 키를 쓰면 --key-mode raw 로 실행)

사용 예:
    # 0) 엔드포인트/필드 확인 (1콜)
    python collect_kfs_standard_plants.py --probe 토마토
    # 1) 커버리지 표 기반 전량 수집 (개발계정 1,000콜 한도 내: 271종 ≈ 271콜)
    python collect_kfs_standard_plants.py --coverage ../raw/plant_data_coverage.md
    # 2) 특정 이름만
    python collect_kfs_standard_plants.py --names "감자,고추,방울토마토"

재실행 시 원본 XML이 RAW_DIR에 캐시되어 이미 받은 이름은 재호출하지 않는다(--force로 강제).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from common import (
    INTERIM_DIR,
    KNOWN_CROP_OR_PLANT_NAMES,
    RAW_DIR,
    ensure_dirs,
    http_get_text,
    normalize_text,
    now_iso,
    stable_hash,
    write_jsonl,
)
from config import PUBLIC_DATA_PORTAL_API_KEY

# ───────────────────────── CONFIG (Swagger로 확정) ─────────────────────────
# 국립수목원 국가표준식물목록(KpniService, 기관코드 1400119). Swagger '미리보기' 요청 주소로 확정됨.
# gnrlNmLtrtrSearch = 국명 출처문헌 조회(국명 → 학명/문헌). 국명↔학명 매핑에 사용.
LIST_ENDPOINT_URL = "https://apis.data.go.kr/1400119/KpniService/gnrlNmLtrtrSearch"
SEARCH_TERM_PARAM = "reqPlantGnrlNm"  # 요청 식물 국명 파라미터
SEARCH_TYPE_PARAM = ""                # 이 오퍼레이션은 검색유형 파라미터 없음 → 생략
SEARCH_TYPE_VALUE = ""
PAGE_SIZE_PARAM = "numOfRows"
PAGE_NO_PARAM = "pageNo"

# XML 태그명 관용 매핑. KpniService gnrlNmLtrtrSearch 실측 태그를 앞에 두고 타 오퍼레이션 후보를 뒤에 유지.
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "korean_name": ("plantGnrlNm", "korNm", "korNmNm", "koreanNm", "plntbneNm", "nm"),
    "scientific_name": ("plantSpecsScnm", "scNm", "scnm", "sciNm", "scientificName", "plntSpecScnm"),
    "recommend_flag": ("rcmmnTpcdNm", "reclassNm", "recommendNm"),
    "literature": ("ltrtrInfrmNm", "ltrtrNm"),
    "name_type": ("lvbngFrlngTpcdNm",),
    # 아래는 이 오퍼레이션 응답엔 없음(과/속/ID). scnmSearch 2차 보강 시 사용.
    "standard_id": ("plantPilbkNo", "spcsId", "plntSpecNo", "id", "no"),
    "family_kor": ("familyKorNm", "fmlyKorNm", "gwaKorNm"),
    "family_sci": ("familyNm", "fmlyNm", "gwaNm"),
}
RECOMMEND_VALUE = "추천명"  # rcmmnTpcdNm 값: 추천명(=표준 국명) / 비추천명(=이명)
# 검색 결과 아이템을 감싸는 태그.
ITEM_TAGS = ("item", "row", "plantList", "result")

SOURCE_LABEL = "국립수목원 국가표준식물목록"
SOURCE_DATASET_ID = "15142872"
SOURCE_LICENSE = "KOGL-4 (출처표시·상업적이용금지·변경금지)"
RAW_SUBDIR = "kfs_standard_plants"
DEFAULT_OUTPUT = INTERIM_DIR / "kfs_standard_plants.jsonl"
DEFAULT_COVERAGE = RAW_DIR / "plant_data_coverage.md"
COVERAGE_FLAG_LABELS = ("cultivation", "disease", "pest", "pesticide")  # 양육/병/해충/농약


# ───────────────────────────── 시드(커버리지) 로딩 ─────────────────────────────
def parse_coverage_markdown(path: Path) -> list[dict[str, Any]]:
    """커버리지 마크다운 표에서 (대표명, DB표기 변형[], 커버리지 플래그)를 추출한다."""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        head = cells[0]
        # 헤더행/구분행 스킵
        if set(head) <= set("-: "):
            continue
        if "식물명" in head or head in {"대표 식물명", "대표식물명"}:
            continue
        name, aliases = _split_name_aliases(head)
        if not name:
            continue
        flags = {}
        for label, cell in zip(COVERAGE_FLAG_LABELS, cells[1:]):
            flags[label] = cell.upper() in {"O", "○", "Y", "YES", "TRUE", "1"}
        rows.append({"name": name, "db_aliases": aliases, "coverage": flags})
    return rows


def _split_name_aliases(head: str) -> tuple[str, list[str]]:
    """대표명과 별칭을 분리.
    - 'X (DB 표기: a, b)' → ('X', [a, b])
    - 'X(별칭)'           → ('X', ['별칭'])   # 인라인 괄호(미완결 포함)
    """
    marker = None
    for m in ("DB 표기", "DB표기", "표기"):
        idx = head.find(m)
        if idx != -1:
            marker = idx
            break
    if marker is not None:
        open_paren = head.rfind("(", 0, marker)
        name = head[:open_paren].strip() if open_paren != -1 else head[:marker].strip()
        tail = head[marker:]
        colon = tail.find(":")
        alias_blob = tail[colon + 1:].strip() if colon != -1 else ""
        if alias_blob.endswith(")"):
            alias_blob = alias_blob[:-1]  # '(DB 표기: ...)' 닫는 래퍼 괄호 1개만 제거
        return name.strip(), _split_aliases(alias_blob)
    # 마커 없음: 인라인 괄호가 있으면 기본명 + 괄호내용(별칭)으로 분리 (strip('()') 파괴 금지)
    base = re.split(r"[(（]", head, maxsplit=1)[0].strip()
    aliases: list[str] = []
    for inside in re.findall(r"[(（]([^()（）]*)", head):
        for part in re.split(r"[,，/]", inside):
            part = part.strip().rstrip(")） ").strip()
            if part and part not in aliases:
                aliases.append(part)
    return (base or head.strip()), aliases


def _split_aliases(blob: str) -> list[str]:
    """콤마로 분리하되 이름 안의 괄호는 보존한다: '고추(보통재배), 고추(억제재배)'."""
    parts: list[str] = []
    depth = 0
    buf = ""
    for ch in blob:
        if ch == "(":
            depth += 1
            buf += ch
        elif ch == ")":
            depth = max(0, depth - 1)
            buf += ch
        elif ch == "," and depth == 0:
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def load_seeds(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        return [{"name": n, "db_aliases": [], "coverage": {}} for n in names]
    if args.names_file:
        seeds = []
        for line in Path(args.names_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                seeds.append({"name": line, "db_aliases": [], "coverage": {}})
        return seeds
    coverage_path = Path(args.coverage) if args.coverage else DEFAULT_COVERAGE
    if coverage_path.exists():
        seeds = parse_coverage_markdown(coverage_path)
        if seeds:
            print(f"[seed] 커버리지 {coverage_path} 에서 {len(seeds)}종 로드")
            return seeds
        print(f"[seed] {coverage_path} 에서 표를 찾지 못했습니다.")
    print("[seed] 커버리지/names 미지정 → common.KNOWN_CROP_OR_PLANT_NAMES 폴백 사용")
    uniq = list(dict.fromkeys(KNOWN_CROP_OR_PLANT_NAMES))
    return [{"name": n, "db_aliases": [], "coverage": {}} for n in uniq]


# ───────────────────────────── HTTP / XML ─────────────────────────────
def build_url(endpoint: str, params: dict[str, Any], service_key: str, key_mode: str) -> str:
    if key_mode == "raw":
        encoded_key = service_key
    elif key_mode == "decode":
        encoded_key = quote(service_key, safe="")
    else:  # auto: 이미 %-인코딩된 키면 그대로, 아니면 1회 인코딩
        encoded_key = service_key if "%" in service_key else quote(service_key, safe="")
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    sep = "&" if query else ""
    return f"{endpoint}?serviceKey={encoded_key}{sep}{query}"


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_to_dict(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return normalize_text(element.text or "")
    data: dict[str, Any] = {}
    for child in children:
        key = strip_ns(child.tag)
        value = element_to_dict(child)
        if key in data:
            if not isinstance(data[key], list):
                data[key] = [data[key]]
            data[key].append(value)
        else:
            data[key] = value
    return data


def find_items(root: ET.Element) -> list[ET.Element]:
    for tag in ITEM_TAGS:
        found = [el for el in root.iter() if strip_ns(el.tag) == tag]
        if found:
            return found
    return []


def detect_api_error(root: ET.Element) -> str | None:
    """data.go.kr 공통 에러(키 미등록/한도초과 등)를 감지해 메시지를 돌려준다."""
    codes: dict[str, str] = {}
    for el in root.iter():
        tag = strip_ns(el.tag)
        if tag in {"resultCode", "returnReasonCode", "errMsg", "resultMsg", "returnAuthMsg", "cmmMsgHeader"}:
            codes[tag] = normalize_text(el.text or "")
    ok_codes = {"00", "0", "", "NORMAL SERVICE.", "NORMAL_CODE"}
    result_code = codes.get("resultCode") or codes.get("returnReasonCode")
    if result_code and result_code not in ok_codes:
        msg = codes.get("errMsg") or codes.get("returnAuthMsg") or codes.get("resultMsg") or ""
        return f"code={result_code} {msg}".strip()
    return None


def fetch_xml(name: str, args: argparse.Namespace, service_key: str) -> str:
    """원본 XML을 반환. RAW 캐시가 있으면 재사용(할당량 보호), 일시적 네트워크 오류는 재시도."""
    raw_dir = RAW_DIR / RAW_SUBDIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{stable_hash(name)}.xml"
    if raw_path.exists() and not args.force:
        return raw_path.read_text(encoding="utf-8")

    params: dict[str, Any] = {
        SEARCH_TERM_PARAM: name,
        PAGE_SIZE_PARAM: args.num_rows,
        PAGE_NO_PARAM: 1,
    }
    if SEARCH_TYPE_PARAM:
        params[SEARCH_TYPE_PARAM] = SEARCH_TYPE_VALUE
    url = build_url(args.endpoint_url, params, service_key, args.key_mode)

    # http_get_text 는 HTTPError/URLError 만 감싸므로, read 타임아웃(TimeoutError=OSError)까지 잡아 재시도한다.
    last_exc: Exception | None = None
    for attempt in range(args.retries + 1):
        try:
            raw = http_get_text(url, timeout=args.timeout)
            raw_path.write_text(raw, encoding="utf-8", newline="\n")
            time.sleep(args.sleep)
            return raw
        except (RuntimeError, OSError) as exc:
            last_exc = exc
            if attempt < args.retries:
                time.sleep(args.retry_sleep * (attempt + 1))
    raise RuntimeError(f"{args.retries + 1}회 시도 실패: {last_exc}")


# ───────────────────────────── 필드 매핑 / 정규화 ─────────────────────────────
def pick_field(record: dict[str, Any], key: str) -> str:
    for candidate in FIELD_CANDIDATES.get(key, ()):
        value = record.get(candidate)
        if isinstance(value, list):
            value = next((v for v in value if v), "")
        if value:
            return normalize_text(str(value))
    return ""


def aggregate_kpni(seed: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """KpniService 국명-문헌 행들을 학명 기준으로 묶어 표준국명·학명·이명으로 정규화한다.

    한 국명 질의는 같은 종의 여러 국명(추천명 1 + 비추천명 N) × 여러 출처문헌 행을 돌려준다.
    → 질의어와 국명이 일치하는 행의 학명을 앵커로 잡고, 그 학명 그룹에서
      추천명 = 표준국명, 나머지 국명 = 이명으로 집계한다.
    """
    rows: list[tuple[str, str, str]] = []  # (국명, 학명, 추천구분)
    for it in items:
        gnrl = pick_field(it, "korean_name")
        scnm = pick_field(it, "scientific_name")
        rec = pick_field(it, "recommend_flag")
        if gnrl and scnm:
            rows.append((gnrl, scnm, rec))
    if not rows:
        return None

    query = seed["name"]
    exact = [r for r in rows if r[0] == query]
    if exact:
        anchor_scnm, match_type = exact[0][1], "exact"
    else:
        rec_rows = [r for r in rows if r[2] == RECOMMEND_VALUE]
        if rec_rows:
            anchor_scnm, match_type = rec_rows[0][1], "recommended"
        else:
            anchor_scnm = Counter(r[1] for r in rows).most_common(1)[0][0]
            match_type = "frequent"

    group = [r for r in rows if r[1] == anchor_scnm]
    recommended = [gnrl for gnrl, _s, rec in group if rec == RECOMMEND_VALUE and gnrl]
    if recommended:
        standard_kor, recommend_source = recommended[0], "KPNI 추천명"
    elif any(gnrl == query for gnrl, _s, _r in group):
        standard_kor, recommend_source = query, "질의어(추천명 없음)"
    else:
        standard_kor, recommend_source = group[0][0], "최빈학명 대표"

    synonyms: list[str] = []
    for gnrl, _scnm, _rec in group:
        if gnrl and gnrl != standard_kor and gnrl not in synonyms:
            synonyms.append(gnrl)

    return {
        "query_name": query,
        "db_aliases": seed.get("db_aliases", []),
        "coverage": seed.get("coverage", {}),
        "matched": True,
        "match_type": match_type,
        "standard_korean_name": standard_kor,
        "scientific_name": anchor_scnm,
        "synonyms": synonyms,
        "recommend_source": recommend_source,
        "record_count": len(group),
        "source": SOURCE_LABEL,
        "source_dataset_id": SOURCE_DATASET_ID,
        "license": SOURCE_LICENSE,
        "collected_at": now_iso(),
    }


def unmatched_record(seed: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "query_name": seed["name"],
        "db_aliases": seed.get("db_aliases", []),
        "coverage": seed.get("coverage", {}),
        "matched": False,
        "match_type": "none",
        "reason": reason,
        "standard_korean_name": seed["name"],
        "scientific_name": "",
        "synonyms": [],
        "recommend_source": "",
        "record_count": 0,
        "source": SOURCE_LABEL,
        "source_dataset_id": SOURCE_DATASET_ID,
        "license": SOURCE_LICENSE,
        "collected_at": now_iso(),
    }


# ───────────────────────────── probe ─────────────────────────────
def run_probe(name: str, args: argparse.Namespace, service_key: str) -> int:
    print(f"[probe] '{name}' 조회\n[probe] endpoint = {args.endpoint_url}")
    try:
        raw = fetch_xml(name, args, service_key)
    except (RuntimeError, OSError) as exc:
        print(f"[probe] 요청 실패: {exc}")
        print("[probe] → LIST_ENDPOINT_URL / 검색 파라미터를 Swagger '미리보기'로 확인 후 --endpoint-url 로 교체하세요.")
        return 2
    print("\n──── RAW XML (앞부분) ────")
    print(raw[:2000])
    try:
        root = ET.fromstring(raw.strip())
    except ET.ParseError as exc:
        print(f"\n[probe] XML 파싱 실패: {exc} (원본이 XML이 아닐 수 있음)")
        return 2
    err = detect_api_error(root)
    if err:
        print(f"\n[probe] API 에러 감지: {err}")
        return 2
    item_els = find_items(root)
    print(f"\n──── 발견된 item 태그 수: {len(item_els)} ────")
    if not item_els:
        print("[probe] item 태그를 찾지 못했습니다. ITEM_TAGS 에 실제 반복 태그명을 추가하세요.")
        return 0

    items = [element_to_dict(el) for el in item_els]
    items = [it for it in items if isinstance(it, dict)]
    first = items[0]
    print("[probe] 첫 item 의 태그 → 값:")
    for k, v in first.items():
        print(f"   {k}: {(v if isinstance(v, str) else str(v))[:60]}")
    print("\n[probe] 필드 매핑:")
    for key in ("korean_name", "scientific_name", "recommend_flag", "literature"):
        print(f"   {key} → {pick_field(first, key) or '(미매칭 — FIELD_CANDIDATES 보강 필요)'}")

    agg = aggregate_kpni({"name": name, "db_aliases": [], "coverage": {}}, items)
    if agg:
        print("\n[probe] 집계(정규화) 결과:")
        print(f"   표준국명 : {agg['standard_korean_name']}  ({agg['recommend_source']})")
        print(f"   학명     : {agg['scientific_name']}")
        print(f"   이명     : {agg['synonyms']}")
        print(f"   매칭유형 : {agg['match_type']} / 그룹 행수: {agg['record_count']}")
    else:
        print("\n[probe] 집계 실패: 국명/학명 태그 매핑을 확인하세요.")
    return 0


# ───────────────────────────── main ─────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="국립수목원 국가표준식물목록(15142872) 표준 식물명 수집")
    p.add_argument("--probe", metavar="NAME", help="이름 1건만 조회해 원본 XML/필드 구조를 출력(엔드포인트·태그 확정용)")
    p.add_argument("--coverage", help=f"커버리지 마크다운 경로 (기본 {DEFAULT_COVERAGE})")
    p.add_argument("--names", help="쉼표로 구분한 식물명 목록(커버리지 대신 사용)")
    p.add_argument("--names-file", help="식물명 목록 파일(한 줄에 하나)")
    p.add_argument("--endpoint-url", default=LIST_ENDPOINT_URL, help="요청 주소(base+operation). Swagger 확정값")
    p.add_argument("--num-rows", type=int, default=100, help="이름당 조회 행 수(이명 전량 수집용, 1콜)")
    p.add_argument("--limit", type=int, help="처리할 이름 개수 상한(할당량 보호)")
    p.add_argument("--sleep", type=float, default=0.2, help="호출 간 대기(초)")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--retries", type=int, default=2, help="일시적 네트워크 오류 재시도 횟수")
    p.add_argument("--retry-sleep", type=float, default=1.5, help="재시도 간 기본 대기(초, 시도마다 증가)")
    p.add_argument("--key-mode", choices=("auto", "raw", "decode"), default="auto",
                   help="serviceKey 인코딩: auto(기본)/raw(Encoding키)/decode(Decoding키 강제 인코딩)")
    p.add_argument("--force", action="store_true", help="RAW 캐시 무시하고 재호출")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()

    service_key = PUBLIC_DATA_PORTAL_API_KEY
    if not service_key:
        print("PUBLIC_DATA_PORTAL_API_KEY 가 .env 에 없습니다. data.go.kr 발급키를 설정하세요.", file=sys.stderr)
        return 1

    if args.endpoint_url == LIST_ENDPOINT_URL:
        print(f"[warn] 기본 엔드포인트 사용 중: {LIST_ENDPOINT_URL}")
        print("[warn] Swagger '미리보기'의 요청 주소와 다르면 --probe 로 먼저 확인하세요.\n")

    if args.probe:
        return run_probe(args.probe, args, service_key)

    seeds = load_seeds(args)
    if args.limit:
        seeds = seeds[: args.limit]

    results: list[dict[str, Any]] = []
    matched = 0
    unmatched_names: list[str] = []
    for i, seed in enumerate(seeds, start=1):
        name = seed["name"]
        try:
            raw = fetch_xml(name, args, service_key)
            root = ET.fromstring(raw.strip())
        except (RuntimeError, OSError, ET.ParseError) as exc:
            results.append(unmatched_record(seed, f"fetch/parse 실패: {exc}"))
            unmatched_names.append(name)
            print(f"[{i}/{len(seeds)}] {name}: 실패 ({exc})")
            continue

        err = detect_api_error(root)
        if err:
            # 할당량 초과 등은 즉시 중단(잔여 이름을 낭비하지 않도록)
            print(f"[{i}/{len(seeds)}] {name}: API 에러 → {err}")
            if "LIMIT" in err.upper() or "EXCEED" in err.upper():
                print("[stop] API 에러로 중단합니다. 원인 해결 후 재실행하면 캐시 덕에 이어서 진행됩니다.")
                results.append(unmatched_record(seed, f"api error: {err}"))
                break
            results.append(unmatched_record(seed, f"api error: {err}"))
            unmatched_names.append(name)
            continue

        items = [element_to_dict(el) for el in find_items(root)]
        items = [it for it in items if isinstance(it, dict)]
        record = aggregate_kpni(seed, items)
        if record is None:
            results.append(unmatched_record(seed, "표준목록에 매칭 결과 없음"))
            unmatched_names.append(name)
            print(f"[{i}/{len(seeds)}] {name}: 매칭 없음")
            continue

        results.append(record)
        matched += 1
        print(f"[{i}/{len(seeds)}] {name} → {record['standard_korean_name']} "
              f"/ {record['scientific_name'] or '(학명 미매칭)'} "
              f"[{record['match_type']}, 이명 {len(record['synonyms'])}]")

    count = write_jsonl(Path(args.output), results)
    print(f"\n완료: {count}건 기록 → {args.output}")
    print(f"매칭 {matched} / 미매칭 {len(results) - matched}")
    if unmatched_names:
        print("미매칭 이름:", ", ".join(unmatched_names[:40]) + (" ..." if len(unmatched_names) > 40 else ""))
    if matched == 0:
        print("\n[hint] 매칭이 0건이면 대개 엔드포인트/태그 문제입니다 → python collect_kfs_standard_plants.py --probe 토마토")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
