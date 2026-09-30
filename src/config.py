"""
AeroPredict Configuration
Pydantic settings for environment-based configuration
"""

from functools import lru_cache
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "aeropredict"
    app_env: str = Field(default="development", alias="APP_ENV")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_workers: int = Field(default=2, alias="API_WORKERS")

    # MLflow
    mlflow_tracking_uri: str = Field(default="http://localhost:5000", alias="MLFLOW_TRACKING_URI")
    mlflow_model_name: str = Field(default="aeropredict-rul-model", alias="MLFLOW_MODEL_NAME")
    mlflow_model_stage: str = Field(default="Production", alias="MLFLOW_MODEL_STAGE")

    # Ollama (Local LLM)
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="llama3.2", alias="OLLAMA_MODEL")
    ollama_timeout: int = Field(default=30, alias="OLLAMA_TIMEOUT")

    # RAG Settings
    enable_rag: bool = Field(default=True, alias="ENABLE_RAG")
    rag_top_k: int = Field(default=5, alias="RAG_TOP_K")
    rag_similarity_threshold: float = Field(default=0.7, alias="RAG_SIMILARITY_THRESHOLD")
    vector_db_path: str = Field(default="data/vector_db", alias="VECTOR_DB_PATH")
    manuals_path: str = Field(default="data/manuals", alias="MANUALS_PATH")

    # Monitoring
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")
    metrics_port: int = Field(default=9090, alias="METRICS_PORT")
    prometheus_pushgateway: Optional[str] = Field(default=None, alias="PROMETHEUS_PUSHGATEWAY")

    # Database
    database_url: str = Field(default="postgresql://airflow:airflow@localhost:5432/airflow", alias="DATABASE_URL")

    # MinIO/S3
    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    mlflow_artifact_bucket: str = Field(default="mlflow", alias="MLFLOW_ARTIFACT_BUCKET")

    # Airflow
    airflow_url: str = Field(default="http://localhost:8080", alias="AIRFLOW_URL")
    airflow_user: str = Field(default="airflow", alias="AIRFLOW_USER")
    airflow_password: str = Field(default="airflow", alias="AIRFLOW_PASSWORD")

    # Security
    secret_key: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    cors_origins: List[str] = Field(default=["*"], alias="CORS_ORIGINS")

    # Model
    model_path: str = Field(default="models/rul_model.pth", alias="MODEL_PATH")
    sequence_length: int = Field(default=50, alias="SEQUENCE_LENGTH")
    prediction_horizon: int = Field(default=1, alias="PREDICTION_HORIZON")

    # Health Check
    health_check_interval: int = Field(default=30, alias="HEALTH_CHECK_INTERVAL")

    # Sentry
    sentry_dsn: Optional[str] = Field(default=None, alias="SENTRY_DSN")
    sentry_environment: str = Field(default="development", alias="SENTRY_ENVIRONMENT")
    sentry_release: str = Field(default="1.0.0", alias="SENTRY_RELEASE")
    sentry_traces_sample_rate: float = Field(default=0.1, alias="SENTRY_TRACES_SAMPLE_RATE")

    # Flask Admin
    flask_admin_port: int = Field(default=8081, alias="FLASK_ADMIN_PORT")
    flask_secret_key: str = Field(default="change-me", alias="FLASK_SECRET_KEY")


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Export for easy access
settings = get_settings()