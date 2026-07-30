"""RAG 품질/메모리 보강 테스트 (test.py)

프로젝트 핵심 체크리스트(발표 자료 기준)를 코드로 검증한다.

  [이미지 요구사항]
    R1. 질문과 관련된 문서를 잘 찾는가            -> test_retrieval_*
    R2. 찾은 문서를 바탕으로 답변하는가           -> test_grounded_*
    R3. 없는 내용은 없다고 말하는가(근거 제한)     -> test_restraint_*
    R4. 답변에 출처/근거를 제시하는가             -> test_citations_*
    R5. 테스트를 통해 개선했는가                  -> (이 파일 자체)

  [추가로 필요하다고 판단한 케이스]
    A1. LLM 실패/키 없음 -> 조용한 룰베이스 대신 명시적 오류   -> test_error_*
    A2. 여러 문서 종합 & 중복 출처 제거                       -> test_citations_dedup / test_grounded_multi_doc
    A3. 스몰토크는 검색 없이 안내로 처리                       -> test_smalltalk_*
    A4. 안전성 리뷰(농약/살충 경고 부착)                       -> test_safety_*
    A5. 메모리: 이전 대화 로드 + 순서 복원                     -> test_memory_history_order
    A6. 메모리: 과거 첨부 사진 분석 복원(크로스 토픽 기억)     -> test_memory_photo_recall
    A7. 메모리: 특정 세션 이어가기 + 소유권 검증              -> test_session_continue_* / test_session_ownership
    A8. 저장: 사진 분석을 메시지에 함께 영속화                -> test_persist_saves_photo_analysis
    A9. E2E: 그래프 전체 흐름(검색·LLM 목킹)                  -> test_e2e_plant_care_flow

실행:  pytest backend/tests/test.py -v
"""

import json
import types
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth.security import get_current_user
from app.db.session import get_supabase_client
from app.services.rag import nodes_retrieval, pipeline
from app.services.rag.vectorstore import SearchResult


TEST_USER_ID = uuid.UUID("d3b07384-d113-49c3-a558-1ec114a84d41")
PLANT_ID = "d3b07384-d113-49c3-a558-1ec114a84d41"
SESSION_ID = "e3b07384-d113-49c3-a558-1ec114a84d44"
OTHER_USER_ID = "aaaaaaaa-d113-49c3-a558-1ec114a84d99"


# ---------------------------------------------------------------------------
# 공용 페이크 (경량 Supabase / OpenAI / 검색)
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    """eq/order/limit 필터를 실제와 유사하게 처리하고 insert/update를 기록하는 테이블."""

    def __init__(self, name, db):
        self.name = name
        self.db = db
        self._op = None
        self._payload = None
        self._filters = []
        self._order = None
        self._limit = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, field, value):
        self._filters.append((field, str(value)))
        return self

    def order(self, field, desc=False):
        self._order = (field, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._op == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            self.db.inserts.append((self.name, self._payload))
            return _Resp(rows)
        if self._op == "update":
            self.db.updates.append((self.name, self._payload, dict(self._filters)))
            return _Resp([])
        if self._op == "select":
            rows = [
                r for r in self.db.tables.get(self.name, [])
                if all(str(r.get(f)) == v for f, v in self._filters)
            ]
            if self._order:
                field, desc = self._order
                rows = sorted(rows, key=lambda r: r.get(field) or "", reverse=desc)
            if self._limit is not None:
                rows = rows[: self._limit]
            return _Resp(rows)
        return _Resp([])


class FakeDB:
    """table()만 지원하는 초경량 Supabase 대체물. inserts/updates를 검증에 활용."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _FakeTable(name, self)


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


@pytest.fixture
def fake_llm(monkeypatch):
    """openai.OpenAI를 목킹한다.

    system 프롬프트 내용으로 3가지 호출을 구분한다.
      - 쿼리 확장(build_retrieval_query)  -> {"queries": [...]}
      - 재랭킹(grade_or_rerank)            -> "yes"
      - 최종 답변(generate_answer)         -> recorder.answer(JSON)
    recorder.calls 로 실제 전달된 messages(대화 이력 포함)를 검증할 수 있다.
    recorder.fail=True 면 최종 답변 호출에서 예외를 던진다.
    """
    import openai

    recorder = types.SimpleNamespace(
        calls=[],
        fail=False,
        answer={
            "reasoning": "과습으로 인한 황화 가능성을 문서와 대조",
            "summary": "과습에 따른 잎 황화 가능성이 있어 물주기 점검이 필요합니다.",
            "possibleCauses": ["과습/배수 불량", "광량 변화"],
            "todayActions": ["물받이 물 비우기", "흙 마름 확인 후 관수 조절"],
            "observationChecklist": ["새잎까지 번지는지 관찰"],
        },
    )

    class _Cmp:
        def create(self, **kwargs):
            recorder.calls.append(kwargs)
            messages = kwargs.get("messages") or []
            system = messages[0]["content"] if messages else ""
            if "검색 쿼리 생성기" in system:
                return _Completion(json.dumps({"queries": ["몬스테라 황화", "과습 배수", "잎 노랑"]}))
            if "관련성이 있다면" in system:
                return _Completion("yes")
            if recorder.fail:
                raise RuntimeError("LLM 호출 강제 실패(테스트)")
            return _Completion(json.dumps(recorder.answer))

    class _FakeOpenAI:
        def __init__(self, *a, **k):
            self.chat = types.SimpleNamespace(completions=_Cmp())

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(pipeline.settings, "OPENAI_API_KEY", "test-key", raising=False)
    return recorder


def make_docs(*sources):
    """(source_id, title) 목록으로 retrieved_docs 형태의 문서 리스트 생성."""
    docs = []
    for source_id, title in sources:
        docs.append({
            "content": f"{title} 관련 공식 문서 본문. 과습 시 잎이 노랗게 변할 수 있습니다.",
            "metadata": {
                "source_id": source_id,
                "title": title,
                "url": "https://www.nihhs.go.kr",
                "publisher": "국립원예특작과학원",
                "section": "물관리",
            },
            "score": 0.9,
        })
    return docs


def base_answer_state(docs, question="잎이 노랗게 변해요", **overrides):
    state = {
        "retrieved_docs": docs,
        "question": question,
        "user_context": "식물 별명: 몬스테라 / 품종: Monstera deliciosa",
        "image_description": "사진 분석 결과 없음",
        "vision_error": None,
        "image_signals": [],
        "chat_history": [],
        "plant_data": {"name": "몬스테라", "species": "Monstera deliciosa"},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# R1. 검색: 질문과 관련된 문서를 잘 찾는가
# ---------------------------------------------------------------------------
def test_retrieval_returns_documents(monkeypatch):
    captured = {}

    def fake_search(query, top_k=8, target_crop_terms=None):
        captured["query"] = query
        captured["top_k"] = top_k
        return [SearchResult("과습 시 황화가 발생합니다.", {"source_id": "S1", "title": "물관리"}, 0.88)]

    monkeypatch.setattr(nodes_retrieval, "search_documents", fake_search)

    state = {
        "question": "몬스테라 잎이 노랗게 변해요",
        "plant_data": {"name": "몬스테라", "species": "Monstera deliciosa"},
        "image_signals": ["잎 변색 (황화)"],
        "image_description": "",
        "search_query": "몬스테라 황화",
        "user_context": "식물: 몬스테라",
    }
    result = pipeline.retrieve_docs(state)

    docs = result["retrieved_docs"]
    assert len(docs) == 1
    assert docs[0]["content"].startswith("과습")
    assert docs[0]["metadata"]["source_id"] == "S1"
    # 질문/식물/징후가 검색 질의에 반영되는지
    assert "몬스테라" in captured["query"]


def test_retrieval_skipped_for_smalltalk(monkeypatch):
    called = {"n": 0}

    def fake_search(query, top_k=8, target_crop_terms=None):
        called["n"] += 1
        return []

    monkeypatch.setattr(nodes_retrieval, "search_documents", fake_search)
    result = pipeline.retrieve_docs({"question": "안녕하세요"})
    assert result["retrieved_docs"] == []
    assert called["n"] == 0  # 스몰토크는 검색 자체를 하지 않음


# ---------------------------------------------------------------------------
# R2/R4. 근거 기반 답변 + 출처 제시
# ---------------------------------------------------------------------------
def test_grounded_answer_uses_documents(fake_llm):
    docs = make_docs(("S1", "실내정원 물관리 요령"))
    result = pipeline.generate_answer(base_answer_state(docs))

    draft = result["draft_answer"]
    assert draft["summary"] == fake_llm.answer["summary"]
    # 문서 본문이 LLM 입력(user 메시지)에 실제로 전달됐는지
    answer_call = fake_llm.calls[-1]
    user_msg = answer_call["messages"][-1]["content"]
    assert "실내정원 물관리 요령" in user_msg


def test_citations_carry_source_metadata(fake_llm):
    docs = make_docs(("S1", "물관리 요령"))
    draft = pipeline.generate_answer(base_answer_state(docs))["draft_answer"]

    assert len(draft["citations"]) == 1
    cit = draft["citations"][0]
    assert cit["sourceId"] == "S1"
    assert cit["title"] == "물관리 요령"
    assert cit["publisher"] == "국립원예특작과학원"
    assert cit["excerpt"]  # 발췌문이 채워짐


def test_grounded_multi_doc_synthesis(fake_llm):
    docs = make_docs(("S1", "물관리 요령"), ("S2", "병해충 진단 가이드"))
    draft = pipeline.generate_answer(base_answer_state(docs))["draft_answer"]

    # 서로 다른 출처 2건이 모두 인용되고, 두 문서 본문이 프롬프트에 포함
    assert {c["sourceId"] for c in draft["citations"]} == {"S1", "S2"}
    user_msg = fake_llm.calls[-1]["messages"][-1]["content"]
    assert "물관리 요령" in user_msg and "병해충 진단 가이드" in user_msg


def test_citations_dedup_same_source(fake_llm):
    # 같은 source_id 문서가 2개 검색돼도 인용은 1건으로 합쳐져야 함
    docs = make_docs(("S1", "물관리 요령"), ("S1", "물관리 요령"))
    draft = pipeline.generate_answer(base_answer_state(docs))["draft_answer"]
    assert len(draft["citations"]) == 1


# ---------------------------------------------------------------------------
# R3. 근거가 없으면 "없다"고 말하고 답변을 제한
# ---------------------------------------------------------------------------
def test_restraint_when_no_documents(fake_llm):
    result = pipeline.generate_answer(base_answer_state([]))
    draft = result["draft_answer"]

    assert draft["citations"] == []
    assert "근거" in draft["summary"] or "찾" in draft["summary"]
    # 근거 없음은 LLM을 호출하지 않고 절제 응답으로 단락(환각 차단)
    assert fake_llm.calls == []


# ---------------------------------------------------------------------------
# A1. 폴백 대신 명시적 오류
# ---------------------------------------------------------------------------
def test_error_when_llm_fails(fake_llm):
    fake_llm.fail = True
    docs = make_docs(("S1", "물관리 요령"))
    with pytest.raises(RuntimeError):
        pipeline.generate_answer(base_answer_state(docs))


def test_error_when_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(pipeline.settings, "OPENAI_API_KEY", "", raising=False)
    docs = make_docs(("S1", "물관리 요령"))
    with pytest.raises(RuntimeError):
        pipeline.generate_answer(base_answer_state(docs))


# ---------------------------------------------------------------------------
# A3. 스몰토크
# ---------------------------------------------------------------------------
def test_smalltalk_is_detected():
    assert pipeline.is_smalltalk_question("안녕하세요") is True
    assert pipeline.is_smalltalk_question("잎이 노랗게 변하고 흙이 축축해요") is False


def test_smalltalk_answer_without_llm(fake_llm):
    state = base_answer_state([], question="안녕")
    draft = pipeline.generate_answer(state)["draft_answer"]
    assert draft["citations"] == []
    assert fake_llm.calls == []  # 스몰토크는 LLM 호출 없이 안내


# ---------------------------------------------------------------------------
# A4. 안전성 리뷰
# ---------------------------------------------------------------------------
def test_safety_notice_and_pesticide_warning():
    draft = {
        "summary": "요약",
        "possibleCauses": ["원인"],
        "todayActions": ["농약을 살포합니다", "물받이를 비웁니다"],
        "observationChecklist": ["관찰"],
        "citations": [],
    }
    final = pipeline.safety_review({"draft_answer": draft})["final_answer"]

    assert final["safetyNotice"]
    pesticide_action = next(a for a in final["todayActions"] if "농약" in a)
    assert "전문가" in pesticide_action  # 안전 사용 경고가 덧붙음


# ---------------------------------------------------------------------------
# A5/A6. 메모리: 이전 대화 로드 + 사진 기억
# ---------------------------------------------------------------------------
def _history_db(messages):
    return FakeDB({
        "chat_sessions": [{"id": SESSION_ID, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID}],
        "chat_messages": messages,
    })


def test_memory_history_order():
    db = _history_db([
        {"id": "m1", "session_id": SESSION_ID, "role": "user",
         "content": {"text": "첫 질문"}, "created_at": "2026-06-25T12:00:00+00:00"},
        {"id": "m2", "session_id": SESSION_ID, "role": "assistant",
         "content": {"text": "첫 답변"}, "created_at": "2026-06-25T12:00:05+00:00"},
        {"id": "m3", "session_id": SESSION_ID, "role": "user",
         "content": {"text": "둘째 질문"}, "created_at": "2026-06-25T12:01:00+00:00"},
    ])
    out = pipeline.load_chat_history({
        "db_client": db, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID, "new_session": False,
    })

    assert out["session_id"] == SESSION_ID
    texts = [(h["role"], h["text"]) for h in out["chat_history"]]
    # 시간순(오래된 -> 최신)으로 복원
    assert texts == [("user", "첫 질문"), ("assistant", "첫 답변"), ("user", "둘째 질문")]


def test_memory_photo_recall():
    # 과거 턴에 사진을 첨부했고, 그 분석이 content.imageAnalysis 로 저장돼 있음
    db = _history_db([
        {"id": "m1", "session_id": SESSION_ID, "role": "user",
         "content": {"text": "이 사진 좀 봐줘", "imageAnalysis": "잎끝 갈변과 반점이 관찰됨"},
         "created_at": "2026-06-25T12:00:00+00:00"},
        {"id": "m2", "session_id": SESSION_ID, "role": "assistant",
         "content": {"text": "반점은 병해충 가능성이 있습니다."},
         "created_at": "2026-06-25T12:00:05+00:00"},
    ])
    out = pipeline.load_chat_history({
        "db_client": db, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID, "new_session": False,
    })
    joined = "\n".join(h["text"] for h in out["chat_history"])
    # 다른 주제로 넘어갔다가 다시 언급해도 되도록 과거 사진 분석이 맥락에 복원됨
    assert "이전에 첨부한 사진 분석" in joined
    assert "잎끝 갈변과 반점" in joined


def test_memory_injected_into_llm_messages(fake_llm):
    state = base_answer_state(
        make_docs(("S1", "물관리 요령")),
        chat_history=[
            {"role": "user", "text": "지난번에 보낸 사진 기억해?"},
            {"role": "assistant", "text": "네, 잎끝 갈변이 있었죠."},
        ],
    )
    pipeline.generate_answer(state)
    roles = [m["role"] for m in fake_llm.calls[-1]["messages"]]
    # system 다음에 과거 user/assistant turn이 실제로 주입되고, 마지막이 현재 질문(user)
    assert roles[0] == "system"
    assert "assistant" in roles
    assert roles[-1] == "user"


def test_new_session_has_no_history():
    db = _history_db([
        {"id": "m1", "session_id": SESSION_ID, "role": "user",
         "content": {"text": "옛 대화"}, "created_at": "2026-06-25T12:00:00+00:00"},
    ])
    out = pipeline.load_chat_history({
        "db_client": db, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID, "new_session": True,
    })
    assert out["session_id"] is None
    assert out["chat_history"] == []


# ---------------------------------------------------------------------------
# A7. 특정 세션 이어가기 + 소유권 검증
# ---------------------------------------------------------------------------
def test_session_continue_by_id():
    db = FakeDB({
        "chat_sessions": [{"id": SESSION_ID, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID}],
        "chat_messages": [
            {"id": "m1", "session_id": SESSION_ID, "role": "user",
             "content": {"text": "예전 세션 대화"}, "created_at": "2026-06-20T09:00:00+00:00"},
        ],
    })
    out = pipeline.load_chat_history({
        "db_client": db, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID,
        "new_session": False, "target_session_id": SESSION_ID,
    })
    assert out["session_id"] == SESSION_ID
    assert out["chat_history"][0]["text"] == "예전 세션 대화"


def test_session_ownership_rejected():
    # 다른 사용자의 세션을 지정하면 접근 거부(ValueError -> 라우터에서 404)
    db = FakeDB({
        "chat_sessions": [{"id": SESSION_ID, "user_id": OTHER_USER_ID, "plant_id": PLANT_ID}],
        "chat_messages": [],
    })
    with pytest.raises(ValueError):
        pipeline.load_chat_history({
            "db_client": db, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID,
            "new_session": False, "target_session_id": SESSION_ID,
        })


# ---------------------------------------------------------------------------
# A8. 저장: 사진 분석을 메시지에 함께 영속화
# ---------------------------------------------------------------------------
def _persist_state(db, **overrides):
    state = {
        "db_client": db,
        "user_id": str(TEST_USER_ID),
        "plant_id": PLANT_ID,
        "question": "이 사진 상태 봐줘",
        "plant_data": {"name": "몬스테라", "species": "Monstera deliciosa"},
        "new_session": False,
        "session_id": None,
        "photo_id": None,
        "image_description": None,
        "image_signals": [],
        "final_answer": {
            "summary": "요약",
            "possibleCauses": ["원인1"],
            "todayActions": ["행동1"],
            "observationChecklist": ["관찰1"],
            "citations": [],
        },
    }
    state.update(overrides)
    return state


def test_persist_saves_photo_analysis():
    db = FakeDB({"chat_sessions": [], "chat_messages": []})
    state = _persist_state(
        db,
        photo_id="photo-123",
        image_description="잎끝 갈변과 반점 관찰",
        image_signals=["병해충 의심 반점"],
    )
    out = pipeline.persist_result(state)

    # 새 세션이 생성되고 messageId 반환
    assert out["session_id"]
    assert out["message_id"]

    user_inserts = [
        payload for table, payload in db.inserts
        if table == "chat_messages" and isinstance(payload, dict) and payload.get("role") == "user"
    ]
    assert user_inserts, "user 메시지가 저장되어야 함"
    content = user_inserts[0]["content"]
    # 이후 턴의 사진 기억을 위해 분석/사진ID가 content에 함께 저장됨
    assert content["imageAnalysis"] == "잎끝 갈변과 반점 관찰"
    assert content["photoId"] == "photo-123"


def test_persist_reuses_resolved_session():
    db = FakeDB({
        "chat_sessions": [{"id": SESSION_ID, "user_id": str(TEST_USER_ID), "plant_id": PLANT_ID, "title": "기존"}],
        "chat_messages": [],
    })
    state = _persist_state(db, session_id=SESSION_ID)
    out = pipeline.persist_result(state)

    assert out["session_id"] == SESSION_ID
    # 확정된 세션에 그대로 저장 (새 세션을 만들지 않음)
    session_inserts = [t for t, _ in db.inserts if t == "chat_sessions"]
    assert session_inserts == []
    msg_sessions = {
        payload["session_id"] for table, payload in db.inserts
        if table == "chat_messages" and isinstance(payload, dict)
    }
    assert msg_sessions == {SESSION_ID}


# ---------------------------------------------------------------------------
# A9. E2E: 그래프 전체 흐름 (검색·LLM 목킹)
# ---------------------------------------------------------------------------
def test_e2e_plant_care_flow(monkeypatch, fake_llm):
    def fake_search(query, top_k=8, target_crop_terms=None):
        return [
            SearchResult(
                "과습 시 몬스테라 잎이 노랗게 변할 수 있습니다.",
                {"source_id": "S1", "title": "물관리 요령", "url": "https://www.nihhs.go.kr",
                 "publisher": "국립원예특작과학원", "section": "물관리"},
                0.9,
            )
        ]

    monkeypatch.setattr(nodes_retrieval, "search_documents", fake_search)

    db = FakeDB({
        "plants": [{"id": PLANT_ID, "user_id": str(TEST_USER_ID), "name": "몬스테라", "species": "Monstera deliciosa"}],
        "care_logs": [],
        "plant_photos": [],
        "chat_sessions": [],
        "chat_messages": [],
    })

    app.dependency_overrides[get_current_user] = lambda: TEST_USER_ID
    app.dependency_overrides[get_supabase_client] = lambda: db
    client = TestClient(app)
    try:
        resp = client.post("/api/v1/chat/plant-care", json={
            "plantId": PLANT_ID,
            "question": "몬스테라 잎이 노랗게 변해요",
        })
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"] == fake_llm.answer["summary"]
    assert data["citations"] and data["citations"][0]["sourceId"] == "S1"
    assert data["sessionId"]  # 새 세션이 생성되어 반환
    assert data["safetyNotice"]
