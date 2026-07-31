from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class Citation(BaseModel):
    sourceId: str
    title: str
    url: Optional[str] = None
    publisher: Optional[str] = None
    excerpt: Optional[str] = None
    section: Optional[str] = None


class ChatMemoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class PlantCareChatRequest(BaseModel):
    plantId: Optional[UUID] = None
    gardenId: Optional[UUID] = None
    careLogId: Optional[UUID] = None
    photoId: Optional[UUID] = None
    sessionId: Optional[UUID] = None
    newSession: bool = False
    responseMode: Literal["expert", "companion"] = "expert"
    llmProvider: Optional[Literal["openai", "local"]] = None
    llmModel: Optional[Literal["gpt-5.4", "gpt-5.5", "gpt-5.6-sol"]] = None
    recentMessages: List[ChatMemoryMessage] = Field(default_factory=list)
    question: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_consultation_target(self):
        if bool(self.plantId) == bool(self.gardenId):
            raise ValueError("plantId와 gardenId 중 하나만 입력해야 합니다.")
        if self.gardenId and self.careLogId:
            raise ValueError("텃밭 상담에서는 식물별 관리일지를 직접 지정할 수 없습니다.")
        if self.llmProvider == "local" and self.llmModel is not None:
            raise ValueError("로컬 모델을 선택할 때는 llmModel을 지정하지 않습니다.")
        return self


class PlantCareChatResponse(BaseModel):
    summary: str
    possibleCauses: List[str]
    todayActions: List[str]
    observationChecklist: List[str]
    citations: List[Citation]
    safetyNotice: Optional[str] = None
    sessionId: Optional[UUID] = None
    messageId: Optional[UUID] = None
    llmProvider: Optional[Literal["openai", "local"]] = None
    llmModel: Optional[str] = None


class ChatModelInfo(BaseModel):
    chatModel: str
    visionModel: str
    fallbackEnabled: bool = False
    localChatModel: Optional[str] = None
    localVisionModel: Optional[str] = None
    localAuxiliaryEnabled: bool = False
    primaryCircuit: dict = Field(default_factory=dict)
    primaryConfigured: bool = False
    primaryAvailable: bool = False
    localAvailable: bool = False
    availableOpenAIModels: List[str] = Field(default_factory=list)


class ChatFeedbackRequest(BaseModel):
    rating: Literal["helpful", "not_helpful", "unsafe", "irrelevant"]
    comment: Optional[str] = Field(None, max_length=1000)


class ChatFeedbackResponse(BaseModel):
    messageId: UUID
    rating: str
    saved: bool = True


class ChatFeedbackItem(BaseModel):
    messageId: UUID
    rating: str
    comment: Optional[str] = None


class SessionFeedbackStats(BaseModel):
    sessionId: UUID
    title: Optional[str] = None
    helpful: int = 0
    notHelpful: int = 0
    unsafe: int = 0
    irrelevant: int = 0
    total: int = 0


class ChatSession(BaseModel):
    id: UUID
    userId: UUID
    plantId: Optional[UUID] = None
    gardenId: Optional[UUID] = None
    title: Optional[str] = None
    createdAt: datetime


class ChatMessage(BaseModel):
    id: UUID
    sessionId: UUID
    sender: str
    content: str
    citations: Optional[List[Citation]] = []
    createdAt: datetime
