from app.services.rag.nodes_context import garden_member_label, summarize_user_context


def test_garden_member_label_combines_nickname_and_species_once():
    assert garden_member_label({"name": "김고구", "species": "고구마"}) == "김고구 (고구마)"
    assert garden_member_label({"name": "고구마", "species": "고구마"}) == "고구마"


def test_garden_context_does_not_repeat_representative_crop_when_member_exists():
    result = summarize_user_context({
        "plant_data": {
            "name": "고구마 텃밭",
            "context_type": "garden",
            "cultivation_type": "single",
            "representative_crop": "고구마",
            "member_plants": [{"id": "plant-1", "name": "김고구", "species": "고구마"}],
        },
        "care_logs": [],
    })

    assert "구성 식물: 김고구 (고구마)" in result["user_context"]
    assert "대표 작물:" not in result["user_context"]


def test_empty_garden_context_uses_representative_crop():
    result = summarize_user_context({
        "plant_data": {
            "name": "고구마 텃밭",
            "context_type": "garden",
            "cultivation_type": "single",
            "representative_crop": "고구마",
            "member_plants": [],
        },
        "care_logs": [],
    })

    assert "대표 작물: 고구마" in result["user_context"]
    assert "구성 식물: 등록된 식물 없음" in result["user_context"]
