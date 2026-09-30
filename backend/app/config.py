"""All runtime configuration comes from environment variables (prefix HSA_)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HSA_", env_file=None, extra="ignore")

    env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://hsa:hsa@localhost:5432/hsa"
    secret_key: str = "dev-insecure-change-me"

    # public URL of the site (used in password-reset links, payment return pages)
    public_base_url: str = "http://localhost:8620"
    cookie_secure: bool = False
    session_days: int = 30

    # upstream question bank (read-only) and app media store
    upstream_root: Path = Path("/upstream")
    media_root: Path = Path("/data/media")

    # payment webhooks: shared secrets per provider (never stored in the DB or in git)
    sepay_api_key: str = ""
    casso_secure_token: str = ""
    generic_webhook_secret: str = ""

    # email (optional; without SMTP the reset link is written to the log for admins)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    rate_limits: bool = True  # disable only in tests
    log_level: str = "INFO"
    trust_proxy_headers: bool = True

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (s.secret_key.startswith("dev-") or len(s.secret_key) < 32):
        raise RuntimeError("HSA_SECRET_KEY must be set to a random value of at least 32 characters in production")
    return s
