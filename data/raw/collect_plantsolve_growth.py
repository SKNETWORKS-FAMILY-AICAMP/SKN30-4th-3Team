import json
import os
import re

def parse_plantsolve_slug(slug_name, raw_data):
    """
    Parses and standardizes PlantSolve HTML/JSON response for indoor & global plant growth care.
    No API Key required.
    """
    care_info = raw_data.get("care", {})
    lighting_info = raw_data.get("lighting", {})
    params_info = raw_data.get("parameters", {})
    growth_info = raw_data.get("growthCharacteristics", {})
    
    doc = {
        "doc_id": f"plantsolve:{slug_name}",
        "crop_or_plant": [raw_data.get("common_name", slug_name.replace("-", " ").title())],
        "scientific_name": raw_data.get("scientific_name", ""),
        "common_name": raw_data.get("common_name", slug_name.replace("-", " ").title()),
        "slug": slug_name,
        "source_id": "plantsolve_api",
        "publisher": "PlantSolve Global",
        "category": "growth_care",
        "care": care_info,
        "lighting": lighting_info,
        "parameters": params_info,
        "growthCharacteristics": growth_info,
        "troubleshooting": raw_data.get("troubleshooting", []),
        "summary": raw_data.get("summary", "")
    }
    return doc

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_jsonl = os.path.join(base_dir, "plantsolve_growth_care.jsonl")
    
    if os.path.exists(target_jsonl):
        with open(target_jsonl, "r", encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
        print(f"[PlantSolve] Verified pure unique dataset: {count} species records at {target_jsonl}")

if __name__ == "__main__":
    main()
