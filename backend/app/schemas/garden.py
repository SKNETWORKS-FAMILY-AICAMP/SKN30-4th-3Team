from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class GardenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40, description="텃밭 이름")
    location: Optional[str] = Field(None, max_length=60, description="텃밭 위치")
    description: Optional[str] = Field(None, max_length=80, description="텃밭 설명")
    sunlight: Optional[str] = Field(None, max_length=40, description="공통 일조 환경")
    soilType: Optional[str] = Field(None, max_length=40, description="토양 종류")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("텃밭 이름은 비워둘 수 없습니다.")
        return value


class GardenUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40, description="텃밭 이름")
    location: Optional[str] = Field(None, max_length=60, description="텃밭 위치")
    description: Optional[str] = Field(None, max_length=80, description="텃밭 설명")
    sunlight: Optional[str] = Field(None, max_length=40, description="공통 일조 환경")
    soilType: Optional[str] = Field(None, max_length=40, description="토양 종류")

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
