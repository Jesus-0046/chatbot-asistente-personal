"""Configuración centralizada mediante variables de entorno (pydantic-settings).

Todos los secretos llegan por entorno (`.env` o procesos). Nunca se hardcodean
tokens, contraseñas ni API keys en el código.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Entorno
    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    debug: bool = False

    # Telegram
    telegram_token: SecretStr = SecretStr("")
    owner_telegram_ids: list[int] = []

    # Base de datos
    database_url: str = "postgresql+asyncpg://asistente:asistente@localhost:5432/asistente"
    db_echo: bool = False

    # OpenAI (Fase 1)
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    # URL base opcional compatible con la API de OpenAI (vacío = API oficial).
    # Modo local: Ollama expone /v1/chat/completions, p. ej.
    # http://host.docker.internal:11434/v1 desde el contenedor Docker.
    openai_base_url: str = ""
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2
    openai_max_tokens: int = 0  # 0 = sin límite por la app
    # Modo local: tiempo que el servidor mantiene el modelo cargado (y su
    # caché de prefijo) entre peticiones. Con el valor por defecto del servidor
    # (5 min) cada turno tras una pausa paga la recarga y el prefill completo.
    # Solo se envía contra un `base_url` configurado, nunca a la API oficial.
    openai_keep_alive: str = "1h"

    # Conversación
    llm_max_tool_iterations: int = 5
    conversation_window_size: int = 40
    confirmation_ttl_minutes: int = 10

    # Rate limiting básico (in-memory)
    rate_limit_enabled: bool = True
    rate_limit_max_per_minute: int = 30

    # Perfil por defecto
    default_timezone: str = "Europe/Madrid"
    default_lang: str = "es-ES"

    # Agenda y recordatorios (Fase 2)
    reminder_poller_interval_seconds: int = 30
    reminder_recovery_window_minutes: int = 60
    reminder_batch_size: int = 20
    default_reminder_hour: int = 9
    default_reminder_offset_minutes: int = 60
    # Lease del claim atómico: duración del estado `processing` antes de que
    # otro worker pueda reclamar el recordatorio (recuperación de procesos
    # muertos tras claim sin envío).
    reminder_claim_lease_seconds: int = 300

    # Proactividad (Fase 6) — OPT-IN por usuario; el poller solo recorre a los
    # que la tienen activada. El límite diario real vive en la BD por usuario.
    proactive_poller_interval_seconds: int = 300
    proactive_max_users_per_run: int = 500
    # Lease del claim del poller (Fase 6.2): duración del estado `processing`
    # antes de que otro worker pueda reclamar la misma notificación
    # (recuperación de procesos muertos tras claim sin envío).
    proactive_notification_lease_seconds: int = 600

    # WhatsApp (Fase 9) — segundo canal oficial vía WhatsApp Cloud API (Meta).
    # `whatsapp_enabled=False` arranca sin credenciales ni servidor webhook;
    # al activarlo, la validación de startup exige verify/access/app_secret.
    whatsapp_enabled: bool = False
    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_api_base_url: str = "https://graph.facebook.com"
    # Interfaz de escucha del webhook. En Docker/servidor debe ser `0.0.0.0`
    # para que el reverse proxy alcance el contenedor; en local `127.0.0.1`.
    whatsapp_webhook_host: str = "127.0.0.1"
    whatsapp_webhook_port: int = 8088
    whatsapp_http_timeout_seconds: float = 30.0

    # Web API (Fase 10.0) — capa HTTP autenticada con sesiones persistentes.
    # La API es un proceso SEPARADO (entrypoint propio) que comparte la misma BD;
    # `api_enabled` es un interruptor operacional, no de seguridad.
    # `whatsapp_webhook_port` (8088) NO se toca: la API escucha en `api_port`.
    api_enabled: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_docs_enabled: bool = False
    api_cors_origins: list[str] = []
    # Autenticación Web: TTL de sesión (días), TTL del código de enlace y
    # longitud mínima de contraseña (validada en el servicio, no en UI).
    web_session_ttl_days: int = 30
    web_binding_ttl_minutes: int = 30
    web_password_min_length: int = 8
    # Protección básica por IP sobre POST /api/v1/auth/* (en memoria, proceso
    # único; para varios workers se mueve a Redis/Celery en fases posteriores).
    api_rate_limit_auth_per_minute: int = 10
    api_rate_limit_register_per_minute: int = 5

    # Imágenes / OCR (Fase 7) — extracción estructurada de imágenes. Límites
    # configurables; los bytes de la imagen nunca se persisten.
    image_max_size_mb: int = 10
    image_max_per_message: int = 1
    image_max_extracted_chars: int = 20000
    image_timeout_seconds: float = 30.0
    # Formats de imagen admitidos para el análisis visual.
    image_allowed_mime_types: list[str] = ["image/jpeg", "image/png", "image/webp"]

    @property
    def image_max_size_bytes(self) -> int:
        return self.image_max_size_mb * 1024 * 1024

    @property
    def owner_ids_set(self) -> set[int]:
        """Conjunto de IDs autorizados para consultas rápidas (whitelist)."""
        return set(self.owner_telegram_ids)

    def whatsapp_is_configured(self) -> bool:
        """True si el canal WhatsApp tiene todas las credenciales listas.

        Se valida SOLO en el arranque (main) cuando `whatsapp_enabled=True`;
        los tests que simulan el canal pasan las credenciales completas.
        """
        return bool(
            self.whatsapp_enabled
            and self.whatsapp_verify_token.get_secret_value()
            and self.whatsapp_access_token.get_secret_value()
            and self.whatsapp_app_secret.get_secret_value()
        )

    @property
    def whatsapp_media_base_url(self) -> str:
        """URL base para descargar media (imágenes) de la Cloud API."""
        return f"{self.whatsapp_api_base_url}/{self.whatsapp_api_version}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


class ConfigError(RuntimeError):
    """Error de configuración de la aplicación (bloquea el arranque).

    Se distingue del resto de estados de error del proceso: indica que el
    operador debe revisar `.env`/secrets ANTES de que el servicio pueda
    arrancar (no es un fallo de una dependencia externa ni un 503 transitorio).
    """


def validate_critical_env(settings: Settings) -> None:
    """Valida la configuración crítica del proceso PRINCIPAL (bot 24/7).

    - Fallo rápido con mensaje accionable (ConfigError) si falta un secreto
      obligatorio: Telegram siempre; la API key de OpenAI solo se exige contra
      la API oficial (si `openai_base_url` apunta a un proveedor local como
      Ollama no hace falta); WhatsApp si el canal está habilitado.
    - Nunca loguea ni devuelve el valor de los secretos.
    - Se invoca desde `main` (bot) en el arranque.
    """
    missing: list[str] = []
    if not settings.telegram_token.get_secret_value():
        missing.append("TELEGRAM_TOKEN")
    if not settings.openai_api_key.get_secret_value() and not settings.openai_base_url.strip():
        missing.append("OPENAI_API_KEY")
    if settings.whatsapp_enabled:
        missing.extend(
            name
            for name, value in (
                ("WHATSAPP_VERIFY_TOKEN", settings.whatsapp_verify_token.get_secret_value()),
                ("WHATSAPP_ACCESS_TOKEN", settings.whatsapp_access_token.get_secret_value()),
                ("WHATSAPP_APP_SECRET", settings.whatsapp_app_secret.get_secret_value()),
            )
            if not value
        )
    _raise_if_missing(missing)


def validate_api_env(settings: Settings) -> None:
    """Valida la configuración crítica del proceso API Web (proceso separado).

    El API no necesita credenciales de Telegram/WhatsApp ni la API key de
    OpenAI para servir: comparte la misma BD y consume los servicios de
    dominio. Exige una URL de BD configurada (usa credenciales explícitas,
    no el valor de ejemplo) y coherencia `api_docs_enabled`/`api_enabled`.
    """
    missing: list[str] = []
    if not (settings.database_url or "").strip():
        missing.append("DATABASE_URL")
    _raise_if_missing(missing)
    if settings.api_docs_enabled and not settings.api_enabled:
        raise ConfigError("api_docs_enabled=True exige api_enabled=True. Revisa .env (ver .env.example).")


def _raise_if_missing(missing: list[str]) -> None:
    if missing:
        raise ConfigError("Faltan variables obligatorias: " + ", ".join(missing) + ". Revisa .env (ver .env.example).")


def as_dict_for_logging(settings: Settings) -> dict[str, Any]:
    """Representación segura para logs: sin secretos y sin IDs crudos."""
    return {
        "app_env": settings.app_env,
        "log_level": settings.log_level,
        "debug": settings.debug,
        "owner_ids_count": len(settings.owner_ids_set),
    }
