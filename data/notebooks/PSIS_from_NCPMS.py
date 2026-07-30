# python data/notebooks/all_PSIS.py --ncpms-only --list-only
# 명령어로 목록과 이름 매핑 먼저 점검후

# python data/notebooks/all_PSIS.py --ncpms-only --resume
# 상세정보 수집 실행을 권장합니다

# NCPMS에서 병/해충 문건이 있었던 리스트를 대상으로 농약 정보를 수집합니다.

# 정확한 이름 + Supabase에 이미 적재된 작물 & 99개를 한 페이지에
'''
아래 명령어를 powershell에서 실행 시,
NCPMS에서 병/해충 받은 목록 + supabase_supplement_targets.jsonl(이미 적재된 항목)
리스트를 불러와 농약 문서를 불러옵니다

python data/notebooks/PSIS_from_NCPMS.py `
  --ncpms-only `
  --already-exist-in-supabase `
  --ncpms-discovery none `
  --page-size 99 `
  --delay 0 `
  --list-only

  **명령어**
  --list-only 실행 시 SVC01만 호출합니다
  
  --details-only 실행 시 all_PSIS/pesticide_registration_list.jsonl 리스트를
    참조하여 고유 pesti_code마다 대표 disease_use_seq 하나로 SVC02를
    호출합니다. 제품 공통 독성·성분 정보를 수집하는 기본 모드입니다.

  --details-scope registration을 함께 사용하면 모든
    pesti_code + disease_use_seq 등록 건을 대상으로 SVC02를 호출합니다
'''

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DATA_DIR.parent
NOTEBOOKS_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = DATA_DIR / "interim" / "all_PSIS"
DEFAULT_NCPMS_DIR = DATA_DIR / "interim" / "all_NCPMS"
DEFAULT_SUPPLEMENT_FILE = NOTEBOOKS_DIR / "supabase_supplement_targets.jsonl"
DEFAULT_ENDPOINT = "https://psis.rda.go.kr/openApi/service.do"
DEFAULT_SERVICE_TYPE = "AA001"  # XML, according to the supplied PSIS manual.
LIST_SERVICE = "SVC01"
DETAIL_SERVICE = "SVC02"
DEFAULT_PAGE_SIZE = 50
SAFETY_TAGS = [
    "not_diagnosis",
    "expert_check_required",
    "pesticide_caution",
]

LIST_PATH = "pesticide_registration_list.jsonl"
DETAIL_PATH = "pesticide_registration_details.jsonl"
DETAIL_SUMMARY_PATH = "details_summary.json"
CROP_PATH = "crops.jsonl"
RELATION_PATH = "crop_pesticide_relations.jsonl"
ERROR_PATH = "errors.json"
SUMMARY_PATH = "summary.json"
NCPMS_TARGET_PATH = "ncpms_target_plants.jsonl"
NCPMS_MAPPING_PATH = "ncpms_psis_crop_mapping.jsonl"
NCPMS_MAPPING_REVIEW_PATH = "crop_mapping_review_required.jsonl"

# Only semantic aliases that are sufficiently safe for automatic collection
# belong here. Broader family/cultivar relationships are discovered by loose
# search and written to the review file instead of being silently accepted.
NCPMS_TO_PSIS_ALIASES: dict[str, list[str]] = {
    "논벼": ["벼"],
    "양송이": ["양송이버섯"],
    "느타리": ["느타리버섯"],
    "큰느타리": ["새송이버섯", "큰느타리버섯"],
    "쥬키니호박": ["주키니호박"],
    "브로콜리(녹색꽃양배추)": ["브로콜리", "녹색꽃양배추"],
    "콜라비(순무양배추)": ["콜라비", "순무양배추"],
    "백수오(큰조롱)": ["백수오", "큰조롱"],
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_env() -> dict[str, str]:
    values = dict(os.environ)
    for path in (REPO_ROOT / ".env", DATA_DIR / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip("\"'"))
    return values


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(?:p|div|li|ul|ol)\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)</?[a-z][^>]*>", " ", text)
    text = unicodedata.normalize("NFC", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def normalized_name(value: Any) -> str:
    return normalize_text(value).replace(" ", "")


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_to_obj(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return normalize_text(element.text)
    result: dict[str, Any] = {}
    for child in children:
        key = strip_namespace(child.tag)
        value = element_to_obj(child)
        if key in result:
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


def parse_payload(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8-sig", errors="replace").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"list": parsed}

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(
            f"PSIS returned neither JSON nor XML: {text[:300]}"
        ) from exc
    parsed = element_to_obj(root)
    if isinstance(parsed, dict):
        return parsed
    return {strip_namespace(root.tag): parsed}


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def find_first(value: Any, *keys: str) -> str:
    for row in walk(value):
        for key in keys:
            item = row.get(key)
            if isinstance(item, (dict, list)):
                continue
            text = normalize_text(item)
            if text:
                return text
    return ""


def extract_list_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for value in walk(payload):
        if "pestiCode" not in value or "diseaseUseSeq" not in value:
            continue
        row = {
            key: normalize_text(item) if not isinstance(item, (dict, list)) else item
            for key, item in value.items()
        }
        key = (
            normalize_text(row.get("pestiCode")),
            normalize_text(row.get("diseaseUseSeq")),
            normalize_text(row.get("cropCd")),
            normalize_text(row.get("cropName")),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def extract_detail(payload: dict[str, Any]) -> dict[str, Any]:
    # SVC02 normally puts the fields directly below <service>. Walking also
    # tolerates an extra wrapper should the provider change it.
    detail_fields = {
        "pestiKorName",
        "useName",
        "compName",
        "pestiBrandName",
        "pestiEngName",
        "regCpntQnty",
        "toxicGubun",
        "toxicName",
        "fishToxicGubun",
        "cropName",
        "diseaseWeedName",
        "pestiUse",
        "dilutUnit",
        "useSuittime",
        "useNum",
    }
    best: dict[str, Any] = {}
    for row in walk(payload):
        score = len(detail_fields.intersection(row))
        if score > len(detail_fields.intersection(best)):
            best = row
    return {
        key: normalize_text(value) if not isinstance(value, (dict, list)) else value
        for key, value in best.items()
        if key not in {"errorCode", "errorMsg"}
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_no}") from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def dedupe(
    rows: Iterable[dict[str, Any]], key_fields: Iterable[str]
) -> list[dict[str, Any]]:
    fields = tuple(key_fields)
    result: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(normalize_text(row.get(field)) for field in fields)
        if not any(key):
            continue
        result[key] = row
    return list(result.values())


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class PsisClient:
    api_key: str
    endpoint: str = DEFAULT_ENDPOINT
    service_type: str = DEFAULT_SERVICE_TYPE
    timeout: float = 30.0
    delay: float = 0.15
    retries: int = 3

    def request(
        self, service_code: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "apiKey": self.api_key,
            "serviceCode": service_code,
        }
        if service_code == LIST_SERVICE:
            query["serviceType"] = self.service_type
        query.update(
            {
                key: value
                for key, value in (params or {}).items()
                if value not in (None, "")
            }
        )
        encoded = urllib.parse.urlencode(query)
        url = f"{self.endpoint}?{encoded}"
        safe_query = {key: value for key, value in query.items() if key != "apiKey"}
        safe_url = f"{self.endpoint}?{urllib.parse.urlencode(safe_query)}"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Farmhani-PSIS-Collector/1.0"},
                )
                with urllib.request.urlopen(
                    request, timeout=self.timeout
                ) as response:
                    payload = parse_payload(response.read())
                self.raise_api_error(payload, safe_url)
                if self.delay:
                    time.sleep(self.delay)
                return payload
            except (
                urllib.error.URLError,
                TimeoutError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 8))
        raise RuntimeError(
            f"PSIS request failed after {self.retries} attempts: {safe_url}"
        ) from last_error

    @staticmethod
    def raise_api_error(payload: dict[str, Any], safe_url: str) -> None:
        code = find_first(payload, "errorCode")
        message = find_first(payload, "errorMsg")
        if code:
            raise ValueError(f"PSIS API error {code}: {message} ({safe_url})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect the complete PSIS pesticide registration list (SVC01), "
            "then collect every registration detail (SVC02)."
        )
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Prefer PSIS_API_KEY in the repository root .env.",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--service-type", default=DEFAULT_SERVICE_TYPE)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=100_000)
    parser.add_argument(
        "--limit-records",
        type=int,
        help="Testing only: stop after this many SVC01 records.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay after each request; 0.15 stays below the documented 10 TPS.",
    )
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--ncpms-only",
        action="store_true",
        help=(
            "Collect only crops having verified NCPMS disease or insect "
            "relations, with exact/alias matching and review candidates."
        ),
    )
    parser.add_argument(
        "--ncpms-dir",
        type=Path,
        default=DEFAULT_NCPMS_DIR,
        help="Directory containing all_NCPMS relation and plant-code JSONL files.",
    )
    parser.add_argument(
        "--ncpms-discovery",
        choices=["always", "fallback", "none"],
        default="always",
        help=(
            "Loose crop-name discovery policy. 'always' minimizes missed "
            "variants; uncertain matches are review-only."
        ),
    )
    parser.add_argument(
        "--already-exist-in-supabase",
        action="store_true",
        help=(
            "Also collect PSIS for plants that already exist in the Supabase "
            "RAG store but have no NCPMS disease/insect relations. Loads the "
            "supplement targets from --supabase-supplement-file and merges "
            "them with the NCPMS-scoped targets (dedupe by crop name)."
        ),
    )
    parser.add_argument(
        "--supabase-supplement-file",
        type=Path,
        default=DEFAULT_SUPPLEMENT_FILE,
        help=(
            "JSONL of supplement targets (same schema as ncpms_target_plants) "
            "used by --already-exist-in-supabase. Only source_names and "
            "search_aliases are queried; review_aliases are documented only."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume list pages and skip details already saved.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Collect SVC01 and derived crop relations without SVC02 requests.",
    )
    parser.add_argument(
        "--details-only",
        action="store_true",
        help=(
            "Skip every SVC01 request and collect only missing SVC02 details "
            "from an existing pesticide_registration_list.jsonl. Existing "
            "detail rows are always preserved."
        ),
    )
    parser.add_argument(
        "--details-input",
        type=Path,
        help=(
            "Existing SVC01 JSONL used by --details-only. Defaults to "
            "<output-dir>/pesticide_registration_list.jsonl."
        ),
    )
    parser.add_argument(
        "--details-scope",
        choices=["product", "registration"],
        help=(
            "SVC02 collection unit. Defaults to 'product' with "
            "--details-only (one representative diseaseUseSeq per "
            "pestiCode), otherwise 'registration'."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and show output paths without API calls.",
    )

    # Optional SVC01 filters. Leave all empty to collect the complete list.
    parser.add_argument("--use-name")
    parser.add_argument("--crop-name")
    parser.add_argument("--crop-check", choices=["Y", "N"])
    parser.add_argument("--crop-name2")
    parser.add_argument("--crop-check2", choices=["Y", "N"])
    parser.add_argument("--crop-name3")
    parser.add_argument("--crop-check3", choices=["Y", "N"])
    parser.add_argument("--crop-name4")
    parser.add_argument("--crop-check4", choices=["Y", "N"])
    parser.add_argument("--disease-weed-name")
    parser.add_argument("--similar-flag", choices=["Y", "N"])
    parser.add_argument("--pesti-kor-name")
    parser.add_argument("--pesti-brand-name")
    parser.add_argument("--comp-name")
    return parser.parse_args()


def build_list_filters(args: argparse.Namespace) -> dict[str, str]:
    mapping = {
        "useName": args.use_name,
        "cropName": args.crop_name,
        "cropCheck": args.crop_check,
        "cropName2": args.crop_name2,
        "cropCheck2": args.crop_check2,
        "cropName3": args.crop_name3,
        "cropCheck3": args.crop_check3,
        "cropName4": args.crop_name4,
        "cropCheck4": args.crop_check4,
        "diseaseWeedName": args.disease_weed_name,
        "similarFlag": args.similar_flag,
        "pestiKorName": args.pesti_kor_name,
        "pestiBrandName": args.pesti_brand_name,
        "compName": args.comp_name,
    }
    return {key: value for key, value in mapping.items() if value}


def normalized_list_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "pesti_code": normalize_text(raw.get("pestiCode")),
        "disease_use_seq": normalize_text(raw.get("diseaseUseSeq")),
        "crop_code": normalize_text(raw.get("cropCd")) or None,
        "crop_name": normalize_text(raw.get("cropName")) or None,
        "crop_large_class_code": normalize_text(raw.get("cropLrclCd"))
        or None,
        "crop_large_class_name": normalize_text(raw.get("cropLrclNm"))
        or None,
        "target_disease_pest": normalize_text(raw.get("diseaseWeedName"))
        or None,
        "use_type": normalize_text(raw.get("useName")) or None,
        "pesticide_name": normalize_text(raw.get("pestiKorName")) or None,
        "brand_name": normalize_text(raw.get("pestiBrandName")) or None,
        "company_name": normalize_text(raw.get("compName")) or None,
        "active_ingredient": normalize_text(raw.get("engName")) or None,
        "manufacture_import_type": normalize_text(raw.get("cmpaItmNm"))
        or None,
        "mode_of_action": normalize_text(raw.get("indictSymbl")) or None,
        "first_registration_date": normalize_text(
            raw.get("applyFirstRegDate")
        )
        or None,
        "application_timing": normalize_text(raw.get("pestiUse")) or None,
        "dilution_or_dose": normalize_text(raw.get("dilutUnit")) or None,
        "preharvest_interval": normalize_text(raw.get("useSuittime"))
        or None,
        "max_use_count": normalize_text(raw.get("useNum")) or None,
        "wafindex": normalize_text(raw.get("wafindex")) or None,
        "source_service": LIST_SERVICE,
        "source_url": DEFAULT_ENDPOINT,
        "collected_at": now_iso(),
        "safety_tags": SAFETY_TAGS,
        "raw_record": raw,
    }


def canonical_name_variants(value: Any) -> set[str]:
    text = normalize_text(value)
    if not text:
        return set()
    variants = {normalized_name(text)}
    without_notes = re.sub(r"\([^)]*\)", "", text)
    if normalized_name(without_notes):
        variants.add(normalized_name(without_notes))
    for inner in re.findall(r"\(([^)]*)\)", text):
        for item in re.split(r"[,/·]|또는", inner):
            if normalized_name(item):
                variants.add(normalized_name(item))
    return {value for value in variants if value}


def load_ncpms_target_plants(ncpms_dir: Path) -> list[dict[str, Any]]:
    disease_path = ncpms_dir / "plant_disease_relations.jsonl"
    insect_path = ncpms_dir / "plant_insect_relations.jsonl"
    plant_path = ncpms_dir / "plant_codes.jsonl"
    for path in (disease_path, insect_path, plant_path):
        if not path.exists():
            raise FileNotFoundError(
                f"Required NCPMS input is missing: {path}. "
                "Run all_NCPMS_pipeline.py first."
            )

    relation_rows = [
        *read_jsonl(disease_path),
        *read_jsonl(insect_path),
    ]
    verified_plant_keys = {
        normalize_text(row.get("plant_key"))
        for row in relation_rows
        if normalize_text(row.get("plant_key"))
        and normalize_text(row.get("review_status")).lower() == "verified"
    }
    relation_names: dict[str, set[str]] = {}
    for row in relation_rows:
        plant_key = normalize_text(row.get("plant_key"))
        if plant_key not in verified_plant_keys:
            continue
        names = relation_names.setdefault(plant_key, set())
        for field in ("crop_name", "catalog_crop_name", "query_crop_name"):
            name = normalize_text(row.get(field))
            if name:
                names.add(name)

    targets: list[dict[str, Any]] = []
    for plant in read_jsonl(plant_path):
        plant_key = normalize_text(plant.get("plant_key"))
        if plant_key not in verified_plant_keys:
            continue
        crop_name = normalize_text(plant.get("crop_name"))
        source_names = set(relation_names.get(plant_key, set()))
        if crop_name:
            source_names.add(crop_name)
        raw_aliases = plant.get("aliases")
        if isinstance(raw_aliases, list):
            source_names.update(
                normalize_text(alias) for alias in raw_aliases if normalize_text(alias)
            )

        aliases: set[str] = set()
        for source_name in source_names:
            aliases.update(NCPMS_TO_PSIS_ALIASES.get(source_name, []))
            for variant in canonical_name_variants(source_name):
                if variant != normalized_name(source_name):
                    aliases.add(variant)

        targets.append(
            {
                "ncpms_plant_key": plant_key,
                "ncpms_crop_code": normalize_text(plant.get("crop_code")),
                "ncpms_crop_name": crop_name,
                "source_names": sorted(source_names),
                "search_aliases": sorted(
                    {normalize_text(alias) for alias in aliases if normalize_text(alias)}
                ),
                "has_disease": any(
                    normalize_text(row.get("plant_key")) == plant_key
                    and row.get("sick_key")
                    for row in relation_rows
                ),
                "has_insect": any(
                    normalize_text(row.get("plant_key")) == plant_key
                    and row.get("insect_key")
                    for row in relation_rows
                ),
            }
        )
    targets.sort(
        key=lambda row: (
            normalize_text(row.get("ncpms_crop_code")),
            normalize_text(row.get("ncpms_crop_name")),
        )
    )
    return targets


def load_supplement_targets(path: Path) -> list[dict[str, Any]]:
    """Load supplement crops (plants already in Supabase but absent from NCPMS
    disease/insect relations) using the same schema as load_ncpms_target_plants.

    Only ``source_names`` and true-synonym ``search_aliases`` are queried, so a
    match is trustworthy. Coarser parent-crop proxies (e.g. 한라봉→감귤) belong
    in ``review_aliases`` and are documented but never searched automatically,
    so parent-crop pesticide registrations are never silently trusted."""
    if not path.exists():
        raise FileNotFoundError(
            f"Supplement target file is missing: {path}. "
            "Generate it before using --already-exist-in-supabase."
        )
    targets: list[dict[str, Any]] = []
    for row in read_jsonl(path):
        crop_name = normalize_text(row.get("ncpms_crop_name"))
        if not crop_name:
            continue
        source_names = sorted(
            {
                normalize_text(value)
                for value in row.get("source_names", [])
                if normalize_text(value)
            }
            or {crop_name}
        )
        targets.append(
            {
                "ncpms_plant_key": normalize_text(row.get("ncpms_plant_key"))
                or f"supabase:{normalized_name(crop_name)}",
                "ncpms_crop_code": normalize_text(row.get("ncpms_crop_code")),
                "ncpms_crop_name": crop_name,
                "source_names": source_names,
                "search_aliases": sorted(
                    {
                        normalize_text(value)
                        for value in row.get("search_aliases", [])
                        if normalize_text(value)
                    }
                ),
                "review_aliases": sorted(
                    {
                        normalize_text(value)
                        for value in row.get("review_aliases", [])
                        if normalize_text(value)
                    }
                ),
                "has_disease": bool(row.get("has_disease")),
                "has_insect": bool(row.get("has_insect")),
                "target_origin": "supabase_supplement",
            }
        )
    return targets


def merge_targets(
    primary: list[dict[str, Any]], extra: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append ``extra`` targets to ``primary``, skipping duplicate crop names,
    then sort so resume checkpoints stay stable."""
    seen = {
        normalized_name(row.get("ncpms_crop_name"))
        for row in primary
        if normalized_name(row.get("ncpms_crop_name"))
    }
    merged = list(primary)
    for row in extra:
        key = normalized_name(row.get("ncpms_crop_name"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(row)
    merged.sort(
        key=lambda row: (
            normalize_text(row.get("ncpms_crop_code")),
            normalize_text(row.get("ncpms_crop_name")),
        )
    )
    return merged


def classify_psis_crop_name(
    response_name: str,
    target: dict[str, Any],
) -> tuple[str, str, float]:
    response_normalized = normalized_name(response_name)
    source_names = [
        normalize_text(value) for value in target.get("source_names", []) if value
    ]
    aliases = [
        normalize_text(value) for value in target.get("search_aliases", []) if value
    ]
    source_normalized = {normalized_name(value) for value in source_names}
    alias_normalized = {normalized_name(value) for value in aliases}

    if response_normalized in source_normalized:
        return "verified", "exact_name", 1.0
    if response_normalized in alias_normalized:
        return "verified", "alias_matched", 1.0

    response_variants = canonical_name_variants(response_name)
    target_variants: set[str] = set()
    for value in [*source_names, *aliases]:
        target_variants.update(canonical_name_variants(value))
    if response_variants.intersection(target_variants):
        return "verified", "normalized_variant", 0.95

    candidates = source_normalized | alias_normalized
    containment = any(
        min(len(response_normalized), len(candidate)) >= 2
        and (
            response_normalized in candidate
            or candidate in response_normalized
        )
        for candidate in candidates
    )
    best_similarity = max(
        (
            SequenceMatcher(None, response_normalized, candidate).ratio()
            for candidate in candidates
        ),
        default=0.0,
    )
    if containment:
        return "review_required", "name_containment", best_similarity
    if best_similarity >= 0.72:
        return "review_required", "fuzzy_name", best_similarity
    return "rejected", "unrelated_name", best_similarity


def paged_list_query(
    client: PsisClient,
    args: argparse.Namespace,
    params: dict[str, Any],
    *,
    context: str,
) -> tuple[list[dict[str, Any]], int | None]:
    rows: list[dict[str, Any]] = []
    total_count: int | None = None
    previous_fingerprint = ""
    for page in range(1, args.max_pages + 1):
        progress(f"[PSIS NCPMS search] {context} page={page}")
        payload = client.request(
            LIST_SERVICE,
            {
                **params,
                "displayCount": args.page_size,
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
        if len(page_rows) < args.page_size:
            break
    return rows, total_count


def collect_ncpms_scoped_list(
    client: PsisClient,
    args: argparse.Namespace,
    errors: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    targets = (
        load_ncpms_target_plants(args.ncpms_dir) if args.ncpms_only else []
    )
    if args.already_exist_in_supabase:
        targets = merge_targets(
            targets, load_supplement_targets(args.supabase_supplement_file)
        )
    write_jsonl(args.output_dir / NCPMS_TARGET_PATH, targets)

    accepted_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    if args.resume:
        accepted_rows = read_jsonl(args.output_dir / LIST_PATH)
        mapping_rows = read_jsonl(args.output_dir / NCPMS_MAPPING_PATH)
        review_rows = read_jsonl(args.output_dir / NCPMS_MAPPING_REVIEW_PATH)

    for target_index, target in enumerate(targets, 1):
        target_name = normalize_text(target.get("ncpms_crop_name"))
        source_names = [
            normalize_text(value)
            for value in target.get("source_names", [])
            if normalize_text(value)
        ]
        aliases = [
            normalize_text(value)
            for value in target.get("search_aliases", [])
            if normalize_text(value)
        ]
        search_names = list(dict.fromkeys([*source_names, *aliases]))
        if not search_names:
            continue

        target_had_exact_rows = False
        query_specs: list[tuple[str, str]] = [
            (search_name, "Y") for search_name in search_names
        ]
        seen_queries: set[tuple[str, str]] = set()
        target_results: list[tuple[dict[str, Any], str, str]] = []

        for search_name, crop_check in query_specs:
            query_key = (normalized_name(search_name), crop_check)
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            try:
                raw_rows, _ = paged_list_query(
                    client,
                    args,
                    {"cropName": search_name, "cropCheck": crop_check},
                    context=(
                        f"{target_index}/{len(targets)} "
                        f"{target_name} exact={search_name}"
                    ),
                )
                if raw_rows:
                    target_had_exact_rows = True
                target_results.extend(
                    (raw, search_name, crop_check) for raw in raw_rows
                )
            except Exception as exc:
                errors.append(
                    {
                        "stage": "ncpms_crop_search",
                        "ncpms_plant_key": target.get("ncpms_plant_key"),
                        "ncpms_crop_name": target_name,
                        "search_name": search_name,
                        "crop_check": crop_check,
                        "error": str(exc),
                        "collected_at": now_iso(),
                    }
                )
                write_json(args.output_dir / ERROR_PATH, errors)

        use_loose = args.ncpms_discovery == "always" or (
            args.ncpms_discovery == "fallback" and not target_had_exact_rows
        )
        if use_loose:
            # Loose discovery uses the official/source names. Aliases were
            # already searched exactly and broad alias expansion can introduce
            # excessive unrelated crops.
            for search_name in source_names:
                query_key = (normalized_name(search_name), "N")
                if query_key in seen_queries:
                    continue
                seen_queries.add(query_key)
                try:
                    raw_rows, _ = paged_list_query(
                        client,
                        args,
                        {"cropName": search_name, "cropCheck": "N"},
                        context=(
                            f"{target_index}/{len(targets)} "
                            f"{target_name} loose={search_name}"
                        ),
                    )
                    target_results.extend(
                        (raw, search_name, "N") for raw in raw_rows
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "stage": "ncpms_crop_discovery",
                            "ncpms_plant_key": target.get("ncpms_plant_key"),
                            "ncpms_crop_name": target_name,
                            "search_name": search_name,
                            "crop_check": "N",
                            "error": str(exc),
                            "collected_at": now_iso(),
                        }
                    )
                    write_json(args.output_dir / ERROR_PATH, errors)

        target_seen_rows: set[tuple[str, str, str]] = set()
        mapping_counts: dict[tuple[str, str, str, str, str], int] = {}
        for raw, search_name, crop_check in target_results:
            row = normalized_list_row(raw)
            response_crop_name = normalize_text(row.get("crop_name"))
            response_crop_code = normalize_text(row.get("crop_code"))
            status, method, score = classify_psis_crop_name(
                response_crop_name, target
            )
            row_key = (
                normalize_text(row.get("pesti_code")),
                normalize_text(row.get("disease_use_seq")),
                response_crop_code or normalized_name(response_crop_name),
            )
            if row_key in target_seen_rows:
                continue
            target_seen_rows.add(row_key)

            mapping_key = (
                response_crop_code,
                response_crop_name,
                status,
                method,
                search_name,
            )
            mapping_counts[mapping_key] = mapping_counts.get(mapping_key, 0) + 1
            scoped = {
                **row,
                "ncpms_plant_key": target.get("ncpms_plant_key"),
                "ncpms_crop_code": target.get("ncpms_crop_code"),
                "ncpms_crop_name": target_name,
                "crop_match_status": status,
                "crop_match_method": method,
                "crop_match_score": round(score, 4),
                "query_crop_name": search_name,
                "query_crop_check": crop_check,
            }
            if status == "verified":
                accepted_rows.append(scoped)
            elif status == "review_required":
                review_rows.append(scoped)

        for (
            psis_crop_code,
            psis_crop_name,
            status,
            method,
            search_name,
        ), registration_count in mapping_counts.items():
            mapping = {
                "ncpms_plant_key": target.get("ncpms_plant_key"),
                "ncpms_crop_code": target.get("ncpms_crop_code"),
                "ncpms_crop_name": target_name,
                "psis_crop_code": psis_crop_code or None,
                "psis_crop_name": psis_crop_name or None,
                "match_status": status,
                "match_method": method,
                "query_crop_name": search_name,
                "registration_count": registration_count,
                "collected_at": now_iso(),
            }
            if status == "verified":
                mapping_rows.append(mapping)
            elif status == "review_required":
                # The review registration rows contain the actual pesticide
                # records; this compact mapping entry makes review easier.
                mapping["record_type"] = "mapping_summary"
                review_rows.append(mapping)

        accepted_rows = dedupe(
            accepted_rows,
            ("ncpms_plant_key", "pesti_code", "disease_use_seq"),
        )
        mapping_rows = dedupe(
            mapping_rows,
            (
                "ncpms_plant_key",
                "psis_crop_code",
                "psis_crop_name",
                "match_method",
            ),
        )
        review_rows = dedupe(
            review_rows,
            (
                "ncpms_plant_key",
                "pesti_code",
                "disease_use_seq",
                "psis_crop_code",
                "psis_crop_name",
                "record_type",
            ),
        )
        write_jsonl(args.output_dir / LIST_PATH, accepted_rows)
        write_jsonl(args.output_dir / NCPMS_MAPPING_PATH, mapping_rows)
        write_jsonl(
            args.output_dir / NCPMS_MAPPING_REVIEW_PATH,
            review_rows,
        )

        if args.limit_records and len(accepted_rows) >= args.limit_records:
            accepted_rows = accepted_rows[: args.limit_records]
            write_jsonl(args.output_dir / LIST_PATH, accepted_rows)
            break

    return accepted_rows, targets, mapping_rows, review_rows


def collect_list(
    client: PsisClient,
    args: argparse.Namespace,
    filters: dict[str, str],
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int | None, int]:
    output_path = args.output_dir / LIST_PATH
    rows = read_jsonl(output_path) if args.resume else []
    rows = dedupe(rows, ("pesti_code", "disease_use_seq"))
    # Checkpoints are written only after complete pages, so this calculation is
    # safe for normal resumes. Page 1 is the first page in the PSIS manual.
    start_page = len(rows) // args.page_size + 1 if rows else 1
    total_count: int | None = None
    pages_collected = max(0, start_page - 1)

    if args.limit_records and len(rows) >= args.limit_records:
        return rows[: args.limit_records], len(rows), pages_collected

    for page in range(start_page, args.max_pages + 1):
        params: dict[str, Any] = {
            **filters,
            "displayCount": args.page_size,
            "startPoint": page,
        }
        progress(f"[PSIS SVC01 page {page}] collected={len(rows)}")
        try:
            payload = client.request(LIST_SERVICE, params)
            page_rows = extract_list_items(payload)
            total_text = find_first(payload, "totalCount")
            if total_text:
                try:
                    total_count = int(total_text.replace(",", ""))
                except ValueError:
                    pass
        except Exception as exc:
            errors.append(
                {
                    "stage": "list",
                    "page": page,
                    "error": str(exc),
                    "collected_at": now_iso(),
                }
            )
            write_json(args.output_dir / ERROR_PATH, errors)
            raise

        if not page_rows:
            break

        collected_at = now_iso()
        for raw in page_rows:
            row = normalized_list_row(raw)
            row["collected_at"] = collected_at
            rows.append(row)

        rows = dedupe(rows, ("pesti_code", "disease_use_seq"))
        if args.limit_records and len(rows) >= args.limit_records:
            rows = rows[: args.limit_records]
        write_jsonl(output_path, rows)
        pages_collected = page

        if args.limit_records and len(rows) >= args.limit_records:
            break
        if total_count is not None and len(rows) >= total_count:
            break
        if len(page_rows) < args.page_size:
            break

    return rows, total_count, pages_collected


def build_crop_outputs(
    rows: list[dict[str, Any]], output_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    crop_groups: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    for row in rows:
        crop_code = normalize_text(row.get("crop_code"))
        crop_name = normalize_text(row.get("crop_name"))
        if crop_code:
            plant_key = f"psis:{crop_code}"
            crop_key_method = "psis_crop_code"
        elif crop_name:
            plant_key = f"psis-name:{normalized_name(crop_name)}"
            crop_key_method = "normalized_crop_name"
        else:
            plant_key = ""
            crop_key_method = "missing"

        if plant_key:
            current = crop_groups.setdefault(
                plant_key,
                {
                    "plant_key": plant_key,
                    "crop_code": crop_code or None,
                    "crop_name": crop_name or None,
                    "crop_names": [],
                    "crop_large_class_code": row.get(
                        "crop_large_class_code"
                    ),
                    "crop_large_class_name": row.get(
                        "crop_large_class_name"
                    ),
                    "ncpms_plants": [],
                    "key_method": crop_key_method,
                    "source_service": LIST_SERVICE,
                },
            )
            names = current["crop_names"]
            if crop_name and crop_name not in names:
                names.append(crop_name)
            ncpms_plant_key = normalize_text(row.get("ncpms_plant_key"))
            if ncpms_plant_key:
                ncpms_entry = {
                    "plant_key": ncpms_plant_key,
                    "crop_code": row.get("ncpms_crop_code"),
                    "crop_name": row.get("ncpms_crop_name"),
                    "match_method": row.get("crop_match_method"),
                }
                if ncpms_entry not in current["ncpms_plants"]:
                    current["ncpms_plants"].append(ncpms_entry)

        relations.append(
            {
                "plant_key": plant_key or None,
                "crop_code": crop_code or None,
                "crop_name": crop_name or None,
                "crop_key_method": crop_key_method,
                "ncpms_plant_key": row.get("ncpms_plant_key"),
                "ncpms_crop_code": row.get("ncpms_crop_code"),
                "ncpms_crop_name": row.get("ncpms_crop_name"),
                "crop_match_status": row.get("crop_match_status"),
                "crop_match_method": row.get("crop_match_method"),
                "pesti_code": row.get("pesti_code"),
                "disease_use_seq": row.get("disease_use_seq"),
                "target_disease_pest": row.get("target_disease_pest"),
                "pesticide_name": row.get("pesticide_name"),
                "brand_name": row.get("brand_name"),
                "source_service": LIST_SERVICE,
                "collected_at": row.get("collected_at"),
                "safety_tags": SAFETY_TAGS,
            }
        )

    crops = list(crop_groups.values())
    for crop in crops:
        crop["crop_names"] = sorted(crop["crop_names"])
    crops.sort(
        key=lambda row: (
            normalize_text(row.get("crop_code")),
            normalize_text(row.get("crop_name")),
        )
    )
    relations = dedupe(
        relations,
        ("ncpms_plant_key", "pesti_code", "disease_use_seq"),
    )
    write_jsonl(output_dir / CROP_PATH, crops)
    write_jsonl(output_dir / RELATION_PATH, relations)
    return crops, relations


def collect_details(
    client: PsisClient,
    args: argparse.Namespace,
    list_rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output_path = args.output_dir / DETAIL_PATH
    details_scope = args.details_scope or (
        "product" if args.details_only else "registration"
    )
    # details-only is an incremental enrichment operation. Never discard an
    # existing detail file merely because --resume was accidentally omitted.
    details = (
        read_jsonl(output_path)
        if (args.resume or args.details_only) and output_path.exists()
        else []
    )
    details = dedupe(details, ("pesti_code", "disease_use_seq"))
    completed_registration_keys = {
        (
            normalize_text(row.get("pesti_code")),
            normalize_text(row.get("disease_use_seq")),
        )
        for row in details
    }
    completed_product_codes = {
        normalize_text(row.get("pesti_code"))
        for row in details
        if row.get("pesti_code")
    }
    candidate_rows = [
        {
            "pesti_code": normalize_text(row.get("pesti_code")),
            "disease_use_seq": normalize_text(row.get("disease_use_seq")),
        }
        for row in list_rows
        if row.get("pesti_code") and row.get("disease_use_seq")
    ]
    dedupe_fields = (
        ("pesti_code",)
        if details_scope == "product"
        else ("pesti_code", "disease_use_seq")
    )
    keys = dedupe(candidate_rows, dedupe_fields)
    keys = [
        (
            normalize_text(row.get("pesti_code")),
            normalize_text(row.get("disease_use_seq")),
        )
        for row in keys
    ]

    for index, (pesti_code, disease_use_seq) in enumerate(keys, 1):
        if details_scope == "product":
            already_completed = pesti_code in completed_product_codes
        else:
            already_completed = (
                pesti_code,
                disease_use_seq,
            ) in completed_registration_keys
        if already_completed:
            continue
        progress(
            f"[PSIS SVC02 {index}/{len(keys)}] "
            f"pestiCode={pesti_code} diseaseUseSeq={disease_use_seq}"
        )
        try:
            payload = client.request(
                DETAIL_SERVICE,
                {
                    "pestiCode": pesti_code,
                    "diseaseUseSeq": disease_use_seq,
                },
            )
            raw = extract_detail(payload)
            details.append(
                {
                    "pesti_code": pesti_code,
                    "disease_use_seq": disease_use_seq,
                    "pesticide_name": normalize_text(
                        raw.get("pestiKorName")
                    )
                    or None,
                    "use_type": normalize_text(raw.get("useName")) or None,
                    "company_name": normalize_text(raw.get("compName"))
                    or None,
                    "brand_name": normalize_text(
                        raw.get("pestiBrandName")
                    )
                    or None,
                    "active_ingredient": normalize_text(
                        raw.get("pestiEngName")
                    )
                    or None,
                    "active_ingredient_amount": normalize_text(
                        raw.get("regCpntQnty")
                    )
                    or None,
                    "toxicity_code": normalize_text(
                        raw.get("toxicGubun")
                    )
                    or None,
                    "toxicity_name": normalize_text(
                        raw.get("toxicName")
                    )
                    or None,
                    "fish_toxicity": normalize_text(
                        raw.get("fishToxicGubun")
                    )
                    or None,
                    "crop_name": normalize_text(raw.get("cropName"))
                    or None,
                    "target_disease_pest": normalize_text(
                        raw.get("diseaseWeedName")
                    )
                    or None,
                    "application_timing": normalize_text(
                        raw.get("pestiUse")
                    )
                    or None,
                    "dilution_or_dose": normalize_text(
                        raw.get("dilutUnit")
                    )
                    or None,
                    "preharvest_interval": normalize_text(
                        raw.get("useSuittime")
                    )
                    or None,
                    "max_use_count": normalize_text(raw.get("useNum"))
                    or None,
                    "source_service": DETAIL_SERVICE,
                    "source_url": DEFAULT_ENDPOINT,
                    "collected_at": now_iso(),
                    "usage_scope": "safety_reference_only",
                    "safety_tags": SAFETY_TAGS,
                    "raw_record": raw,
                }
            )
            completed_registration_keys.add((pesti_code, disease_use_seq))
            completed_product_codes.add(pesti_code)
            # A full run can take hours. Persist every successful detail so
            # Ctrl+C or a transient failure loses at most the active request.
            write_jsonl(output_path, details)
        except KeyboardInterrupt:
            write_jsonl(output_path, details)
            raise
        except Exception as exc:
            errors.append(
                {
                    "stage": "detail",
                    "pesti_code": pesti_code,
                    "disease_use_seq": disease_use_seq,
                    "error": str(exc),
                    "collected_at": now_iso(),
                }
            )
            write_json(args.output_dir / ERROR_PATH, errors)

    details = dedupe(details, ("pesti_code", "disease_use_seq"))
    write_jsonl(output_path, details)
    return details


def main() -> int:
    args = parse_args()
    if args.list_only and args.details_only:
        raise ValueError("--list-only and --details-only cannot be used together.")
    if args.page_size < 1 or args.page_size > 99:
        raise ValueError(
            "--page-size must be between 1 and 99 "
            "(the PSIS manual defines a two-digit field)."
        )
    if args.max_pages < 1:
        raise ValueError("--max-pages must be positive.")
    if args.limit_records is not None and args.limit_records < 1:
        raise ValueError("--limit-records must be positive.")

    filters = build_list_filters(args)
    details_input = args.details_input or (args.output_dir / LIST_PATH)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "endpoint": args.endpoint,
                    "service_type": args.service_type,
                    "list_service": LIST_SERVICE,
                    "detail_service": DETAIL_SERVICE,
                    "page_size": args.page_size,
                    "filters": filters,
                    "output_dir": str(args.output_dir),
                    "list_only": args.list_only,
                    "details_only": args.details_only,
                    "details_scope": args.details_scope
                    or ("product" if args.details_only else "registration"),
                    "details_input": str(details_input),
                    "details_output": str(args.output_dir / DETAIL_PATH),
                    "resume": args.resume,
                    "ncpms_only": args.ncpms_only,
                    "ncpms_dir": str(args.ncpms_dir),
                    "ncpms_discovery": args.ncpms_discovery,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    env = load_env()
    api_key = args.api_key or env.get("PSIS_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "PSIS_API_KEY is required. Put it in the repository root .env; "
            "do not commit or print it."
        )

    client = PsisClient(
        api_key=api_key,
        endpoint=args.endpoint,
        service_type=args.service_type,
        timeout=args.timeout,
        delay=args.delay,
        retries=args.retries,
    )
    errors = (
        json.loads((args.output_dir / ERROR_PATH).read_text(encoding="utf-8"))
        if args.resume and (args.output_dir / ERROR_PATH).exists()
        else []
    )
    if not isinstance(errors, list):
        errors = []

    if args.details_only:
        if not details_input.exists():
            raise FileNotFoundError(
                f"SVC01 input for --details-only does not exist: {details_input}"
            )
        list_rows = read_jsonl(details_input)
        if not list_rows:
            raise ValueError(
                f"SVC01 input for --details-only is empty: {details_input}"
            )
        details = collect_details(client, args, list_rows, errors)
        unique_keys = {
            (
                normalize_text(row.get("pesti_code")),
                normalize_text(row.get("disease_use_seq")),
            )
            for row in list_rows
            if row.get("pesti_code") and row.get("disease_use_seq")
        }
        unique_product_codes = {
            normalize_text(row.get("pesti_code"))
            for row in list_rows
            if row.get("pesti_code")
        }
        collected_product_codes = {
            normalize_text(row.get("pesti_code"))
            for row in details
            if row.get("pesti_code")
        }
        details_scope = args.details_scope or "product"
        target_count = (
            len(unique_product_codes)
            if details_scope == "product"
            else len(unique_keys)
        )
        completed_count = (
            len(collected_product_codes)
            if details_scope == "product"
            else len(
                {
                    (
                        normalize_text(row.get("pesti_code")),
                        normalize_text(row.get("disease_use_seq")),
                    )
                    for row in details
                    if row.get("pesti_code")
                    and row.get("disease_use_seq")
                }
            )
        )
        detail_summary = {
            "collected_at": now_iso(),
            "endpoint": args.endpoint,
            "detail_service": DETAIL_SERVICE,
            "details_only": True,
            "details_input": str(details_input),
            "details_output": str(args.output_dir / DETAIL_PATH),
            "details_scope": details_scope,
            "input_records": len(list_rows),
            "unique_registration_keys": len(unique_keys),
            "unique_product_codes": len(unique_product_codes),
            "detail_rows": len(details),
            "completed_targets": completed_count,
            "missing_details": max(target_count - completed_count, 0),
            "errors": len(errors),
        }
        write_json(args.output_dir / ERROR_PATH, errors)
        write_json(
            args.output_dir / DETAIL_SUMMARY_PATH,
            detail_summary,
        )
        progress(
            f"[PSIS SVC02] input={len(list_rows)} "
            f"scope={details_scope} targets={target_count} "
            f"completed={completed_count} "
            f"missing={detail_summary['missing_details']} errors={len(errors)}"
        )
        return 0

    ncpms_targets: list[dict[str, Any]] = []
    ncpms_mappings: list[dict[str, Any]] = []
    ncpms_mapping_reviews: list[dict[str, Any]] = []
    if args.ncpms_only or args.already_exist_in_supabase:
        (
            list_rows,
            ncpms_targets,
            ncpms_mappings,
            ncpms_mapping_reviews,
        ) = collect_ncpms_scoped_list(client, args, errors)
        total_count = None
        pages_collected = None
    else:
        list_rows, total_count, pages_collected = collect_list(
            client, args, filters, errors
        )
    crops, relations = build_crop_outputs(list_rows, args.output_dir)
    details: list[dict[str, Any]] = []
    if not args.list_only:
        details = collect_details(client, args, list_rows, errors)

    summary = {
        "collected_at": now_iso(),
        "endpoint": args.endpoint,
        "list_service": LIST_SERVICE,
        "detail_service": DETAIL_SERVICE,
        "filters": filters,
        "reported_total_count": total_count,
        "pages_collected": pages_collected,
        "list_records": len(list_rows),
        "unique_registration_keys": len(
            {
                (row.get("pesti_code"), row.get("disease_use_seq"))
                for row in list_rows
            }
        ),
        "unique_pesti_codes": len(
            {row.get("pesti_code") for row in list_rows if row.get("pesti_code")}
        ),
        "unique_crop_keys": len(crops),
        "relations": len(relations),
        "details": len(details),
        "list_only": args.list_only,
        "ncpms_only": args.ncpms_only,
        "ncpms_target_plants": len(ncpms_targets),
        "ncpms_plants_matched": len(
            {
                row.get("ncpms_plant_key")
                for row in ncpms_mappings
                if row.get("ncpms_plant_key")
            }
        ),
        "ncpms_crop_mappings": len(ncpms_mappings),
        "ncpms_mapping_review_records": len(ncpms_mapping_reviews),
        "errors": len(errors),
        "safety_notice": (
            "농약 사용 전 PSIS의 최신 등록 여부, 제품 라벨 및 "
            "농약안전사용기준을 다시 확인해야 합니다."
        ),
    }
    write_json(args.output_dir / ERROR_PATH, errors)
    write_json(args.output_dir / SUMMARY_PATH, summary)
    progress(
        f"[PSIS] list={len(list_rows)} crops={len(crops)} "
        f"relations={len(relations)} details={len(details)} errors={len(errors)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
