import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, status
from supabase import Client

from app.auth.security import get_current_user
from app.db.session import get_supabase_client
from app.schemas.garden import Garden, GardenCreate, GardenPhoto, GardenPhotoCreate, GardenUpdate


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gardens", tags=["Gardens"])


def garden_response(item: dict, plant_count: int = 0) -> Garden:
    return Garden(
        id=uuid.UUID(item["id"]),
        name=item["name"],
        location=item.get("location"),
        description=item.get("description"),
        sunlight=item.get("sunlight"),
        soilType=item.get("soil_type"),
        imageUrl=item.get("image_url"),
        cultivationType=item.get("cultivation_type") or "mixed",
        representativeCrop=item.get("representative_crop"),
        plantCount=plant_count,
        createdAt=datetime.fromisoformat(item["created_at"]),
    )


def owned_garden(garden_id: uuid.UUID, user_id: uuid.UUID, db: Client) -> dict:
    response = (
        db.table("gardens")
        .select("*")
        .eq("id", str(garden_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="텃밭을 찾을 수 없거나 접근 권한이 없습니다.",
        )
    return response.data[0]


@router.get("", response_model=List[Garden], summary="사용자의 텃밭 목록 조회")
def list_gardens(
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
):
    try:
        gardens_response = (
            db.table("gardens")
            .select("*")
            .eq("user_id", str(current_user_id))
            .order("created_at", desc=True)
            .execute()
        )
        plants_response = db.table("plants").select("garden_id").eq("user_id", str(current_user_id)).execute()
        counts: dict[str, int] = {}
        for plant in plants_response.data or []:
            garden_id = plant.get("garden_id")
            if garden_id:
                counts[garden_id] = counts.get(garden_id, 0) + 1
        return [garden_response(item, counts.get(item["id"], 0)) for item in gardens_response.data or []]
    except HTTPException:
        raise
    except Exception:
        logger.exception("텃밭 목록 조회 중 오류 발생")
        raise HTTPException(status_code=500, detail="텃밭 목록 조회 중 오류가 발생했습니다.")


@router.post("", response_model=Garden, status_code=status.HTTP_201_CREATED, summary="텃밭 생성")
def create_garden(
    garden_in: GardenCreate,
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
):
    try:
        payload = {
            "user_id": str(current_user_id),
            "name": garden_in.name.strip(),
            "location": garden_in.location,
            "description": garden_in.description,
            "sunlight": garden_in.sunlight,
            "soil_type": garden_in.soilType,
            "cultivation_type": garden_in.cultivationType,
            "representative_crop": garden_in.representativeCrop,
        }
        response = db.table("gardens").insert(payload).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="텃밭 생성에 실패했습니다.")
        return garden_response(response.data[0])
    except HTTPException:
        raise
    except Exception:
        logger.exception("텃밭 생성 중 오류 발생")
        raise HTTPException(status_code=500, detail="텃밭 생성 중 오류가 발생했습니다.")


@router.patch("/{gardenId}", response_model=Garden, summary="텃밭 수정")
def update_garden(
    garden_in: GardenUpdate,
    gardenId: uuid.UUID = Path(..., description="텃밭 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
):
    try:
        current = owned_garden(gardenId, current_user_id, db)
        field_map = {
            "name": "name",
            "location": "location",
            "description": "description",
            "sunlight": "sunlight",
            "soilType": "soil_type",
            "imageUrl": "image_url",
            "cultivationType": "cultivation_type",
            "representativeCrop": "representative_crop",
        }
        payload = {
            column: getattr(garden_in, field)
            for field, column in field_map.items()
            if field in garden_in.model_fields_set
        }
        if "name" in payload and payload["name"] is not None:
            payload["name"] = payload["name"].strip()
        if "representative_crop" in payload and payload["representative_crop"] is not None:
            payload["representative_crop"] = payload["representative_crop"].strip() or None
        next_type = payload.get("cultivation_type", current.get("cultivation_type") or "mixed")
        next_crop = payload.get("representative_crop", current.get("representative_crop"))
        if next_type == "single" and not next_crop:
            raise HTTPException(status_code=422, detail="단일 작물 텃밭은 대표 작물을 입력해야 합니다.")
        if payload:
            response = (
                db.table("gardens")
                .update(payload)
                .eq("id", str(gardenId))
                .eq("user_id", str(current_user_id))
                .execute()
            )
            if not response.data:
                raise HTTPException(status_code=404, detail="수정할 텃밭을 찾을 수 없습니다.")
            current = response.data[0]
        plants_response = (
            db.table("plants")
            .select("id")
            .eq("garden_id", str(gardenId))
            .eq("user_id", str(current_user_id))
            .execute()
        )
        return garden_response(current, len(plants_response.data or []))
    except HTTPException:
        raise
    except Exception:
        logger.exception("텃밭 수정 중 오류 발생")
        raise HTTPException(status_code=500, detail="텃밭 수정 중 오류가 발생했습니다.")


@router.post("/{gardenId}/photos", response_model=GardenPhoto, status_code=status.HTTP_201_CREATED, summary="텃밭 사진 메타데이터 등록")
def create_garden_photo(
    photo_in: GardenPhotoCreate,
    gardenId: uuid.UUID = Path(..., description="텃밭 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
):
    try:
        owned_garden(gardenId, current_user_id, db)
        if not photo_in.storagePath.startswith(f"users/{current_user_id}/"):
            raise HTTPException(status_code=400, detail="현재 사용자의 저장 경로만 등록할 수 있습니다.")
        payload = {
            "plant_id": None,
            "garden_id": str(gardenId),
            "storage_path": photo_in.storagePath,
            "note": photo_in.note,
            "captured_at": (photo_in.capturedAt or datetime.now().astimezone()).isoformat(),
        }
        response = db.table("plant_photos").insert(payload).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="텃밭 사진 등록에 실패했습니다.")
        item = response.data[0]
        return GardenPhoto(
            id=uuid.UUID(item["id"]),
            gardenId=uuid.UUID(item["garden_id"]),
            storagePath=item["storage_path"],
            capturedAt=datetime.fromisoformat(item["captured_at"]),
            note=item.get("note"),
            createdAt=datetime.fromisoformat(item["created_at"]),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("텃밭 사진 등록 중 오류 발생")
        raise HTTPException(status_code=500, detail="텃밭 사진 등록 중 오류가 발생했습니다.")


@router.delete("/{gardenId}", status_code=status.HTTP_204_NO_CONTENT, summary="텃밭 삭제")
def delete_garden(
    gardenId: uuid.UUID = Path(..., description="텃밭 UUID"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
):
    try:
        owned_garden(gardenId, current_user_id, db)
        (
            db.table("plants")
            .update({"garden_id": None})
            .eq("garden_id", str(gardenId))
            .eq("user_id", str(current_user_id))
            .execute()
        )
        (
            db.table("gardens")
            .delete()
            .eq("id", str(gardenId))
            .eq("user_id", str(current_user_id))
            .execute()
        )
        return None
    except HTTPException:
        raise
    except Exception:
        logger.exception("텃밭 삭제 중 오류 발생")
        raise HTTPException(status_code=500, detail="텃밭 삭제 중 오류가 발생했습니다.")
