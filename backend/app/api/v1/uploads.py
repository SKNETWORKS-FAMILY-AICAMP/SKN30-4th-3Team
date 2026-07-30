import logging
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from supabase import Client

from app.auth.security import get_current_user
from app.db.session import get_supabase_client, get_supabase_service_client
from app.core.config import settings
from app.schemas.plant import PlantPhoto
from app.schemas.garden import GardenPhoto
from app.schemas.upload import UploadSignedUrlRequest, UploadSignedUrlResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["Uploads"])

MAX_PHOTO_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

@router.post("/signed-url", response_model=UploadSignedUrlResponse, status_code=status.HTTP_200_OK, summary="사진 업로드용 signed URL 발급")
def create_signed_upload_url(
    request: UploadSignedUrlRequest,
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    """
    Supabase Storage 사진 업로드용 signed URL을 발급합니다.
    격리된 사용자 경로: users/{user_id}/plants/{uuid}_{filename}
    """
    _, ext = os.path.splitext(request.fileName)
    if ext.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="지원하지 않는 이미지 형식입니다. (jpg, jpeg, png, webp, gif만 허용)"
        )
    if request.mimeType and request.mimeType not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="지원하지 않는 이미지 형식입니다. (jpeg, png, webp, gif만 허용)"
        )
    if request.fileSize is not None and request.fileSize > MAX_PHOTO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="사진 파일이 너무 큽니다. 8MB 이하로 업로드해주세요."
        )
    try:
        # 고유 파일명 생성
        unique_filename = f"{uuid.uuid4()}{ext.lower()}"
        storage_path = f"users/{current_user_id}/plants/{unique_filename}"
        
        # Supabase Storage Signed Upload URL 발급
        bucket_name = settings.SUPABASE_STORAGE_BUCKET
        response = db.storage.from_(bucket_name).create_signed_upload_url(storage_path)
        
        signed_url = response.get("signedUrl") or response.get("signed_url")
        if not signed_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Signed URL 생성에 실패했습니다."
            )
            
        return UploadSignedUrlResponse(
            signedUrl=signed_url,
            storagePath=storage_path
        )
    except Exception:
        logger.exception("API 처리 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Signed URL 발급 중 오류가 발생했습니다."
        )

@router.post("/plant-photo", response_model=PlantPhoto, status_code=status.HTTP_201_CREATED, summary="식물 사진 파일 업로드 및 메타데이터 등록")
async def upload_plant_photo(
    plantId: uuid.UUID = Form(..., description="사진을 연결할 식물 UUID"),
    note: str | None = Form(None, description="사진 메모"),
    capturedAt: str | None = Form(None, description="촬영 시간 ISO 문자열"),
    file: UploadFile = File(..., description="업로드할 식물 사진 파일"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client)
):
    """
    브라우저가 Supabase signed upload URL을 직접 사용할 수 없는 환경을 위한 서버 경유 업로드 API입니다.
    업로드 전 식물 소유권을 검증하고, Storage 저장 후 plant_photos 메타데이터를 등록합니다.
    """
    try:
        plant_check = await run_in_threadpool(
            lambda: db.table("plants").select("id").eq("id", str(plantId)).eq("user_id", str(current_user_id)).execute()
        )
        if not plant_check.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="식물을 찾을 수 없거나 해당 식물에 대한 권한이 없습니다."
            )

        if file.content_type and file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="지원하지 않는 이미지 형식입니다. (jpeg, png, webp, gif만 허용)"
            )

        _, ext = os.path.splitext(file.filename or "")
        safe_ext = ext if ext.lower() in ALLOWED_IMAGE_EXTENSIONS else ".jpg"
        storage_path = f"users/{current_user_id}/plants/{plantId}/{uuid.uuid4()}{safe_ext}"
        # 크기 제한을 초과하는 업로드는 메모리 고갈 방지를 위해 초과 시점에 즉시 중단한다
        content = await file.read(MAX_PHOTO_UPLOAD_BYTES + 1)
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="업로드할 사진 파일이 비어 있습니다."
            )
        if len(content) > MAX_PHOTO_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="사진 파일이 너무 큽니다. 8MB 이하로 업로드해주세요."
            )

        service_db = get_supabase_service_client()
        await run_in_threadpool(
            lambda: service_db.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
                storage_path,
                content,
                file_options={
                    "content-type": file.content_type or "application/octet-stream",
                    "upsert": "false"
                }
            )
        )

        captured_at = datetime.now(timezone.utc)
        if capturedAt:
            captured_at = datetime.fromisoformat(capturedAt.replace("Z", "+00:00"))

        insert_data = {
            "plant_id": str(plantId),
            "storage_path": storage_path,
            "note": note,
            "captured_at": captured_at.isoformat()
        }
        response = await run_in_threadpool(
            lambda: db.table("plant_photos").insert(insert_data).execute()
        )
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="식물 사진 메타데이터 등록에 실패했습니다."
            )

        item = response.data[0]
        return PlantPhoto(
            id=uuid.UUID(item["id"]),
            plantId=uuid.UUID(item["plant_id"]),
            storagePath=item["storage_path"],
            capturedAt=datetime.fromisoformat(item["captured_at"]),
            note=item.get("note"),
            createdAt=datetime.fromisoformat(item["created_at"])
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("API 처리 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="식물 사진 업로드 중 오류가 발생했습니다."
        )


@router.post("/garden-photo", response_model=GardenPhoto, status_code=status.HTTP_201_CREATED, summary="텃밭 사진 파일 업로드 및 메타데이터 등록")
async def upload_garden_photo(
    gardenId: uuid.UUID = Form(..., description="사진을 연결할 텃밭 UUID"),
    note: str | None = Form(None, description="사진 메모"),
    capturedAt: str | None = Form(None, description="촬영 시간 ISO 문자열"),
    file: UploadFile = File(..., description="업로드할 텃밭 사진 파일"),
    current_user_id: uuid.UUID = Depends(get_current_user),
    db: Client = Depends(get_supabase_client),
):
    try:
        garden_check = await run_in_threadpool(
            lambda: db.table("gardens").select("id").eq("id", str(gardenId)).eq("user_id", str(current_user_id)).execute()
        )
        if not garden_check.data:
            raise HTTPException(status_code=404, detail="텃밭을 찾을 수 없거나 해당 텃밭에 대한 권한이 없습니다.")
        if file.content_type and file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            raise HTTPException(status_code=415, detail="지원하지 않는 이미지 형식입니다. (jpeg, png, webp, gif만 허용)")

        _, ext = os.path.splitext(file.filename or "")
        safe_ext = ext if ext.lower() in ALLOWED_IMAGE_EXTENSIONS else ".jpg"
        storage_path = f"users/{current_user_id}/gardens/{gardenId}/{uuid.uuid4()}{safe_ext}"
        content = await file.read(MAX_PHOTO_UPLOAD_BYTES + 1)
        if not content:
            raise HTTPException(status_code=400, detail="업로드할 사진 파일이 비어 있습니다.")
        if len(content) > MAX_PHOTO_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="사진 파일이 너무 큽니다. 8MB 이하로 업로드해주세요.")

        service_db = get_supabase_service_client()
        await run_in_threadpool(
            lambda: service_db.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
                storage_path,
                content,
                file_options={"content-type": file.content_type or "application/octet-stream", "upsert": "false"},
            )
        )
        captured_at = datetime.now(timezone.utc)
        if capturedAt:
            captured_at = datetime.fromisoformat(capturedAt.replace("Z", "+00:00"))
        payload = {
            "plant_id": None,
            "garden_id": str(gardenId),
            "storage_path": storage_path,
            "note": note,
            "captured_at": captured_at.isoformat(),
        }
        response = await run_in_threadpool(lambda: db.table("plant_photos").insert(payload).execute())
        if not response.data:
            raise HTTPException(status_code=500, detail="텃밭 사진 메타데이터 등록에 실패했습니다.")
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
        logger.exception("텃밭 사진 업로드 중 오류 발생")
        raise HTTPException(status_code=500, detail="텃밭 사진 업로드 중 오류가 발생했습니다.")
