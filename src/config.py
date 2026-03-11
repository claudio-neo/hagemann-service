from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Configuración del hagemann-service"""

    # Base de datos
    database_url: str = "postgresql://postgres:localdev@postgres:5432/neofreight"

    # Keycloak
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "neofreight"
    keycloak_client_id: str = "neofreight-api"

    # Servicio
    service_name: str = "hagemann-service"
    debug: bool = True

    # Schema
    db_schema: str = "hagemann"

    # JWT
    jwt_secret: str = "hagemann-super-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8 horas

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
