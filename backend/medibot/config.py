from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / '.env', ROOT / 'backend' / '.env'),
        env_file_encoding='utf-8', extra='ignore', env_ignore_empty=True,
    )
    data_dir: Path = ROOT / 'data'
    database_path: Path = ROOT / 'data' / 'db' / 'mediassist.db'
    qdrant_path: Path = ROOT / '.state' / 'qdrant'
    qdrant_url: str | None = None
    qdrant_api_key: str | None = Field(default=None, repr=False)
    qdrant_collection: str = 'medibot'
    model_cache_dir: Path = ROOT / '.state' / 'models'
    embedding_model: str = 'BAAI/bge-small-en-v1.5'
    reranker_model: str = 'jinaai/jina-reranker-v1-tiny-en'
    retrieval_candidates: int = Field(default=10, ge=3, le=50)
    rerank_top_k: int = Field(default=3, ge=1, le=10)
    groq_api_key: str = Field(default='', repr=False)
    groq_model: str = 'llama-3.3-70b-versatile'
    groq_timeout_seconds: float = 45
    tenant_id: str = Field(default='', repr=False)
    ai_client_id: str = Field(default='', repr=False)
    ai_client_secret: str = Field(default='', repr=False)
    model_serving_scope: str = Field(default='', repr=False)
    bedrock_api_url: str = Field(default='', repr=False)
    ssl_ca_bundle: str = ''
    bedrock_timeout_seconds: float = Field(default=45, gt=0, le=120)
    bedrock_auth_timeout_seconds: float = Field(default=15, gt=0, le=60)
    groq_fallback_enabled: bool = True

    jwt_secret: str = Field(default='', repr=False)
    doctor_username: str = Field(default='', repr=False)
    doctor_password: str = Field(default='', repr=False)
    nurse_username: str = Field(default='', repr=False)
    nurse_password: str = Field(default='', repr=False)
    billing_executive_username: str = Field(default='', repr=False)
    billing_executive_password: str = Field(default='', repr=False)
    technician_username: str = Field(default='', repr=False)
    technician_password: str = Field(default='', repr=False)
    admin_username: str = Field(default='', repr=False)
    admin_password: str = Field(default='', repr=False)
    session_hours: int = 8
    sql_max_rows: int = 100
    sql_timeout_seconds: float = 3
    reference_date: str | None = None
    cors_origins: str = 'http://localhost:3000,http://127.0.0.1:3000'


@lru_cache
def get_settings() -> Settings:
    return Settings()
