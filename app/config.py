from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_sqlalchemy_postgres_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


class Settings(BaseSettings):
    database_provider: str = "Docker PostgreSQL"
    database_url: str = "postgresql://app_admin:app_password@app-db:5432/heart_failure"

    jwt_secret_key: str = "DEV-ONLY-CHANGE-ME"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    allowed_origins: str = "http://localhost:5173,http://localhost"
    rate_limit_per_minute: int = 600

    fhir_base_url: str = "http://hapi-fhir:8080/fhir"
    fhir_timeout_seconds: int = 10
    fhir_subscription_callback: str = "http://api:8000/fhir/events/observation"
    fhir_hook_secret: str = "DEV-FHIR-HOOK-CHANGE-ME"

    mongo_url: str = "mongodb://mongo:27017"
    mongo_database: str = "heart_failure_billing"

    ml_base_url: str = "http://ml:8001"
    ml_timeout_seconds: int = 30
    critical_risk_threshold: float = 0.70

    orthanc_base_url: str = "http://orthanc:8042"
    orthanc_timeout_seconds: int = 30

    alert_escalation_seconds: int = 120
    public_base_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        return normalize_sqlalchemy_postgres_url(self.database_url)

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]


settings = Settings()