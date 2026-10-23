from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    org_name: str = "My Organization"
    database_url: str = "sqlite+aiosqlite:///./aether.db"
    # Fernet key or any passphrase; used to encrypt provider secrets at rest.
    secret_key: str = "change-me-in-production"

    auth_mode: Literal["none", "api_key", "oidc"] = "none"
    # First admin key for api_key/oidc deployments (must start with "ak_"); created on startup if missing.
    bootstrap_api_key: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"])
    log_level: str = "INFO"
    log_json: bool = True

    worker_concurrency: int = 16
    worker_poll_interval_s: float = 1.0
    job_lease_s: int = 300
    max_job_attempts: int = 3

    # Default models used by Aether itself (task generation, tool simulation, user simulation, judging).
    # Expressed as "<provider name>/<model name>"; can be overridden per run.
    default_generator_model: str | None = None
    default_judge_model: str | None = None
    default_simulator_model: str | None = None

    embedded_worker: bool = False
    auto_create_schema: bool = False
    static_dir: str | None = None

    request_timeout_s: float = 120.0
    # TLS trust for outbound calls: "system" uses the OS trust store (picks up corporate CAs),
    # "certifi" uses Mozilla's bundle. ca_bundle adds a PEM file (e.g. mounted in containers).
    tls_trust: Literal["system", "certifi"] = "system"
    ca_bundle: str | None = None
    seed_samples: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
