import os
from typing import List, Union
from pydantic import AnyHttpUrl, BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Annotated

def check_cors_origins(v: Union[str, List[str]]) -> Union[List[str], str]:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list):
        return v
    raise ValueError(v)

class Settings(BaseSettings):
    PROJECT_NAME: str = "Farm하니? Plant Care RAG API"
    API_V1_STR: str = "/api/v1"
    
    # CORS Origins — 운영 배포 시 Render 환경변수 CORS_ORIGINS에 프론트 도메인을 콤마로 등록한다.
    # (예: CORS_ORIGINS=https://farmhani.vercel.app,https://farmhani.example.com)
    CORS_ORIGINS: Annotated[
        Union[List[str], str], BeforeValidator(check_cors_origins)
    ] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Supabase 설정
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "plant-photos"

    # JWT 로컬 검증 설정
    # SUPABASE_JWT_SECRET: 레거시(HS256) 프로젝트에서만 필요. 비워두면 JWKS 공개키 검증 사용.
    SUPABASE_JWT_SECRET: str = ""
    JWT_AUDIENCE: str = "authenticated"

    # OpenAI & LLM 설정
    OPENAI_API_KEY: str = ""
    CHAT_MODEL: str = "gpt-4o-mini"
    VISION_MODEL: str = "gpt-4o-mini"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LLM_FALLBACK_ENABLED: bool = False
    LOCAL_LLM_AUXILIARY_ENABLED: bool = False
    LOCAL_LLM_BASE_URL: str = "http://127.0.0.1:11434/v1"
    LOCAL_LLM_API_KEY: str = "ollama"
    LOCAL_CHAT_MODEL: str = "qwen3-vl:4b-instruct"
    LOCAL_VISION_MODEL: str = "qwen3-vl:4b-instruct"
    LLM_FAILURE_THRESHOLD: int = 2
    LLM_CIRCUIT_OPEN_SECONDS: float = 60.0
    LOCAL_LLM_TIMEOUT_SECONDS: float = 120.0

    # Pydantic Settings가 파일을 읽어들일 위치 후보군 지정
    # 프로젝트 루트(.env) 또는 backend 폴더 내부(.env) 어디서든 환경변수를 불러올 수 있게 지원합니다.
    model_config = SettingsConfigDict(
        env_file=(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../.env"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.env"),
            ".env"
        ),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
