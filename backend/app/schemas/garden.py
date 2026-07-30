from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class GardenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40, description="텃밭 이름")
    location: Optional[str] = Field(None, max_length=60, description="텃밭 위치")
    description: Optional[str] = Field(None, max_length=80, description="텃밭 설명")
    sunlight: Optional[str] = Field(None, max_length=40, description="공통 일조 환경")
    soilType: Optional[str] = Field(None, max_length=40, description="토양 종류")
    cultivationType: Literal["single", "mixed"] = Field("mixed", description="단일 작물 또는 여러 작물 텃밭")
    representativeCrop: Optional[str] = Field(None, max_length=80, description="대표 작물명")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("텃밭 이름은 비워둘 수 없습니다.")
        return value

    @model_validator(mode="after")
    def validate_representative_crop(self):
        if self.cultivationType == "single" and not (self.representativeCrop or "").strip():
            raise ValueError("단일 작물 텃밭은 대표 작물을 입력해야 합니다.")
        if self.representativeCrop:
            self.representativeCrop = self.representativeCrop.strip()
        return self


class GardenUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40, description="텃밭 이름")
    location: Optional[str] = Field(None, max_length=60, description="텃밭 위치")
    description: Optional[str] = Field(None, max_length=80, description="텃밭 설명")
    sunlight: Optional[str] = Field(None, max_length=40, description="공통 일조 환경")
    soilType: Optional[str] = Field(None, max_length=40, description="토양 종류")
    imageUrl: Optional[str] = Field(None, description="텃밭 대표 이미지 URL")
    cultivationType: Optional[Literal["single", "mixed"]] = None
    representativeCrop: Optional[str] = Field(None, max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("텃밭 이름은 비워둘 수 없습니다.")
        return value


class Garden(GardenCreate):
    id: UUID
    plantCount: int = 0
    imageUrl: Optional[str] = None
    createdAt: datetime


class GardenPhotoCreate(BaseModel):
    storagePath: str = Field(..., description="Supabase Storage에 저장된 파일 경로")
    capturedAt: Optional[datetime] = None
    note: Optional[str] = None


class GardenPhoto(GardenPhotoCreate):
    id: UUID
    gardenId: UUID
    createdAt: datetime
