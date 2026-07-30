from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

app = FastAPI(
    title="Farm하니? Plant Care RAG API",
    description="식물 주치의 AI MVP용 FastAPI 서버입니다.",
    version="0.1.0"
)

# CORS 설정 — 허용 도메인은 환경변수 CORS_ORIGINS로 제어 (기본값: 로컬 개발 도메인)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", status_code=200)
async def health_check():
    """
    인프라 및 배포 Health Check를 위한 엔드포인트
    """
    return {
        "status": "healthy",
        "service": "Farm하니? Backend API",
        "version": "0.1.0"
    }

@app.get("/", status_code=200)
async def root():
    return {
        "status": "ok",
        "service": "Farm하니? Backend API",
        "docs": "/docs",
        "health": "/health"
    }

# API 라우터 등록
from app.api.v1.plants import router as plants_router
from app.api.v1.chat import router as chat_router
from app.api.v1.uploads import router as uploads_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.rag import router as rag_router
from app.api.v1.gardens import router as gardens_router

app.include_router(plants_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(uploads_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(gardens_router, prefix="/api/v1")
