import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.auth.security import get_current_user
from app.db.session import get_supabase_client

# 테스트용 고정 사용자 UUID
TEST_USER_ID = uuid.UUID("d3b07384-d113-49c3-a558-1ec114a84d41")

# Mock Supabase API 응답 클래스
class MockAPIResponse:
    def __init__(self, data):
        self.data = data

# Mock Supabase 테이블 조회/삽입 헬퍼 클래스
class MockSupabaseTable:
    def __init__(self, name):
        self.name = name
        self.queries = []

    def select(self, *args, **kwargs):
        if self.name == "chat_messages" and args and "sender" in str(args[0]):
            raise RuntimeError("column chat_messages.sender does not exist")
        self.queries.append(("select", args, kwargs))
        return self

    def eq(self, field, value):
        self.queries.append(("eq", field, value))
        return self

    def insert(self, data):
        if self.name == "plants":
            assert "health_score" in data
            assert data["health_score"] is None
        self.queries.append(("insert", data))
        return self

    def upsert(self, data, **kwargs):
        self.queries.append(("upsert", data, kwargs))
        return self

    def or_(self, filter_str):
        self.queries.append(("or", filter_str))
        return self

    def in_(self, field, values):
        self.queries.append(("in", field, values))
        return self

    def delete(self):
        self.queries.append(("delete",))
        return self

    def order(self, field, desc=False):
        self.queries.append(("order", field, desc))
        return self

    def update(self, data):
        self.queries.append(("update", data))
        return self

    def limit(self, num):
        self.queries.append(("limit", num))
        return self

    def execute(self):
        data = []
        if self.name == "plants":
            if any(q[0] == "select" for q in self.queries):
                eq_queries = [q for q in self.queries if q[0] == "eq"]
                invalid_id = any(q[1] == "id" and str(q[2]) == "00000000-0000-0000-0000-000000000000" for q in eq_queries)
                if invalid_id:
                    data = []
                else:
                    data = [
                        {
                            "id": "d3b07384-d113-49c3-a558-1ec114a84d41",
                            "user_id": str(TEST_USER_ID),
                            "name": "몬스테라",
                            "species": "Monstera deliciosa",
                            "location": "거실 창가",
                            "sunlight": "간접광",
                            "garden_id": "a3b07384-d113-49c3-a558-1ec114a84d40",
                            "image_url": "https://xyz.supabase.co/storage/v1/object/public/plant-photos/abc.jpg",
                            "created_at": "2026-06-01T12:00:00+00:00"
                        }
                    ]
            elif any(q[0] == "delete" for q in self.queries):
                data = [{"id": "d3b07384-d113-49c3-a558-1ec114a84d41"}]
            elif any(q[0] == "insert" for q in self.queries):
                insert_val = next(q[1] for q in self.queries if q[0] == "insert")
                data = [
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": str(TEST_USER_ID),
                        "name": insert_val["name"],
                        "species": insert_val.get("species"),
                        "location": insert_val.get("location"),
                        "sunlight": insert_val.get("sunlight"),
                        "garden_id": insert_val.get("garden_id"),
                        "image_url": insert_val.get("image_url"),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                ]
            elif any(q[0] == "update" for q in self.queries):
                update_val = next(q[1] for q in self.queries if q[0] == "update")
                data = [
                    {
                        "id": "d3b07384-d113-49c3-a558-1ec114a84d41",
                        "user_id": str(TEST_USER_ID),
                        "name": update_val.get("name", "몬스테라"),
                        "species": update_val.get("species", "Monstera deliciosa"),
                        "location": update_val.get("location", "거실 창가"),
                        "sunlight": update_val.get("sunlight", "간접광"),
                        "garden_id": update_val.get("garden_id", "a3b07384-d113-49c3-a558-1ec114a84d40"),
                        "image_url": update_val.get("image_url", "https://xyz.supabase.co/storage/v1/object/public/plant-photos/abc.jpg"),
                        "created_at": "2026-06-01T12:00:00+00:00"
                    }
                ]
        elif self.name == "gardens":
            garden = {
                "id": "a3b07384-d113-49c3-a558-1ec114a84d40",
                "user_id": str(TEST_USER_ID),
                "name": "베란다 텃밭",
                "location": "남향 베란다",
                "description": "상추와 토마토",
                "sunlight": "오전 직사광선",
                "soil_type": "상토",
                "image_url": None,
                "created_at": "2026-06-01T12:00:00+00:00",
            }
            eq_queries = [q for q in self.queries if q[0] == "eq"]
            requested_id = next((str(q[2]) for q in eq_queries if q[1] == "id"), None)
            if requested_id and requested_id != garden["id"]:
                data = []
            elif any(q[0] == "insert" for q in self.queries):
                insert_val = next(q[1] for q in self.queries if q[0] == "insert")
                data = [{**garden, **insert_val, "id": garden["id"]}]
            elif any(q[0] == "update" for q in self.queries):
                update_val = next(q[1] for q in self.queries if q[0] == "update")
                data = [{**garden, **update_val}]
            elif any(q[0] == "delete" for q in self.queries):
                data = [{"id": garden["id"]}]
            elif any(q[0] == "select" for q in self.queries):
                data = [garden]
        elif self.name == "care_logs":
            if any(q[0] == "select" for q in self.queries):
                data = [
                    {
                        "id": "c3b07384-d113-49c3-a558-1ec114a84d42",
                        "plant_id": "d3b07384-d113-49c3-a558-1ec114a84d41",
                        "watered_at": "2026-06-25",
                        "leaf_condition": "정상",
                        "soil_condition": "약간 마름",
                        "memo": "정기 물주기",
                        "created_at": "2026-06-25T12:00:00+00:00"
                    }
                ]
            elif any(q[0] == "insert" for q in self.queries):
                insert_val = next(q[1] for q in self.queries if q[0] == "insert")
                data = [
                    {
                        "id": str(uuid.uuid4()),
                        "plant_id": insert_val["plant_id"],
                        "watered_at": insert_val.get("watered_at"),
                        "leaf_condition": insert_val.get("leaf_condition"),
                        "soil_condition": insert_val.get("soil_condition"),
                        "memo": insert_val.get("memo"),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                ]
            elif any(q[0] == "update" for q in self.queries):
                update_val = next(q[1] for q in self.queries if q[0] == "update")
                data = [
                    {
                        "id": "c3b07384-d113-49c3-a558-1ec114a84d42",
                        "plant_id": "d3b07384-d113-49c3-a558-1ec114a84d41",
                        "watered_at": update_val.get("watered_at", "2026-06-25"),
                        "leaf_condition": update_val.get("leaf_condition", "정상"),
                        "soil_condition": update_val.get("soil_condition", "약간 마름"),
                        "memo": update_val.get("memo", "정기 물주기"),
                        "created_at": "2026-06-25T12:00:00+00:00"
                    }
                ]
            elif any(q[0] == "delete" for q in self.queries):
                data = [{"id": "c3b07384-d113-49c3-a558-1ec114a84d42"}]
        elif self.name == "plant_photos":
            if any(q[0] == "select" for q in self.queries):
                data = [
                    {
                        "id": "e3b07384-d113-49c3-a558-1ec114a84d43",
                        "plant_id": "d3b07384-d113-49c3-a558-1ec114a84d41",
                        "storage_path": "users/d3b07384/plants/leaf.jpg",
                        "note": "초기 촬영",
                        "captured_at": "2026-06-01T12:00:00+00:00",
                        "created_at": "2026-06-01T12:00:00+00:00"
                    }
                ]
            elif any(q[0] == "insert" for q in self.queries):
                insert_val = next(q[1] for q in self.queries if q[0] == "insert")
                data = [
                    {
                        "id": str(uuid.uuid4()),
                        "plant_id": insert_val["plant_id"],
                        "storage_path": insert_val["storage_path"],
                        "note": insert_val.get("note"),
                        "captured_at": insert_val.get("captured_at"),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                ]
        elif self.name == "chat_sessions":
            if any(q[0] == "select" for q in self.queries):
                data = [
                    {
                        "id": "e3b07384-d113-49c3-a558-1ec114a84d44",
                        "user_id": str(TEST_USER_ID),
                        "plant_id": "d3b07384-d113-49c3-a558-1ec114a84d41",
                        "created_at": "2026-06-25T12:00:00+00:00"
                    }
                ]
                eq_queries = [q for q in self.queries if q[0] == "eq"]
                requested_id = next((str(q[2]) for q in eq_queries if q[1] == "id"), None)
                if requested_id and requested_id != data[0]["id"]:
                    data = []
            elif any(q[0] == "delete" for q in self.queries):
                data = [{"id": "e3b07384-d113-49c3-a558-1ec114a84d44"}]
            elif any(q[0] == "insert" for q in self.queries):
                insert_val = next(q[1] for q in self.queries if q[0] == "insert")
                data = [
                    {
                        "id": insert_val.get("id", str(uuid.uuid4())),
                        "user_id": insert_val["user_id"],
                        "plant_id": insert_val.get("plant_id"),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                ]
        elif self.name == "chat_messages":
            if any(q[0] == "select" for q in self.queries):
                data = [
                    {
                        "id": "e3b07384-d113-49c3-a558-1ec114a84d45",
                        "session_id": "e3b07384-d113-49c3-a558-1ec114a84d44",
                        "role": "user",
                        "content": {"text": "식물이 아파요"},
                        "citations": [],
                        "created_at": "2026-06-25T12:01:00+00:00"
                    },
                    {
                        "id": "e3b07384-d113-49c3-a558-1ec114a84d46",
                        "session_id": "e3b07384-d113-49c3-a558-1ec114a84d44",
                        "role": "assistant",
                        "content": {"text": "물을 주세요."},
                        "citations": [
                            {
                                "sourceId": "RAG-DOC-999",
                                "title": "도감",
                                "url": "http://nongsaro.go.kr",
                                "publisher": "농진청"
                            }
                        ],
                        "created_at": "2026-06-25T12:02:00+00:00"
                    }
                ]
                eq_queries = [q for q in self.queries if q[0] == "eq"]
                message_id = next((str(q[2]) for q in eq_queries if q[1] == "id"), None)
                if message_id:
                    data = [item for item in data if item["id"] == message_id]
            elif any(q[0] == "insert" for q in self.queries):
                insert_val = next(q[1] for q in self.queries if q[0] == "insert")
                data = [
                    {
                        "id": insert_val.get("id", str(uuid.uuid4())),
                        "session_id": insert_val["session_id"],
                        "role": insert_val["role"],
                        "content": insert_val["content"],
                        "citations": insert_val.get("citations", []),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                ]
        elif self.name == "chat_feedback":
            if any(q[0] == "upsert" for q in self.queries):
                upsert_val = next(q[1] for q in self.queries if q[0] == "upsert")
                data = [{
                    "id": str(uuid.uuid4()),
                    **upsert_val,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }]
            elif any(q[0] == "select" for q in self.queries):
                data = [{
                    "message_id": "e3b07384-d113-49c3-a558-1ec114a84d46",
                    "user_id": str(TEST_USER_ID),
                    "rating": "helpful",
                    "comment": None
                }]
                in_queries = [q for q in self.queries if q[0] == "in"]
                allowed_ids = next((q[2] for q in in_queries if q[1] == "message_id"), None)
                if allowed_ids is not None:
                    data = [item for item in data if item["message_id"] in allowed_ids]
        elif self.name == "plant_catalog":
            mock_catalog = [
                {"id": "cat-001", "name": "몬스테라 델리시오사", "species": "Monstera deliciosa", "family_name": "천남성과", "description": "구멍 난 큰 잎"},
                {"id": "cat-002", "name": "인도고무나무", "species": "Ficus elastica", "family_name": "뽕나무과", "description": "두꺼운 광택 잎"},
                {"id": "cat-003", "name": "스킨답서스", "species": "Epipremnum aureum", "family_name": "천남성과", "description": "강인한 생명력"}
            ]
            data = mock_catalog
            or_query = next((q[1] for q in self.queries if q[0] == "or"), None)
            if or_query:
                import re
                matches = re.findall(r'%([^%]+)%', or_query)
                if matches:
                    search_term = matches[0].lower()
                    data = [
                        item for item in mock_catalog
                        if search_term in item["name"].lower() or search_term in item["species"].lower()
                    ]
        elif self.name == "rpc_match_rag_chunks":
            data = [
                {
                    "id": "e3b07384-d113-49c3-a558-1ec114a84d47",
                    "source_id": "f1b07384-d113-49c3-a558-1ec114a84d01",
                    "title": "실내정원 유지관리 가이드라인 - 물관리 요령",
                    "url": "https://www.nihhs.go.kr",
                    "publisher": "국립원예특작과학원",
                    "content": "실내 식물 관리에서 가장 빈번하게 발생하는 이상 증상은 '과습'입니다. 몬스테라는 잎이 노랗게 변하고 무르는 황화 현상이 생길 수 있습니다.",
                    "similarity": 0.85
                }
            ]
        return MockAPIResponse(data)

class MockSupabaseStorageBucket:
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name

    def create_signed_upload_url(self, path):
        return {
            "signed_url": f"https://mock-supabase.co/storage/v1/object/upload/sign/{self.bucket_name}/{path}?token=mock-token",
            "signedUrl": f"https://mock-supabase.co/storage/v1/object/upload/sign/{self.bucket_name}/{path}?token=mock-token",
            "token": "mock-token",
            "path": path
        }

class MockSupabaseStorage:
    def from_(self, bucket_name):
        return MockSupabaseStorageBucket(bucket_name)

class MockSupabaseClient:
    def __init__(self):
        self.storage = MockSupabaseStorage()

    def table(self, name):
        return MockSupabaseTable(name)

    def rpc(self, name, params):
        mock_table = MockSupabaseTable(f"rpc_{name}")
        mock_table.queries.append(("rpc", name, params))
        return mock_table

# FastAPI 의존성 주입 오버라이드
app.dependency_overrides[get_current_user] = lambda: TEST_USER_ID
app.dependency_overrides[get_supabase_client] = lambda: MockSupabaseClient()

# 글로벌 supabase 클라이언트 오버라이드
from app.db import session as db_session
db_session.supabase = MockSupabaseClient()

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_list_plants():
    response = client.get("/api/v1/plants")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["name"] == "몬스테라"

def test_create_plant():
    payload = {
        "name": "홍바오",
        "species": "Ficus elastica",
        "location": "거실 안쪽",
        "sunlight": "반음지"
    }
    response = client.post("/api/v1/plants", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "홍바오"
    assert "id" in data
    assert "createdAt" in data


def test_list_gardens_with_plant_count():
    response = client.get("/api/v1/gardens")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "베란다 텃밭"
    assert data[0]["plantCount"] == 1


def test_create_garden():
    response = client.post(
        "/api/v1/gardens",
        json={"name": "옥상 텃밭", "location": "옥상", "sunlight": "종일 직사광선", "soilType": "상토"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "옥상 텃밭"
    assert data["soilType"] == "상토"


def test_reject_blank_garden_name():
    response = client.post("/api/v1/gardens", json={"name": "   "})
    assert response.status_code == 422


def test_update_garden():
    garden_id = "a3b07384-d113-49c3-a558-1ec114a84d40"
    response = client.patch(f"/api/v1/gardens/{garden_id}", json={"name": "새 텃밭", "description": "허브 구획"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "새 텃밭"
    assert data["description"] == "허브 구획"


def test_delete_garden_unassigns_plants():
    garden_id = "a3b07384-d113-49c3-a558-1ec114a84d40"
    response = client.delete(f"/api/v1/gardens/{garden_id}")
    assert response.status_code == 204


def test_garden_not_found():
    response = client.patch(
        "/api/v1/gardens/00000000-0000-0000-0000-000000000000",
        json={"name": "없는 텃밭"},
    )
    assert response.status_code == 404


def test_assign_plant_to_owned_garden():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    garden_id = "a3b07384-d113-49c3-a558-1ec114a84d40"
    response = client.patch(f"/api/v1/plants/{plant_id}", json={"gardenId": garden_id})
    assert response.status_code == 200
    assert response.json()["gardenId"] == garden_id


def test_reject_assigning_plant_to_unknown_garden():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    response = client.patch(
        f"/api/v1/plants/{plant_id}",
        json={"gardenId": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404

def test_create_care_log():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    payload = {
        "wateredAt": "2026-06-25",
        "leafCondition": "잎 끝이 약간 노랗게 마름",
        "soilCondition": "손가락 마디 깊이까지 바짝 말라 있음",
        "memo": "최근 물 준지 10일 지남"
    }
    response = client.post(f"/api/v1/plants/{plant_id}/care-logs", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["leafCondition"] == "잎 끝이 약간 노랗게 마름"
    assert "id" in data
    assert data["plantId"] == plant_id

def test_create_plant_photo():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    payload = {
        "storagePath": "users/d3b07384/plants/leaf_yellow.jpg",
        "capturedAt": "2026-06-29T12:00:00Z",
        "note": "클로즈업 촬영"
    }
    response = client.post(f"/api/v1/plants/{plant_id}/photos", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["storagePath"] == "users/d3b07384/plants/leaf_yellow.jpg"

def test_consult_plant_care():
    payload = {
        "plantId": "d3b07384-d113-49c3-a558-1ec114a84d41",
        "question": "잎 끝이 노랗게 변하는 이유가 뭘까요?"
    }
    response = client.post("/api/v1/chat/plant-care", json=payload)
    print("@@@ ERROR DETAIL:", response.json())
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "possibleCauses" in data
    assert "todayActions" in data
    assert "safetyNotice" in data

def test_create_signed_upload_url():
    payload = {
        "fileName": "my_monstera.png",
        "mimeType": "image/png"
    }
    response = client.post("/api/v1/uploads/signed-url", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "signedUrl" in data
    assert "storagePath" in data
    assert data["storagePath"].startswith(f"users/{TEST_USER_ID}/plants/")
    assert data["storagePath"].endswith(".png")
    assert "token=mock-token" in data["signedUrl"]

def test_list_plant_catalog_all():
    response = client.get("/api/v1/plant-catalog")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    assert any(item["name"] == "몬스테라 델리시오사" for item in data)

def test_search_plant_catalog_by_name():
    response = client.get("/api/v1/plant-catalog?q=몬스")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "몬스테라 델리시오사"
    assert data[0]["species"] == "Monstera deliciosa"

def test_search_plant_catalog_by_species_case_insensitive():
    response = client.get("/api/v1/plant-catalog?q=FiCuS")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "인도고무나무"

def test_search_plant_catalog_no_match():
    response = client.get("/api/v1/plant-catalog?q=우주식물")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0

def test_search_rag_documents(monkeypatch):
    from app.api.v1 import rag as rag_api
    from app.services.rag.vectorstore import SearchResult

    monkeypatch.setattr(
        rag_api,
        "search_documents",
        lambda q, top_k=5: [
            SearchResult(
                content="과습일 때는 흙 표면뿐 아니라 속흙이 충분히 마른 뒤 물을 주는 것이 좋습니다.",
                metadata={
                    "source_id": "RAG-DOC-001",
                    "title": "실내 식물 물관리",
                    "url": "https://example.com",
                    "publisher": "테스트 기관",
                },
                score=0.9,
            )
        ],
    )

    response = client.get("/api/v1/rag/search?q=과습")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "title" in data[0]
    assert "excerpt" in data[0]

def test_get_plant_detail_success():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    response = client.get(f"/api/v1/plants/{plant_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "몬스테라"
    assert "careLogs" in data
    assert "photos" in data
    assert len(data["careLogs"]) > 0
    assert len(data["photos"]) > 0
    assert data["careLogs"][0]["leafCondition"] == "정상"

def test_get_plant_detail_not_found():
    invalid_id = "00000000-0000-0000-0000-000000000000"
    response = client.get(f"/api/v1/plants/{invalid_id}")
    assert response.status_code == 404

def test_delete_plant_success():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    response = client.delete(f"/api/v1/plants/{plant_id}")
    assert response.status_code == 204

def test_update_plant_success():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    payload = {"name": "새로운몬스테라", "location": "방 안"}
    response = client.patch(f"/api/v1/plants/{plant_id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "새로운몬스테라"
    assert data["location"] == "방 안"

def test_update_care_log_success():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    log_id = "c3b07384-d113-49c3-a558-1ec114a84d42"
    payload = {"memo": "수정된 메모", "leafCondition": "아주 좋음"}
    response = client.put(f"/api/v1/plants/{plant_id}/care-logs/{log_id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["memo"] == "수정된 메모"
    assert data["leafCondition"] == "아주 좋음"

def test_delete_care_log_success():
    plant_id = "d3b07384-d113-49c3-a558-1ec114a84d41"
    log_id = "c3b07384-d113-49c3-a558-1ec114a84d42"
    response = client.delete(f"/api/v1/plants/{plant_id}/care-logs/{log_id}")
    assert response.status_code == 204

def test_list_chat_sessions_success():
    response = client.get("/api/v1/chat/sessions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "e3b07384-d113-49c3-a558-1ec114a84d44"


def test_delete_chat_session_success():
    session_id = "e3b07384-d113-49c3-a558-1ec114a84d44"
    response = client.delete(f"/api/v1/chat/sessions/{session_id}")
    assert response.status_code == 204
    assert response.content == b""


def test_delete_chat_session_not_found():
    session_id = "00000000-0000-0000-0000-000000000000"
    response = client.delete(f"/api/v1/chat/sessions/{session_id}")
    assert response.status_code == 404

def test_list_chat_messages_success():
    session_id = "e3b07384-d113-49c3-a558-1ec114a84d44"
    response = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["sender"] == "user"
    assert data[1]["sender"] == "assistant"
    assert len(data[1]["citations"]) == 1


def test_save_chat_feedback_success():
    message_id = "e3b07384-d113-49c3-a558-1ec114a84d46"
    response = client.post(
        f"/api/v1/chat/messages/{message_id}/feedback",
        json={"rating": "helpful"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["messageId"] == message_id
    assert data["rating"] == "helpful"
    assert data["saved"] is True


def test_save_chat_feedback_rejects_user_message():
    message_id = "e3b07384-d113-49c3-a558-1ec114a84d45"
    response = client.post(
        f"/api/v1/chat/messages/{message_id}/feedback",
        json={"rating": "helpful"}
    )
    assert response.status_code == 400


def test_list_session_feedback():
    session_id = "e3b07384-d113-49c3-a558-1ec114a84d44"
    response = client.get(f"/api/v1/chat/sessions/{session_id}/feedback")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["messageId"] == "e3b07384-d113-49c3-a558-1ec114a84d46"
    assert data[0]["rating"] == "helpful"


def test_feedback_summary():
    response = client.get("/api/v1/chat/feedback/summary")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["sessionId"] == "e3b07384-d113-49c3-a558-1ec114a84d44"
    assert data[0]["helpful"] == 1
    assert data[0]["notHelpful"] == 0
    assert data[0]["total"] == 1


def test_image_rag_signals_are_added_to_retrieval_query(monkeypatch):
    from app.services.rag import pipeline as rag_pipeline

    def fake_analyze_plant_image(db, storage_path, question):
        return {
            "signals": ["잎 앞면에 노란 반점 관찰", "사진상 심각도: 경미"],
            "description": "첨부 사진에서 잎 일부의 노란 반점과 가장자리 마름이 관찰됩니다.",
            "affectedParts": ["잎"],
            "severity": "경미",
        }

    monkeypatch.setattr(rag_pipeline, "analyze_plant_image", fake_analyze_plant_image)

    signal_result = rag_pipeline.extract_image_signals(
        {
            "db_client": MockSupabaseClient(),
            "photo_data": {
                "id": "e3b07384-d113-49c3-a558-1ec114a84d43",
                "storage_path": "users/d3b07384/plants/leaf.jpg",
                "note": "잎 앞면 사진",
            },
            "recent_photos": [],
            "care_logs": [],
            "question": "사진도 같이 보고 상태를 알려줘",
        }
    )

    assert "잎 앞면에 노란 반점 관찰" in signal_result["image_signals"]
    assert "노란 반점" in signal_result["image_description"]

    query_result = rag_pipeline.build_retrieval_query(
        {
            "plant_data": {"name": "몬스테라", "species": "Monstera deliciosa"},
            "question": "사진도 같이 보고 상태를 알려줘",
            "image_signals": signal_result["image_signals"],
            "image_description": signal_result["image_description"],
            "user_context": "식물 별명: 몬스테라",
        }
    )

    assert "사진 분석" in query_result["search_query"]
    assert "노란 반점" in query_result["search_query"]
