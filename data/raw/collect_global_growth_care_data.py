"""
PlantSolve Static JSON API & Trefle API 194종 완벽 학명 보완 수집 스크립트
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# .env 로드
env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from data.scripts.common import RAW_DIR, write_jsonl  # noqa: E402
from data.scripts.collect_watering_care_data import PLANTS_170

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# 학명 미지정 한글 식물 정밀 영문/학명 매핑 맵
SPECIFIC_SCI_OVERRIDE = {
    "005": {"scientific": "Dracaena sanderiana", "name_en": "Lucky Bamboo"},
    "010": {"scientific": "Cupressus macrocarpa", "name_en": "Goldcrest Cypress"},
    "032": {"scientific": "Mentha", "name_en": "Mint"},
    "033": {"scientific": "Ocimum basilicum", "name_en": "Basil"},
    "046": {"scientific": "Dracaena trifasciata", "name_en": "Snake Plant"},
    "048": {"scientific": "Cactaceae", "name_en": "Cactus"},
    "054": {"scientific": "Dracaena stuckyi", "name_en": "Sansevieria stuckyi"},
    "055": {"scientific": "Spathiphyllum wallisii", "name_en": "Peace Lily"},
    "085": {"scientific": "Euphorbia pulcherrima", "name_en": "Poinsettia"},
    "146": {"scientific": "Lithops", "name_en": "Living Stone"},
    "187": {"scientific": "Aegagropila linnaei", "name_en": "Marimo Moss Ball"},
    "194": {"scientific": "Agave nana", "name_en": "Dwarf Agave"}
}

def http_get_json(url: str, retries: int = 3) -> dict | list | None:
    for attempt in range(retries):
        try:
            req = Request(url, headers=DEFAULT_HEADERS)
            with urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if "429" in str(e):
                time.sleep(2 * (attempt + 1))
            else:
                break
    return None

def get_all_194_plants() -> list[dict[str, str]]:
    md_file = REPO_ROOT / "docs" / "master_plant_catalog_194.md"
    plants_dict = {}

    for p in PLANTS_170:
        pid = p["id"].zfill(3)
        plants_dict[pid] = {
            "id": pid,
            "name_ko": p["name_ko"],
            "name_en": p["name_en"],
            "scientific": p["scientific"],
        }

    if md_file.exists():
        with open(md_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("|") and len(line.split("|")) >= 4:
                    cols = [c.strip() for c in line.split("|")]
                    num_str = cols[1].strip()
                    if num_str.isdigit():
                        pid = num_str.zfill(3)
                        raw_name = cols[2].replace("**", "").strip()
                        base_name = raw_name.split("(")[0].strip()
                        sci_name = ""
                        if len(cols) >= 5 and cols[3].startswith("*"):
                            sci_name = cols[3].replace("*", "").strip()

                        if pid not in plants_dict:
                            plants_dict[pid] = {
                                "id": pid,
                                "name_ko": base_name,
                                "name_en": base_name,
                                "scientific": sci_name,
                            }
                        else:
                            if sci_name:
                                plants_dict[pid]["scientific"] = sci_name

    # Override 매핑 적용
    for pid, override in SPECIFIC_SCI_OVERRIDE.items():
        if pid in plants_dict:
            plants_dict[pid]["scientific"] = override["scientific"]
            plants_dict[pid]["name_en"] = override["name_en"]

    return [plants_dict[str(i).zfill(3)] for i in range(1, 195) if str(i).zfill(3) in plants_dict]

class PlantSolveClient:
    INDEX_URL = "https://www.plantsolve.com/api/v1/plants/index.json"
    DETAIL_BASE = "https://www.plantsolve.com/api/v1/plants"

    def __init__(self):
        print("[PlantSolve] Index 다운로드 중...")
        self.index_data = http_get_json(self.INDEX_URL) or []
        print(f"[PlantSolve] 색인 완료! 총 {len(self.index_data)}개 식물 DB 등록됨.")

    def find_slug(self, sci_name: str, name_en: str) -> str | None:
        if not self.index_data:
            return None
        
        sci_clean = sci_name.lower().strip() if sci_name else ""
        en_clean = name_en.lower().strip().replace(" ", "-") if name_en else ""

        # 1) 학명 일치
        if sci_clean and len(sci_clean) > 3:
            genus = sci_clean.split()[0]
            for item in self.index_data:
                item_sci = (item.get("scientificName") or "").lower()
                if sci_clean == item_sci or (genus and len(genus) > 3 and genus in item_sci):
                    return item.get("slug")

        # 2) slug 일치
        if en_clean and len(en_clean) > 3:
            for item in self.index_data:
                slug = (item.get("slug") or "").lower()
                if en_clean == slug or slug.startswith(en_clean) or en_clean.startswith(slug):
                    return item.get("slug")

        return None

    def fetch_growth_data(self, sci_name: str, name_en: str) -> dict | None:
        slug = self.find_slug(sci_name, name_en)
        if not slug:
            return None

        detail_url = f"{self.DETAIL_BASE}/{slug}.json"
        data = http_get_json(detail_url)
        if data and isinstance(data, dict):
            care = data.get("care")
            lighting = data.get("lighting")
            params = data.get("parameters")
            if not care and not lighting and not params:
                return None

            return {
                "plantsolve_id": data.get("id"),
                "slug": data.get("slug"),
                "seo_title": data.get("seoTitle"),
                "common_name": data.get("commonName"),
                "scientific_name": data.get("scientificName"),
                "care": care,
                "lighting": lighting,
                "parameters": params,
                "growthCharacteristics": data.get("growthCharacteristics"),
                "troubleshooting": data.get("troubleshooting"),
                "summary": data.get("summary")
            }
        return None

class TrefleClient:
    BASE_URL = "https://trefle.io/api/v1"

    def __init__(self):
        self.token = os.getenv("TREFLE_API_TOKEN", "")

    def fetch_growth_data(self, query: str) -> dict | None:
        if not self.token or not query or len(query) < 3:
            return None
        search_url = f"{self.BASE_URL}/plants/search?token={self.token}&q={quote(query)}"
        data = http_get_json(search_url)
        if not data or not isinstance(data, dict) or not data.get("data"):
            return None

        first_match = data["data"][0]
        plant_id = first_match.get("id")
        if not plant_id:
            return None

        detail_url = f"{self.BASE_URL}/plants/{plant_id}?token={self.token}"
        detail_data = http_get_json(detail_url)
        if detail_data and isinstance(detail_data, dict) and detail_data.get("data"):
            p_data = detail_data["data"]
            growth = p_data.get("growth") or {}
            specifications = p_data.get("specifications") or {}
            
            return {
                "trefle_id": plant_id,
                "common_name": p_data.get("common_name"),
                "scientific_name": p_data.get("scientific_name"),
                "family": p_data.get("family"),
                "growth": {
                    "light": growth.get("light"),
                    "atmospheric_humidity": growth.get("atmospheric_humidity"),
                    "moisture_use": growth.get("moisture_use"),
                    "minimum_temperature": growth.get("minimum_temperature"),
                    "maximum_temperature": growth.get("maximum_temperature"),
                    "ph_maximum": growth.get("ph_maximum"),
                    "ph_minimum": growth.get("ph_minimum"),
                },
                "specifications": {
                    "growth_form": specifications.get("growth_form"),
                    "growth_habit": specifications.get("growth_habit"),
                    "growth_rate": specifications.get("growth_rate"),
                    "average_height": specifications.get("average_height"),
                    "toxicity": specifications.get("toxicity"),
                }
            }
        return None

def main():
    parser = argparse.ArgumentParser(description="Trefle & PlantSolve 194종 완벽 생장 데이터 수집")
    parser.add_argument("--dry-run", action="store_true", help="처음 5건만 테스트")
    parser.add_argument("--output", default=str(RAW_DIR / "global_plant_growth_care_194plants.jsonl"))
    args = parser.parse_args()

    plants = get_all_194_plants()
    if args.dry_run:
        plants = plants[:5]

    trefle_client = TrefleClient()
    plantsolve_client = PlantSolveClient()

    print(f"\n[글로벌 생장 데이터 완벽 수집 시작] 총 {len(plants)}종 (dry_run={args.dry_run})")
    print(f"[출력 파일] {args.output}\n")

    results = []
    trefle_success = 0
    plantsolve_success = 0

    for idx, p in enumerate(plants, 1):
        name_ko = p["name_ko"]
        sci = p["scientific"]
        name_en = p.get("name_en", "")
        print(f"[{idx:03d}/{len(plants):03d}] {name_ko} (학명: {sci})...", end=" ", flush=True)

        trefle_data = None
        plantsolve_data = None

        # 1) Trefle 조회
        if sci:
            trefle_data = trefle_client.fetch_growth_data(sci)
        if not trefle_data and name_en:
            trefle_data = trefle_client.fetch_growth_data(name_en)

        if trefle_data:
            trefle_success += 1

        # 2) PlantSolve 조회
        plantsolve_data = plantsolve_client.fetch_growth_data(sci, name_en)
        if plantsolve_data:
            plantsolve_success += 1

        status_str = f"Trefle={'[OK]' if trefle_data else '[--]'} | PlantSolve={'[OK]' if plantsolve_data else '[--]'}"
        print(status_str)

        combined_record = {
            "doc_id": f"global_growth:{p['id']}",
            "crop_or_plant": [name_ko],
            "scientific_name": sci,
            "category": "growth_care",
            "source_id": "trefle_plantsolve_api",
            "publisher": "Trefle.io & PlantSolve Global",
            "trefle_data": trefle_data,
            "plantsolve_data": plantsolve_data,
            "collected_at": str(date.today()),
            "license": "Trefle & PlantSolve Open API License",
            "url": "https://trefle.io / https://www.plantsolve.com",
            "usage_scope": "rag"
        }
        results.append(combined_record)
        time.sleep(0.15)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = write_jsonl(out_path, results)

    print(f"\n{'='*65}")
    print(f"  Trefle & PlantSolve 생장 데이터 수집 완료")
    print(f"{'='*65}")
    print(f"  전체 수집: {count}종")
    print(f"  Trefle 데이터 확보: {trefle_success}종")
    print(f"  PlantSolve 데이터 확보: {plantsolve_success}종")
    print(f"  출력 경로: {args.output}")
    print(f"{'='*65}")

if __name__ == "__main__":
    main()
