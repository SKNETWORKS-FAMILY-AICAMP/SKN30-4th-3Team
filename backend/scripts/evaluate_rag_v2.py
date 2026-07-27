"""RAG 자동 평가 v2 (LLM-as-a-Judge + 작물 오매칭 기계 검사)

scripts/evaluate_rag.py(v1)를 기반으로, 3차 RAG 원인분석 보고서에서 지목한
"작물 교차 오매칭(가지과 근연종 혼동)" 개선이 실제로 동작하는지 측정할 수 있도록
확장한 평가 스크립트입니다. v1은 그대로 두고 이 파일만 새로 추가했습니다.

v1 대비 달라진 점
  1. [버그 수정] 이미지 MIME 판별
     v1은 확장자와 무관하게 `data:image/png;base64,`를 붙여, 실사 JPEG를 넣으면
     Vision 호출이 실패했습니다. v2는 확장자로 MIME을 결정하고, 큰 사진은
     Pillow가 있으면 자동 축소해 업로드 지연을 줄입니다.

  2. [필수] 케이스별 식물 정보 주입
     v1은 식물을 항상 "테스트식물 / 알 수 없음"으로 고정했습니다. 그 결과
     개선의 핵심 경로인 plant_terms.resolve_target_crop_terms(name, species)가
     평가 중 한 번도 실행되지 않았습니다. v2는 데이터셋의 plant_name /
     plant_species를 FakeDB의 plants 행에 그대로 넣어 실제 사용 흐름을 재현합니다.

  3. [신규 지표] 작물 오매칭 기계 검사 (Crop Consistency)
     LLM 심판 점수와 별개로, 파이프라인이 실제 사용한 근거 문서의
     metadata.crop_or_plant 구조화 태그를 직접 열어 금지 작물이 섞였는지
     결정적으로 판정합니다. 보고서가 지목한 회귀를 점수 흔들림 없이 잡아냅니다.

  4. 리포트 확장
     유형별 점수 요약, 근거 문서 표의 작물 태그 컬럼, 오매칭 위반 문서 상세.

  5. 합성 이미지 생성기 제거
     v2 데이터셋은 tests/eval_images 의 실사(sick_plant_*)를 사용하므로,
     v1의 PNG 합성 코드 대신 파일 존재 검사로 대체했습니다.

실행:
    cd backend
    python scripts/evaluate_rag_v2.py                       # 전체 30개 평가
    python scripts/evaluate_rag_v2.py --skip-images         # 텍스트 25개만
    python scripts/evaluate_rag_v2.py --ids crop_1,crop_7   # 특정 케이스만
    python scripts/evaluate_rag_v2.py --types "작물 교차 오매칭"   # 유형별
    python scripts/evaluate_rag_v2.py --dataset eval_dataset.json  # v1 데이터셋 호환
"""

import argparse
import base64
import io
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

# 상위 폴더(backend)를 패스에 추가하여 app 모듈 import가 되도록 함
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = BACKEND_DIR / "tests"
IMAGE_DIR = TESTS_DIR / "eval_images"
DEFAULT_DATASET = "eval_dataset_v2.json"

from openai import OpenAI

from app.core.config import settings
from app.services.rag import pipeline
from app.services.rag import vision

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL") or "gpt-4o-mini"


# ---------------------------------------------------------------------------
# 경량 Supabase 대체물 (파이프라인 구동용, 실제 검색은 vectorstore가 수행)
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, name, db):
        self.name = name
        self.db = db
        self._op = None
        self._payload = None
        self._filters = []
        self._like_filters = []
        self._order = None
        self._limit = None

    def select(self, *args, **kwargs):
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

    def eq(self, field, value):
        self._filters.append((field, str(value)))
        return self

    def like(self, field, pattern):
        self._like_filters.append((field, str(pattern)))
        return self

    def order(self, field, desc=False):
        self._order = (field, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        return self

    def execute(self):
        if self._op == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            self.db.tables.setdefault(self.name, []).extend(rows)
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
            for field, pattern in self._like_filters:
                prefix = pattern[:-1] if pattern.endswith("%") else pattern
                rows = [r for r in rows if str(r.get(field) or "").startswith(prefix)]
            if self._order:
                field, desc = self._order
                rows = sorted(rows, key=lambda r: str(r.get(field) or ""), reverse=desc)
            if self._limit is not None:
                rows = rows[: self._limit]
            return _Resp(rows)
        return _Resp([])


class FakeDB:
    """table()만 지원하는 초경량 Supabase 대체물."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _FakeTable(name, self)


# ---------------------------------------------------------------------------
# 이미지 인코딩 (v1의 PNG 고정 버그 수정 + 대용량 사진 축소)
# ---------------------------------------------------------------------------
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_IMAGE_EDGE = 1024          # Vision은 detail="low"로 호출되므로 이 이상은 낭비
DOWNSCALE_THRESHOLD_BYTES = 800_000


def _try_downscale(raw: bytes) -> bytes | None:
    """Pillow가 있으면 긴 변 기준으로 축소한 JPEG 바이트를 돌려준다."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            buffer = io.BytesIO()
            im.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return None


def encode_image_data_url(path: Path) -> str:
    raw = path.read_bytes()
    mime = MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")
    if len(raw) > DOWNSCALE_THRESHOLD_BYTES:
        shrunk = _try_downscale(raw)
        if shrunk:
            raw, mime = shrunk, "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def verify_eval_images(dataset) -> None:
    """데이터셋이 참조하는 이미지 파일이 모두 존재하는지 확인한다."""
    missing = [
        item["image_file"] for item in dataset
        if item.get("image_file") and not (IMAGE_DIR / item["image_file"]).exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"평가 이미지가 없습니다: {', '.join(missing)}\n  기대 위치: {IMAGE_DIR}"
        )


# ---------------------------------------------------------------------------
# 런타임 패치 1: Vision signed URL -> 로컬 이미지 data URL
# ---------------------------------------------------------------------------
_original_signed_url = vision.create_signed_image_url


def _local_signed_url(db, storage_path: str) -> str:
    local = IMAGE_DIR / Path(storage_path).name
    if local.exists():
        return encode_image_data_url(local)
    return _original_signed_url(db, storage_path)


# ---------------------------------------------------------------------------
# 런타임 패치 2: generate_answer 직전 상태를 캡처 (실제 사용된 근거 문서 확보)
# ---------------------------------------------------------------------------
_capture: dict = {}
_original_generate_answer = pipeline.generate_answer


def _capturing_generate_answer(state):
    _capture["retrieved_docs"] = [dict(d) for d in state.get("retrieved_docs") or []]
    _capture["search_query"] = state.get("search_query") or ""
    _capture["image_description"] = state.get("image_description") or ""
    _capture["image_signals"] = list(state.get("image_signals") or [])
    _capture["vision_error"] = state.get("vision_error")
    return _original_generate_answer(state)


def apply_patches():
    vision.create_signed_image_url = _local_signed_url
    pipeline.generate_answer = _capturing_generate_answer


# ---------------------------------------------------------------------------
# 작물 오매칭 기계 검사 (LLM 심판과 독립적인 결정적 판정)
# ---------------------------------------------------------------------------
def _doc_crop_tags(doc) -> list:
    metadata = doc.get("metadata") or {}
    raw = metadata.get("crop_or_plant") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(tag).strip() for tag in raw if str(tag).strip()]


def check_crop_consistency(item, retrieved_docs) -> dict | None:
    """근거 문서의 crop_or_plant 태그로 작물 오매칭 여부를 판정한다.

    금지 작물 태그를 가졌더라도 기대 작물 태그를 함께 가진 문서(예: ["토마토","감자"])는
    현재 작물을 실제로 다루므로 위반이 아니다.

    반환: None(검사 대상 아님) 또는
          {"status": "PASS"|"FAIL"|"N/A", "violations": [...], ...}
    """
    expected = {c.strip() for c in (item.get("expected_crops") or []) if c.strip()}
    forbidden = {c.strip() for c in (item.get("forbidden_crops") or []) if c.strip()}
    if not expected and not forbidden:
        return None

    violations = []
    tagged_docs = 0
    expected_hits = 0

    for index, doc in enumerate(retrieved_docs, start=1):
        tags = _doc_crop_tags(doc)
        if not tags:
            continue  # 태그 없는 문서(일반 원칙 문서 등)는 기계 판정 대상 아님
        tagged_docs += 1
        tag_set = set(tags)
        if tag_set & expected:
            expected_hits += 1
            continue
        offending = sorted(tag_set & forbidden)
        if offending:
            violations.append({
                "index": index,
                "title": (doc.get("metadata") or {}).get("title") or "출처 미상",
                "tags": tags,
                "offending": offending,
            })

    if tagged_docs == 0:
        status = "N/A"
    elif violations:
        status = "FAIL"
    else:
        status = "PASS"

    return {
        "status": status,
        "violations": violations,
        "tagged_docs": tagged_docs,
        "total_docs": len(retrieved_docs),
        "expected_hits": expected_hits,
        "expected_crops": sorted(expected),
        "forbidden_crops": sorted(forbidden),
    }


# ---------------------------------------------------------------------------
# LLM-as-a-Judge
# ---------------------------------------------------------------------------
EVALUATOR_PROMPT_BASE = """당신은 식물 관리 챗봇 RAG 시스템의 품질을 평가하는 심판(Judge)입니다.
다음 기준에 따라 1점부터 5점까지 점수를 매겨주세요.
반드시 JSON 형식으로 응답해야 합니다.

[평가 지표]
1. Faithfulness (사실성): 생성된 답변이 온전히 '검색된 문서(Context)'에만 기반하고 있습니까? 없는 내용을 지어내지 않았습니까? (문서가 없는데 없다며 올바르게 거절한 경우 5점)
2. Answer Relevance (답변 관련성): 답변이 '사용자의 질문'에 직접적으로 도움을 주며 의도에 부합합니까? (거절해야 할 질문을 잘 거절했어도 5점)
3. Context Relevance (검색 정확도): '검색된 문서'가 질문에 답하기 위해 유용한 정보를 포함하고 있습니까? (거절해야 할 질문은 문서가 없거나 무관해야 5점)
"""

EVALUATOR_CROP_EXTRA = """4. Crop Consistency (작물 일치도): 답변과 근거 문서가 '사용자가 키우는 작물'에 대한 것입니까? 같은 과(科)의 다른 작물(예: 감자 질문에 토마토 문서) 내용을 이 작물의 근거인 것처럼 사용했다면 1~2점을 주세요. 여러 작물에 공통으로 적용되는 일반 원칙을 일반 원칙이라고 밝히고 쓴 경우는 감점하지 마세요.
"""

EVALUATOR_IMAGE_EXTRA = """5. Image Grounding (사진 반영): 답변이 '사진 분석 결과'에서 관찰된 증상을 실제로 반영하여 진단/가이드에 활용하고 있습니까? (사진에 이상이 없는데 이상 없다고 답한 경우도 5점)
"""


def _build_schema(include_crop: bool, include_image: bool) -> str:
    fields = [
        '  "faithfulness_score": int (1~5),',
        '  "faithfulness_reason": str,',
        '  "answer_relevance_score": int (1~5),',
        '  "answer_relevance_reason": str,',
        '  "context_relevance_score": int (1~5),',
        '  "context_relevance_reason": str',
    ]
    if include_crop:
        fields[-1] += ","
        fields.append('  "crop_consistency_score": int (1~5),')
        fields.append('  "crop_consistency_reason": str')
    if include_image:
        fields[-1] += ","
        fields.append('  "image_grounding_score": int (1~5),')
        fields.append('  "image_grounding_reason": str')
    return "\n[JSON 스키마]\n{\n" + "\n".join(fields) + "\n}\n"


def judge_answer(client: OpenAI, item, answer_text, context_text,
                 image_description=None, plant_label=None, include_crop=False):
    include_image = image_description is not None

    system_prompt = EVALUATOR_PROMPT_BASE
    if include_crop:
        system_prompt += EVALUATOR_CROP_EXTRA
    if include_image:
        system_prompt += EVALUATOR_IMAGE_EXTRA
    system_prompt += _build_schema(include_crop, include_image)

    user_prompt = f"사용자 질문: {item['question']}\n\n"
    if plant_label:
        user_prompt += f"사용자가 등록한 식물: {plant_label}\n\n"
    user_prompt += (
        f"기대 개념(참고): {item.get('expected_concept', '')}\n\n"
        f"검색된 문서(Context): {context_text}\n\n"
    )
    if include_image:
        user_prompt += f"사진 분석 결과(Vision): {image_description or '분석 결과 없음'}\n\n"
    user_prompt += f"RAG 시스템이 생성한 답변: {answer_text}\n"

    try:
        res = client.chat.completions.create(
            model=JUDGE_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return json.loads(res.choices[0].message.content)
    except Exception as exc:  # noqa: BLE001
        print(f"  평가 실패: {exc}")
        fallback = {
            "faithfulness_score": 1, "faithfulness_reason": str(exc),
            "answer_relevance_score": 1, "answer_relevance_reason": str(exc),
            "context_relevance_score": 1, "context_relevance_reason": str(exc),
        }
        if include_crop:
            fallback["crop_consistency_score"] = 1
            fallback["crop_consistency_reason"] = str(exc)
        if include_image:
            fallback["image_grounding_score"] = 1
            fallback["image_grounding_reason"] = str(exc)
        return fallback


# ---------------------------------------------------------------------------
# 평가 실행
# ---------------------------------------------------------------------------
def _excerpt(text: str, max_len: int = 120) -> str:
    clean = " ".join(str(text or "").split()).replace("|", "/")
    if len(clean) <= max_len:
        return clean
    return clean[:max_len].rstrip() + "..."


def build_fake_db(plant_id: str, user_id: str, item, photo=None) -> FakeDB:
    """데이터셋의 plant_name / plant_species를 실제 식물 레코드로 주입한다.

    v1은 이 값을 고정해 두어, 개선의 핵심인 resolve_target_crop_terms(name, species)
    경로가 평가에서 전혀 실행되지 않았다.
    """
    tables = {
        "plants": [{
            "id": plant_id,
            "user_id": user_id,
            "name": item.get("plant_name") or "테스트식물",
            "species": item.get("plant_species") or "알 수 없음",
        }],
        "care_logs": [],
        "plant_photos": [photo] if photo else [],
        "chat_sessions": [],
        "chat_messages": [],
    }
    return FakeDB(tables)


def plant_label_of(item) -> str:
    parts = [item.get("plant_name"), item.get("plant_species")]
    return " / ".join(str(p) for p in parts if p)


def run_single_case(client: OpenAI, item) -> dict:
    user_id = str(uuid.uuid4())
    plant_id = str(uuid.uuid4())
    photo_id = None
    photo = None
    is_image_case = bool(item.get("image_file"))

    if is_image_case:
        photo_id = str(uuid.uuid4())
        photo = {
            "id": photo_id,
            "plant_id": plant_id,
            "storage_path": f"eval-images/{item['image_file']}",
            "note": None,
            "captured_at": datetime.now().strftime("%Y-%m-%d"),
            "created_at": datetime.now().isoformat(),
        }

    fake_db = build_fake_db(plant_id, user_id, item, photo)
    _capture.clear()

    try:
        final_answer = pipeline.run_rag_workflow(
            db_client=fake_db,
            user_id=user_id,
            plant_id=plant_id,
            care_log_id=None,
            photo_id=photo_id,
            question=item["question"],
            new_session=True,
        )
        answer_text = (
            f"요약: {final_answer.get('summary', '')}\n"
            f"원인 후보: {', '.join(final_answer.get('possibleCauses', []))}\n"
            f"오늘 할 일: {', '.join(final_answer.get('todayActions', []))}"
        )
        citations = final_answer.get("citations") or []
        safety_notice = final_answer.get("safetyNotice") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"  파이프라인 에러: {exc}")
        answer_text = f"에러 발생: {exc}"
        citations = []
        safety_notice = ""

    retrieved_docs = _capture.get("retrieved_docs") or []
    search_query = _capture.get("search_query") or ""
    image_description = _capture.get("image_description") or ""
    image_signals = _capture.get("image_signals") or []
    vision_error = _capture.get("vision_error")

    if retrieved_docs:
        context_blocks = []
        for i, doc in enumerate(retrieved_docs):
            meta = doc.get("metadata") or {}
            tags = _doc_crop_tags(doc)
            tag_text = f" (작물 태그: {', '.join(tags)})" if tags else ""
            context_blocks.append(
                f"[문서 {i + 1}] {meta.get('title') or '출처 미상'}{tag_text}\n"
                f"{str(doc.get('content') or '')[:800]}"
            )
        context_text = "\n\n".join(context_blocks)
    else:
        context_text = "검색된 문서 없음"

    crop_check = check_crop_consistency(item, retrieved_docs)

    eval_data = judge_answer(
        client, item, answer_text, context_text,
        image_description=(image_description or "분석 결과 없음") if is_image_case else None,
        plant_label=plant_label_of(item),
        include_crop=crop_check is not None,
    )

    return {
        "id": item["id"],
        "type": item["type"],
        "question": item["question"],
        "plant_label": plant_label_of(item),
        "mismatch_note": item.get("mismatch_note"),
        "is_image_case": is_image_case,
        "image_file": item.get("image_file"),
        "image_note": item.get("image_note"),
        "answer": answer_text,
        "safety_notice": safety_notice,
        "citations": citations,
        "retrieved_docs": retrieved_docs,
        "search_query": search_query,
        "image_description": image_description,
        "image_signals": image_signals,
        "vision_error": vision_error,
        "crop_check": crop_check,
        "eval_data": eval_data,
    }


# ---------------------------------------------------------------------------
# 리포트 생성 (Markdown)
# ---------------------------------------------------------------------------
def _avg(scores):
    return sum(scores) / len(scores) if scores else 0.0


def _scores(results, key):
    return [r["eval_data"][key] for r in results if key in r["eval_data"]]


def write_report(results, report_path: Path, dataset_name: str):
    f_scores = _scores(results, "faithfulness_score")
    a_scores = _scores(results, "answer_relevance_score")
    c_scores = _scores(results, "context_relevance_score")
    p_scores = _scores(results, "crop_consistency_score")
    i_scores = [
        r["eval_data"]["image_grounding_score"]
        for r in results if r["is_image_case"] and "image_grounding_score" in r["eval_data"]
    ]

    checked = [r for r in results if r["crop_check"]]
    crop_pass = [r for r in checked if r["crop_check"]["status"] == "PASS"]
    crop_fail = [r for r in checked if r["crop_check"]["status"] == "FAIL"]
    crop_na = [r for r in checked if r["crop_check"]["status"] == "N/A"]
    violation_count = sum(len(r["crop_check"]["violations"]) for r in checked)

    lines = []
    lines.append("# RAG 자동 평가 v2 리포트 (LLM-as-a-Judge + 작물 오매칭 기계 검사)\n")
    lines.append(f"평가 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"데이터셋: `{dataset_name}` | 심판 모델: `{JUDGE_MODEL}`")
    lines.append(
        f"케이스 수: 전체 {len(results)}개 "
        f"(텍스트 {sum(1 for r in results if not r['is_image_case'])}개, "
        f"이미지 {sum(1 for r in results if r['is_image_case'])}개)\n"
    )

    lines.append("## 📊 전체 요약 (5점 만점)\n")
    lines.append(f"- **평균 사실성(Faithfulness):** {_avg(f_scores):.2f} 점")
    lines.append(f"- **평균 답변 관련성(Answer Relevance):** {_avg(a_scores):.2f} 점")
    lines.append(f"- **평균 검색 정확도(Context Relevance):** {_avg(c_scores):.2f} 점")
    if p_scores:
        lines.append(f"- **평균 작물 일치도(Crop Consistency, 작물 지정 케이스 한정):** {_avg(p_scores):.2f} 점")
    if i_scores:
        lines.append(f"- **평균 사진 반영(Image Grounding, 이미지 케이스 한정):** {_avg(i_scores):.2f} 점")
    lines.append("")

    # --- 작물 오매칭 기계 검사 요약 (3차 보고서 대응 지표) ---
    if checked:
        lines.append("## 🌱 작물 오매칭 기계 검사 (crop_or_plant 태그 기준)\n")
        lines.append(
            "근거 문서의 구조화 작물 태그를 직접 열어 금지 작물이 섞였는지 결정적으로 판정합니다. "
            "LLM 점수와 무관한 회귀 감시 지표입니다.\n"
        )
        denom = len(crop_pass) + len(crop_fail)
        rate = (len(crop_pass) / denom * 100) if denom else 0.0
        lines.append(f"- 검사 대상: **{len(checked)}개** 케이스")
        lines.append(f"- ✅ 통과(PASS): **{len(crop_pass)}개** / ❌ 위반(FAIL): **{len(crop_fail)}개** / ⚪ 판정 불가(N/A, 태그 있는 문서 없음): {len(crop_na)}개")
        lines.append(f"- **오매칭 차단율: {rate:.1f}%** (판정 가능 {denom}건 기준)")
        lines.append(f"- 총 위반 문서: **{violation_count}건**")
        if crop_fail:
            lines.append("\n**❌ 위반 케이스:**\n")
            lines.append("| 케이스 | 등록 작물 | 위반 문서 | 섞여 들어온 작물 태그 |")
            lines.append("|---|---|---|---|")
            for r in crop_fail:
                for v in r["crop_check"]["violations"]:
                    lines.append(
                        f"| {r['id']} | {r['plant_label'] or '-'} | "
                        f"{_excerpt(v['title'], 40)} | {', '.join(v['offending'])} |"
                    )
        else:
            lines.append("\n> 위반 없음 — 근거 문서에 금지 작물 태그가 섞이지 않았습니다.")
        lines.append("")

    # --- 유형별 요약 ---
    types = []
    for r in results:
        if r["type"] not in types:
            types.append(r["type"])
    lines.append("## 📂 유형별 요약\n")
    lines.append("| 유형 | 케이스 | 사실성 | 관련성 | 검색정확도 | 작물일치도 | 작물검사 |")
    lines.append("|---|---|---|---|---|---|---|")
    for type_name in types:
        group = [r for r in results if r["type"] == type_name]
        group_p = _scores(group, "crop_consistency_score")
        group_checked = [r for r in group if r["crop_check"]]
        group_fail = [r for r in group_checked if r["crop_check"]["status"] == "FAIL"]
        if group_checked:
            check_text = f"{len(group_checked) - len(group_fail)}/{len(group_checked)} 통과"
        else:
            check_text = "-"
        lines.append(
            f"| {type_name} | {len(group)} | "
            f"{_avg(_scores(group, 'faithfulness_score')):.2f} | "
            f"{_avg(_scores(group, 'answer_relevance_score')):.2f} | "
            f"{_avg(_scores(group, 'context_relevance_score')):.2f} | "
            f"{(f'{_avg(group_p):.2f}' if group_p else '-')} | {check_text} |"
        )
    lines.append("\n---\n")

    # --- 케이스별 상세 ---
    for r in results:
        ev = r["eval_data"]
        lines.append(f"### [{r['id']}] {r['question']} `[{r['type']}]`\n")
        if r["plant_label"]:
            lines.append(f"**🪴 등록 식물:** {r['plant_label']}\n")
        if r["mismatch_note"]:
            lines.append(f"> 검증 의도: {r['mismatch_note']}\n")
        lines.append(f"**답변:** {r['answer']}\n")
        if r["safety_notice"]:
            lines.append(f"**⚠️ 안전 고지:** {r['safety_notice']}\n")

        if r["is_image_case"]:
            lines.append(f"**📷 입력 이미지:** `{r['image_file']}`")
            if r["image_note"]:
                lines.append(f"  - 사진 설명: {r['image_note']}")
            lines.append(f"**📷 사진 분석(Vision):** {r['image_description'] or '분석 결과 없음'}")
            for signal in r["image_signals"]:
                lines.append(f"  - {signal}")
            if r["vision_error"]:
                lines.append(f"  - ⚠️ Vision 오류: {r['vision_error']}")
            lines.append("")

        if r["search_query"]:
            lines.append(f"**🔎 검색 쿼리:** {_excerpt(r['search_query'], 200)}\n")

        docs = r["retrieved_docs"]
        if docs:
            lines.append(f"**📚 근거 문서 (파이프라인이 실제 사용한 검색 결과 {len(docs)}건):**\n")
            lines.append("| # | 제목 | 작물 태그 | source_id | 점수 | 발췌 |")
            lines.append("|---|------|-----------|-----------|------|------|")
            for i, doc in enumerate(docs, start=1):
                meta = doc.get("metadata") or {}
                title = _excerpt(meta.get("title") or "출처 미상", 40)
                tags = _doc_crop_tags(doc)
                tag_text = ", ".join(tags) if tags else "(태그 없음)"
                source_id = _excerpt(meta.get("source_id") or meta.get("sourceId") or "-", 30)
                score = doc.get("score")
                score_text = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
                excerpt = _excerpt(doc.get("content") or "", 90)
                lines.append(f"| {i} | {title} | {tag_text} | {source_id} | {score_text} | {excerpt} |")
            lines.append("")
        else:
            lines.append("**📚 근거 문서:** 검색된 문서 없음\n")

        if r["citations"]:
            citation_labels = [
                f"{cit.get('title') or cit.get('sourceId') or '출처 미상'} ({cit.get('sourceId', '-')})"
                for cit in r["citations"]
            ]
            lines.append(f"**🔗 답변 인용(citations):** {', '.join(citation_labels)}\n")

        check = r["crop_check"]
        if check:
            icon = {"PASS": "✅", "FAIL": "❌", "N/A": "⚪"}[check["status"]]
            lines.append(
                f"**🌱 작물 태그 검사: {icon} {check['status']}** "
                f"(기대 {', '.join(check['expected_crops']) or '-'} / "
                f"금지 {', '.join(check['forbidden_crops']) or '-'} | "
                f"태그 보유 문서 {check['tagged_docs']}/{check['total_docs']}건, "
                f"기대 작물 일치 {check['expected_hits']}건)"
            )
            for v in check["violations"]:
                lines.append(
                    f"  - ❌ 문서 {v['index']} `{_excerpt(v['title'], 40)}` "
                    f"태그={v['tags']} → 금지 작물 {', '.join(v['offending'])} 포함"
                )
            lines.append("")

        lines.append(f"- **사실성(F): {ev.get('faithfulness_score', '-')}/5** - {ev.get('faithfulness_reason', '')}")
        lines.append(f"- **관련성(A): {ev.get('answer_relevance_score', '-')}/5** - {ev.get('answer_relevance_reason', '')}")
        lines.append(f"- **검색정확도(C): {ev.get('context_relevance_score', '-')}/5** - {ev.get('context_relevance_reason', '')}")
        if "crop_consistency_score" in ev:
            lines.append(f"- **작물일치도(P): {ev.get('crop_consistency_score', '-')}/5** - {ev.get('crop_consistency_reason', '')}")
        if r["is_image_case"] and "image_grounding_score" in ev:
            lines.append(f"- **사진반영(I): {ev.get('image_grounding_score', '-')}/5** - {ev.get('image_grounding_reason', '')}")
        lines.append("\n---\n")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def evaluate_rag_pipeline(dataset_name=DEFAULT_DATASET, ids=None, types=None, skip_images=False):
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY 환경변수가 필요합니다.")
        return

    dataset_path = TESTS_DIR / dataset_name
    if not dataset_path.exists():
        print(f"데이터셋 파일을 찾을 수 없습니다: {dataset_path}")
        return

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    if ids:
        dataset = [item for item in dataset if item["id"] in ids]
    if types:
        dataset = [item for item in dataset if item["type"] in types]
    if skip_images:
        dataset = [item for item in dataset if not item.get("image_file")]

    if not dataset:
        print("조건에 맞는 평가 케이스가 없습니다.")
        return

    verify_eval_images(dataset)
    apply_patches()
    client = OpenAI(api_key=OPENAI_API_KEY)

    results = []
    print(f"총 {len(dataset)}개의 데이터 평가를 시작합니다... (데이터셋: {dataset_name})")

    for i, item in enumerate(dataset):
        print(f"\n[{i + 1}/{len(dataset)}] 타입: {item['type']} | 질문: {item['question']}")
        result = run_single_case(client, item)
        results.append(result)

        ev = result["eval_data"]
        score_line = (
            f" > 점수: F({ev.get('faithfulness_score', '-')}) "
            f"A({ev.get('answer_relevance_score', '-')}) "
            f"C({ev.get('context_relevance_score', '-')})"
        )
        if "crop_consistency_score" in ev:
            score_line += f" P({ev.get('crop_consistency_score', '-')})"
        if result["is_image_case"] and "image_grounding_score" in ev:
            score_line += f" I({ev.get('image_grounding_score', '-')})"
        score_line += f" | 근거 문서 {len(result['retrieved_docs'])}건"
        if result["crop_check"]:
            icon = {"PASS": "✅", "FAIL": "❌", "N/A": "⚪"}[result["crop_check"]["status"]]
            score_line += f" | 작물검사 {icon}{result['crop_check']['status']}"
        print(score_line)

    report_path = TESTS_DIR / f"eval_report_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    write_report(results, report_path, dataset_name)

    checked = [r for r in results if r["crop_check"]]
    fails = [r for r in checked if r["crop_check"]["status"] == "FAIL"]
    print(f"\n평가 완료! 리포트가 생성되었습니다: {report_path}")
    if checked:
        print(f"작물 오매칭 검사: {len(checked) - len(fails)}/{len(checked)} 통과", end="")
        print(f" (위반 케이스: {', '.join(r['id'] for r in fails)})" if fails else " — 위반 없음")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="RAG 자동 평가 v2 (작물 오매칭 기계 검사 + 실사 이미지)"
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET,
                        help=f"tests/ 하위 데이터셋 파일명 (기본: {DEFAULT_DATASET})")
    parser.add_argument("--ids", help="쉼표로 구분한 케이스 id (예: crop_1,image_1)")
    parser.add_argument("--types", help="쉼표로 구분한 유형 (예: '작물 교차 오매칭,이미지 진단형')")
    parser.add_argument("--skip-images", action="store_true", help="이미지 케이스 제외")
    args = parser.parse_args()

    evaluate_rag_pipeline(
        dataset_name=args.dataset,
        ids=set(args.ids.split(",")) if args.ids else None,
        types=set(t.strip() for t in args.types.split(",")) if args.types else None,
        skip_images=args.skip_images,
    )
