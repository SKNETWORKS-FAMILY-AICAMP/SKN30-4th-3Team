from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from app.schemas.care_log import CareLog

class PlantCreate(BaseModel):
    name: str = Field(..., description="식물의 이름")
    species: Optional[str] = Field(None, description="식물의 종류")
    location: Optional[str] = Field(None, json_schema_extra={"example": "베란다"}, description="식물이 위치한 공간")
    sunlight: Optional[str] = Field(None, json_schema_extra={"example": "오전 직사광선"}, description="식물이 받는 햇빛의 종류/양")
    imageUrl: Optional[str] = Field(None, description="식물 대표 이미지 URL")
    gardenId: Optional[UUID] = Field(None, description="소속 텃밭 UUID")

class Plant(PlantCreate):
    id: UUID
    createdAt: datetime

class PlantUpdate(BaseModel):
    name: Optional[str] = Field(None, description="식물의 새 별명")
    species: Optional[str] = Field(None, description="식물의 새 품종명")
    location: Optional[str] = Field(None, description="식물의 새 위치")
    sunlight: Optional[str] = Field(None, description="식물의 새 햇빛 환경")
    imageUrl: Optional[str] = Field(None, description="식물의 새 대표 이미지 URL")
    gardenId: Optional[UUID] = Field(None, description="소속 텃밭 UUID. null이면 배정을 해제합니다.")

class PlantPhotoCreate(BaseModel):
    storagePath: str = Field(..., description="Supabase Storage나 R2에 저장된 파일 경로")
    capturedAt: Optional[datetime] = Field(None, description="사진 촬영 시간")
    note: Optional[str] = Field(None, description="사진에 대한 메모/메모")

class PlantPhoto(PlantPhotoCreate):
    id: UUID
    plantId: UUID
    createdAt: datetime

class PlantDetail(Plant):
    careLogs: List[CareLog] = Field(default_factory=list, description="식물 재배 일지 목록")
    photos: List[PlantPhoto] = Field(default_factory=list, description="식물 사진 히스토리 목록")

class ChecklistTask(BaseModel):
    id: str = Field(..., description="합성 태스크 ID (taskType:plantId)")
    plantId: UUID = Field(..., description="식물 UUID")
    plantName: str = Field(..., description="식물 이름")
    taskType: str = Field(..., description="water(물주기) | observe(상태 기록) | photo(성장 사진)")
    title: str = Field(..., description="체크리스트 항목 제목")
    description: str = Field(..., description="항목 보조 설명")
    done: bool = Field(..., description="오늘 완료 여부")

class WateringReminder(BaseModel):
    plantId: UUID = Field(..., description="식물 UUID")
    name: str = Field(..., description="식물 이름")
    species: Optional[str] = Field(None, description="식물 품종")
    lastWateredAt: Optional[str] = Field(None, description="마지막 물 준 날짜 (ISO date, 기록 없으면 null)")
    daysSinceWatered: Optional[int] = Field(None, description="마지막 물주기 이후 경과 일수")
    intervalDays: int = Field(..., description="권장 물주기 간격 (일)")
    status: str = Field(..., description="due(물 줄 때) | upcoming(1일 이내 도래) | ok(여유) | unknown(기록 없음)")
