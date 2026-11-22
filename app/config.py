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

    # RAG clínico (Semana 12)
    rag_metodo: str = "hibrido"          # bm25 | denso | hibrido
    rag_umbral_cos: float = 0.35         # abstención: similitud coseno máxima mínima
    rag_umbral_bm25: float = 4.0         # abstención: respaldo léxico (BM25 solo no separa bien lo ajeno)
    groq_api_key: str | None = None      # sin llave: respuesta extractiva con citas
    groq_model: str = "openai/gpt-oss-120b"  # qwen/qwen3-32b fue retirado por Groq en julio de 2026

    # Agente clínico (Semana 13)
    agente_modelo: str = "auto"              # auto (Groq si hay llave) | simulado (pruebas sin gastar tokens)
    agente_max_simultaneas: int = 3          # semáforo: llamadas simultáneas al proveedor
    agente_timeout_s: float = 90.0           # tiempo máximo por solicitud
    agente_max_llamadas_modelo: int = 8      # pasos máximos por turno
    agente_ventana_mensajes: int = 20        # mensajes que se envían al modelo (el turno actual nunca se corta)

    # Seguridad (proyecto final): R27 llave de cifrado de campos y R28 llave de la cadena de auditoría.
    # Viven solo en el entorno (.env); sin valor se derivan de JWT_SECRET_KEY.
    campos_clave: str | None = None
    auditoria_clave: str | None = None
    # R25: intentos de prompt injection antes del bloqueo temporal (por usuario y por IP) y su duración
    agente_inyeccion_max: int = 3
    agente_inyeccion_max_ip: int = 5
    agente_bloqueo_minutos: int = 15

    # Interoperabilidad entre instituciones (proyecto final, R29–R33)
    interop_nombre: str = "Heart Failure Monitor (UAO)"          # cómo nos ve el socio (requester/owner)
    interop_sistema_base: str = "https://hfmonitor.uao.edu.co/fhir"  # base de nuestros identifier.system
    interop_secreto_federado: str = "secreto-federado-del-curso-solo-demo"  # el mismo que los socios
    interop_clave_cifrado: str | None = None   # cifra los secretos de socios; sin valor se deriva de JWT_SECRET_KEY
    interop_token_minutos: int = 10
    interop_timeout_s: float = 20.0
    interop_max_bytes: int = 1_048_576         # 1 MB por Bundle

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
