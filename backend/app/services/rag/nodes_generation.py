"""8~9단계: 답변 생성 및 안전성 검토 노드."""
import logging
import os
from typing import Dict, Any

from app.core.config import settings
from app.services.llm import chat_completion
from app.services.llm.schemas import GeneratedAnswer, parse_json_object
from app.services.rag.common import (
    AgentState,
    extract_user_name,
    is_smalltalk_question,
    is_user_name_question,
    make_excerpt,
    plant_persona_status,
    recall_user_name,
)

logger = logging.getLogger(__name__)


# 7. generate_answer 노드
def generate_answer(state: AgentState) -> Dict[str, Any]:
    openai_key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    docs = state["retrieved_docs"]
    question = state["question"]
    context = state["user_context"]
    image_description = state.get("image_description") or "사진 분석 결과 없음"
    vision_error = state.get("vision_error")
    response_mode = state.get("response_mode") or "expert"
    requested_provider = state.get("llm_provider") or "openai"
    requested_model = state.get("llm_model") or os.getenv("CHAT_MODEL") or settings.CHAT_MODEL
    plant = state.get("plant_data") or {}
    is_garden = plant.get("context_type") == "garden"
    is_companion_mode = response_mode == "companion" and not is_garden
    plant_label = plant.get("name") or plant.get("species") or "식물"
    chat_history = state.get("chat_history") or []
    history_text = "\n".join(
        f"{'사용자' if item.get('role') == 'user' else 'AI'}: {item.get('content')}"
        for item in chat_history[-8:]
    )
    remembered_name = recall_user_name(question, chat_history)
    generation_notice = None

    if extract_user_name(question):
        if is_companion_mode:
            summary = f"알겠어. 네 이름은 {remembered_name}이야. 이 상담방에서는 그렇게 기억해둘게."
        else:
            summary = f"알겠습니다. 사용자님의 이름은 {remembered_name}입니다. 이 상담방에서 그렇게 기억해두겠습니다."
        return {
            "draft_answer": {
                "summary": summary,
                "possibleCauses": [],
                "todayActions": ["이 이름은 현재 상담방의 대화 맥락에서 이어서 참고합니다."],
                "observationChecklist": [],
                "citations": [],
            }
        }

    if is_user_name_question(question):
        if remembered_name:
            summary = f"네 이름은 {remembered_name}이야." if is_companion_mode else f"사용자님의 이름은 {remembered_name}입니다."
        else:
            summary = "아직 이 상담방에서 이름을 들은 기록을 찾지 못했어." if is_companion_mode else "아직 이 상담방에서 사용자님의 이름을 확인한 기록이 없습니다."
        return {
            "draft_answer": {
                "summary": summary,
                "possibleCauses": [],
                "todayActions": [],
                "observationChecklist": [],
                "citations": [],
            }
        }
    
    if is_smalltalk_question(question):
        if is_companion_mode:
            persona_status = plant_persona_status(plant, state.get("care_logs") or [])
            return {
                "draft_answer": {
                    "summary": f"안녕, 나 {plant_label}야. {persona_status} 오늘 내 잎이나 흙 상태가 궁금하면 편하게 물어봐. 사진도 같이 보내주면 내가 지금 어떤 느낌인지 더 잘 말해볼게.",
                    "possibleCauses": ["아직 구체적인 질문이나 상태 사진이 없어서 내 컨디션을 정확히 말하긴 어려워."],
                    "todayActions": ["오늘은 내 흙이 얼마나 말랐는지 한 번 만져봐 줘.", "빛이 너무 세거나 바람이 바로 닿는 곳은 아닌지도 봐줘."],
                    "observationChecklist": ["잎 색이 변했는지", "흙이 젖어 있는지", "마지막 물 준 날이 언제인지", "새잎이 잘 펴지는지"],
                    "citations": [],
                }
            }
        subject_label = f"{plant_label} 텃밭" if is_garden else plant_label
        return {
            "draft_answer": {
                "summary": f"안녕하세요. {subject_label} 상담을 도와드릴게요. 물주기, 빛, 잎 상태, 흙 상태 중 궁금한 내용을 편하게 적어주세요.",
                "possibleCauses": ["아직 구체적인 증상이나 관리 질문이 입력되지 않았습니다."],
                "todayActions": ["궁금한 점을 한 문장으로 적거나, 상태 사진을 첨부해 주세요."],
                "observationChecklist": ["잎 색 변화", "흙 마름 정도", "최근 물 준 날짜", "빛을 받는 시간"],
                "citations": [],
            }
        }

    citations = []
    seen_sources = set()
    for doc in docs:
        metadata = doc.get("metadata") or {}
        source_id = metadata.get("source_id") or metadata.get("sourceId") or "unknown"
        if source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        citations.append({
            "sourceId": source_id,
            "title": metadata.get("title") or "출처 미상",
            "url": metadata.get("url"),
            "publisher": metadata.get("publisher"),
            "excerpt": make_excerpt(doc.get("content") or ""),
            "section": metadata.get("section") or metadata.get("category") or metadata.get("source_type")
        })
        
    if openai_key or settings.LLM_FALLBACK_ENABLED:
        try:
            docs_text = "\n\n".join([
                f"--- 문서: {(d.get('metadata') or {}).get('title') or '출처 미상'} ---\n{d.get('content') or ''}"
                for d in docs
            ])
            persona_status = plant_persona_status(plant, state.get("care_logs") or []) if is_companion_mode else ""
            mode_instruction = (
                f"답변 모드는 '내 식물과 대화하기'입니다. 당신은 사용자가 등록한 식물 '{plant_label}' 자신처럼 1인칭으로 말하세요. "
                f"[내 실제 상태] {persona_status} "
                "위의 실제 상태(물 마신 지 며칠째인지, 권장 주기, 함께한 기간)를 대화 흐름에 자연스럽게 녹여 말하세요. "
                "예: '나 3일째 물 못 마셨어.', '우리 같이 산 지 벌써 한 달이 넘었네.' 단, 질문과 무관하면 억지로 언급하지 마세요. "
                "친근한 반말을 사용하되 유치하거나 과장하지 말고, 식물이 부탁하는 듯한 짧은 문장을 섞으세요. "
                "예: '나 내일 흙이 말라 있으면 물 한 번 부탁해.', '오늘은 빛이 너무 세지 않은지 봐줘.' "
                "possibleCauses도 '내가 힘든 이유 후보'처럼 자연스럽게 쓰고, todayActions는 사용자가 식물을 돌보는 행동으로 작성하세요. "
                "공식 문서 근거가 부족하면 '확실히는 모르겠어'라고 말하세요. 안전 관련 내용은 장난처럼 표현하지 마세요. "
            ) if is_companion_mode else (
                "답변 모드는 '전문가와 상담하기'입니다. 차분하고 전문적인 상담 말투를 사용하세요. "
                "summary는 한 문단의 자연스러운 상담 말투로 작성하고, todayActions는 사용자가 바로 따라 할 수 있는 구체적인 행동으로 작성하세요. "
            )
            if is_garden:
                mode_instruction += (
                    "상담 대상은 단일 식물이 아니라 하나의 텃밭입니다. 텃밭의 위치, 일조, 토양, 구성 식물과 최근 관리 기록을 함께 비교하세요. "
                    "구성 식물마다 관리 요구가 다르면 식물별로 구분하고, 텃밭 전체에 일괄 적용하면 위험한 조치는 권하지 마세요. "
                )

            completion = chat_completion(
                primary_model=requested_model,
                local_model=settings.LOCAL_CHAT_MODEL,
                temperature=0.1,
                response_format={"type": "json_object"},
                primary_timeout=18.0,
                max_tokens=1200,
                preferred_provider=requested_provider,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 식물과 텃밭 관리 상담을 돕는 AI입니다. 반드시 사용자의 등록 정보, 최근 관리 기록, 검색된 공식 문서만 근거로 답하세요. "
                            "모든 답변은 JSON 객체만 출력합니다. "
                            "필드: evidenceNotes(string), summary(string), possibleCauses(string[]), todayActions(string[]), observationChecklist(string[]). "
                            "evidenceNotes 필드에는 사용자에게 보여줄 수 있는 짧은 근거 요약만 작성하세요. 내부 추론 과정이나 생각의 흐름은 출력하지 마세요. "
                            f"{mode_instruction}"
                            "이전 대화 메모리는 사용자의 선호, 이름, 직전 맥락을 이해하는 보조 정보로만 사용하세요. "
                            "현재 질문과 무관한 이전 증상, 이전 문서, 이전 답변 근거를 새 답변에 끌어오지 마세요. "
                            "현재 검색된 공식 문서가 없으면 문서 근거가 없다고 명확히 말하세요. "
                            "질병명 확정, 농약 직접 처방, 과도한 단정은 피하고 '~가능성', '관찰 필요' 중심으로 말하세요. "
                            "사진 분석 결과가 있으면 이를 관찰 근거로 반영하되, 사진만으로 확정 진단하지 마세요. "
                            "검색 문서가 부족하면 부족하다고 말하고 추가 사진/물주기/빛/흙 상태 정보를 요청하세요. "
                            "답변 안에서 출처 번호를 직접 꾸며 쓰기보다, 근거 문서는 citations 영역으로 제공된다고 가정하세요."
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"[식물 정보]\n{context}\n\n"
                            f"[사진 분석 결과]\n{image_description}\n\n"
                            f"[사진 분석 참고]\n{vision_error or '오류 없음'}\n\n"
                            f"[이전 대화 메모리]\n{history_text or '이전 대화 없음'}\n\n"
                            f"[검색된 공식 문서]\n{docs_text or '검색 문서 없음'}\n\n"
                            f"[사용자 질문]\n{question}"
                        )
                    }
                ]
            )

            raw_content = str(completion.response.choices[0].message.content or "").strip()
            ans = GeneratedAnswer.model_validate(parse_json_object(raw_content))
            return {
                "draft_answer": {
                    "summary": ans.summary or "입력된 식물 상태와 공식 자료를 바탕으로 관리 가이드를 정리했습니다.",
                    "possibleCauses": ans.possibleCauses or ["입력 정보만으로 확정하기 어려워 추가 관찰이 필요합니다."],
                    "todayActions": ans.todayActions or ["흙 수분, 빛, 통풍 상태를 먼저 확인합니다."],
                    "observationChecklist": ans.observationChecklist or ["잎 색 변화, 줄기 무름, 흙 냄새를 3~7일간 관찰합니다."],
                    "citations": citations
                },
                "llm_provider_used": completion.provider,
                "llm_model_used": completion.model,
            }
        except Exception as exc:
            logger.warning(
                "AI answer generation failed; using evidence fallback (model=%s, %s: %s)",
                os.getenv("CHAT_MODEL") or settings.CHAT_MODEL,
                type(exc).__name__,
                exc,
            )
            generation_notice = "현재 AI 생성 연결을 확인할 수 없어, 질문과 검색 문서를 바탕으로 한 기본 관찰 가이드를 표시합니다."
    else:
        generation_notice = "AI 생성 설정이 없어, 질문과 검색 문서를 바탕으로 한 기본 관찰 가이드를 표시합니다."
            
    # Classify only the current question and current image signals. Including every
    # retrieved document here made a single keyword from a broad source dominate
    # unrelated questions and produced the same fallback answer repeatedly.
    combined_signal_text = f"{question} {image_description} {' '.join(state.get('image_signals') or [])}".lower()

    if not docs:
        if is_companion_mode:
            summary = f"지금은 나({plant_label})에 대한 공식 문서 근거가 충분하지 않아서 확실히 말하긴 어려워. 그래도 오늘 내 잎, 흙, 빛 상태를 같이 보면 다음 답변은 훨씬 정확해질 거야."
            possible_causes = [
                "내 최근 물주기나 빛 기록이 부족해서 컨디션 원인을 좁히기 어려워.",
                "사진이나 재배 일지가 없으면 잎 변화가 일시적인지 관리 문제인지 헷갈릴 수 있어."
            ]
            today_actions = [
                "내 잎 앞뒤랑 흙 표면 사진을 한 장씩 남겨줘.",
                "마지막으로 물 준 날짜와 흙이 마르는 속도를 기록해줘."
            ]
            checklist = [
                "새잎까지 색 변화가 번지는지 봐줘.",
                "줄기 밑동이 무르거나 흙 냄새가 이상한지 확인해줘."
            ]
        else:
            summary = "현재 질문과 식물 기록만으로는 공식 문서 근거가 충분하지 않아 확정적인 판단은 어렵습니다."
            possible_causes = [
                "최근 물주기, 빛, 통풍, 흙 상태 정보가 부족합니다.",
                "사진이나 재배 일지 없이 증상만으로는 원인 후보를 좁히기 어렵습니다."
            ]
            today_actions = [
                "잎 앞뒤, 줄기 밑동, 흙 표면 사진을 추가로 기록합니다.",
                "최근 물 준 날짜와 흙이 마르는 속도를 재배 일지에 남깁니다."
            ]
            checklist = [
                "잎 색 변화가 새잎까지 번지는지 확인합니다.",
                "줄기 밑동이 무르거나 흙 냄새가 나는지 확인합니다."
            ]
    elif any(token in combined_signal_text for token in ["과습", "물주기", "젖", "축축", "무름", "뿌리"]):
        summary = "입력된 증상과 검색 문서를 보면 과습 또는 배수 불량 가능성을 우선 점검해야 합니다."
        possible_causes = [
            "배수가 잘 되지 않거나 흙이 마르기 전 잦은 관수",
            "화분 물받이에 고여있는 정체수"
        ]
        today_actions = [
            "화분 밑 물받이에 고인 물을 완전히 비워줍니다.",
            "손가락 두 마디 깊이까지 흙이 말랐는지 확인하고, 젖어 있으면 물주기를 미룹니다."
        ]
        checklist = [
            "새싹이나 안쪽 줄기가 무르거나 갈색으로 변하는지 3~7일간 관찰합니다.",
            "흙 냄새가 시큼하거나 곰팡이 냄새가 나는지 확인합니다."
        ]
    elif any(token in combined_signal_text for token in ["노랗", "황화", "색이", "하엽", "영양", "질소", "비료"]):
        summary = "잎 황화가 보인다면 과습, 광량 변화, 영양 부족 가능성을 함께 비교해야 합니다."
        possible_causes = [
            "오래된 하엽부터 노랗게 변하는 자연 노화 또는 영양 부족",
            "젖은 흙이 오래 유지되어 뿌리 기능이 떨어진 상태",
            "갑작스러운 빛 환경 변화"
        ]
        today_actions = [
            "새잎과 오래된 잎 중 어디부터 노랗게 변하는지 구분해 기록합니다.",
            "흙 수분을 먼저 확인하고, 젖어 있으면 비료보다 건조와 통풍을 우선합니다."
        ]
        checklist = [
            "노란 부위가 잎맥 사이인지, 잎 가장자리인지, 전체 잎인지 관찰합니다.",
            "다음 물주기 전까지 황화 범위가 넓어지는지 확인합니다."
        ]
    elif any(token in combined_signal_text for token in ["건조", "마름", "잎 끝", "갈변", "바삭", "습도"]):
        summary = "잎끝 마름이나 갈변은 건조, 강한 빛, 물 부족 스트레스 가능성이 있습니다."
        possible_causes = [
            "난방기구 주변이나 바람이 직접 닿아 일어난 습도 저하",
            "흙 하부까지 충분히 젖지 않는 얕은 관수",
            "강한 직사광선 또는 급격한 위치 변화"
        ]
        today_actions = [
            "화분 무게와 흙 속 수분을 확인한 뒤 말랐다면 배수구로 물이 빠질 만큼 충분히 관수합니다.",
            "난방기, 에어컨, 강한 직사광선 위치에서 한 발 떨어뜨립니다."
        ]
        checklist = [
            "잎끝 마름이 멈추는지, 새잎에도 반복되는지 관찰합니다.",
            "실내 습도가 40% 이하로 오래 유지되는지 확인합니다."
        ]
    elif any(token in combined_signal_text for token in ["벌레", "응애", "깍지", "진딧", "반점", "해충"]):
        summary = "반점이나 해충 흔적이 있다면 병해충 가능성을 배제하지 말고 잎 뒷면을 확인해야 합니다."
        possible_causes = [
            "잎 뒷면이나 줄기 마디에 붙은 소형 해충",
            "통풍 부족과 과습이 겹친 반점성 이상",
            "물방울 또는 강한 빛에 의한 국소 손상"
        ]
        today_actions = [
            "잎 뒷면, 줄기 마디, 새순 주변을 확대해서 확인하고 사진으로 남깁니다.",
            "해당 식물을 다른 식물과 잠시 떨어뜨려 관찰합니다."
        ]
        checklist = [
            "흰 가루, 거미줄, 끈적임, 작은 점이 움직이는지 확인합니다.",
            "반점이 원형으로 커지거나 주변 잎으로 번지는지 관찰합니다."
        ]
    else:
        question_excerpt = " ".join(question.split())[:48]
        summary = f"‘{question_excerpt}’ 질문은 한 가지 증상으로 분류하기 어려워, 검색된 공식 자료와 식물 기록을 기준으로 기본 관리 상태부터 점검해야 합니다."
        possible_causes = [
            "물주기, 광량, 통풍 중 하나가 현재 식물 조건과 맞지 않을 가능성",
            "최근 위치 변경이나 계절 변화에 따른 일시적 적응 반응"
        ]
        today_actions = [
            "흙 수분, 빛이 닿는 시간, 통풍 상태를 오늘 기준으로 기록합니다.",
            "증상이 보이는 잎과 정상 잎을 함께 촬영해 비교 기록을 남깁니다."
        ]
        checklist = [
            "3~7일 동안 증상이 새잎으로 확산되는지 확인합니다.",
            "물주기 후 회복되는지 또는 더 처지는지 관찰합니다."
        ]

    if is_companion_mode and not summary.startswith("지금은 나("):
        summary = f"나({plant_label}) 상태를 보면, {summary.replace('입니다.', '인 것 같아.').replace('합니다.', '해줘.')}"
        possible_causes = [item.replace("가능성", "가능성이 있어").replace("상태", "상태일 수 있어") for item in possible_causes]
        today_actions = [
            item.replace("확인합니다.", "확인해줘.").replace("기록합니다.", "기록해줘.").replace("남깁니다.", "남겨줘.").replace("미룹니다.", "미뤄줘.")
            for item in today_actions
        ]
        checklist = [
            item.replace("확인합니다.", "확인해줘.").replace("관찰합니다.", "관찰해줘.").replace("기록합니다.", "기록해줘.")
            for item in checklist
        ]
        
    return {
        "draft_answer": {
            "summary": summary,
            "possibleCauses": possible_causes,
            "todayActions": today_actions,
            "observationChecklist": checklist,
            "citations": citations
        },
        "generation_notice": generation_notice
    }

# 검색된 근거 문서의 안전 태그에 따라 강제로 덧붙이는 고지 문구.
# 답변 문장에 "농약" 같은 단어가 등장하지 않아도 근거가 농약 자료라면 고지해야 한다
# (AGENTS.md 절대규칙 6항, data/PIPELINE.md의 pesticide_caution 규정).
PESTICIDE_CAUTION_NOTICE = (
    "이 답변의 근거 자료에는 농약 정보가 포함되어 있습니다. 특정 약제를 처방하는 것이 아니며, "
    "사용 전 제품 라벨의 적용 작물·희석 배수·사용 시기·수확 전 사용 가능 일수를 직접 확인하고 "
    "안전사용기준을 준수해야 합니다. 실제 살포 판단은 농업기술센터 등 전문가 확인을 거치십시오."
)
LABEL_CHECK_NOTICE = (
    "이 답변의 근거 자료는 제품 라벨 확인이 필요한 정보를 포함합니다. "
    "적용 작물과 사용 기준을 라벨에서 직접 확인하십시오."
)
ACTION_SAFETY_KEYWORDS = ("농약", "살충", "살균", "약제")


def collect_document_safety_tags(docs: Any) -> list:
    """검색된 근거 문서에서 안전 태그를 중복 없이 수집한다 (등장 순서 유지)."""
    tags: list = []
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        metadata = doc.get("metadata") or {}
        raw = metadata.get("safety_tags") or metadata.get("safetyTags") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for tag in raw:
            tag = str(tag).strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


# 8. safety_review 노드
def safety_review(state: AgentState) -> Dict[str, Any]:
    draft = state["draft_answer"]
    response_mode = state.get("response_mode") or "expert"

    if response_mode == "companion":
        safety_notice = "친근한 대화 모드의 답변이지만, 실제 관리는 입력된 내용과 공식 지침서에 기반한 참고 가이드입니다. 증상이 지속되면 전문가 확인을 권장합니다."
    else:
        safety_notice = "본 관리 가이드는 입력된 내용 및 공식 지침서에 기반하여 생성되었으며 특정 질병을 확정하는 것이 아닙니다. 상세 증상이 지속되면 농업기술센터 전문가의 도움을 받으십시오."

    if state.get("generation_notice"):
        safety_notice = f"{state['generation_notice']} {safety_notice}"

    # 답변 문구가 아니라 검색된 근거 문서의 태그를 기준으로 고지를 결정한다.
    document_safety_tags = collect_document_safety_tags(state.get("retrieved_docs"))
    if "pesticide_caution" in document_safety_tags:
        safety_notice = f"{safety_notice} {PESTICIDE_CAUTION_NOTICE}"
    elif "label_check_required" in document_safety_tags:
        safety_notice = f"{safety_notice} {LABEL_CHECK_NOTICE}"

    today_actions = []
    for act in draft["todayActions"]:
        if any(keyword in act for keyword in ACTION_SAFETY_KEYWORDS):
            today_actions.append(f"{act} (안전사용기준 준수 및 전문가 상담 권장)")
        else:
            today_actions.append(act)

    final_answer = {
        "summary": draft["summary"],
        "possibleCauses": draft["possibleCauses"],
        "todayActions": today_actions,
        "observationChecklist": draft["observationChecklist"],
        "citations": draft["citations"],
        "safetyNotice": safety_notice
    }
    return {"final_answer": final_answer}
