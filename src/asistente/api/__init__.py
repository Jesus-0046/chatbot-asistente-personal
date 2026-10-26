"""Capa HTTP autenticada de la aplicación (Fase 10.0).

FastAPI/Uvicorn en proceso separado que reutiliza al 100% las capas de
dominio, aplicación y persistencia existentes. Las reglas:

- Todo endpoint autenticado resuelve el `user_id` desde la sesión Bearer
  (`services.web_auth.authenticate`); NUNCA desde el cuerpo de la petición.
- Los routers son capas finas: validación de entrada + serialización
  explícita. Nada de SQL ni lógica de negocio aquí.
- Errores uniformes: `{"error": {"code", "message", "request_id"}}` con
  códigos por estado (400/401/403/404/409/422/429/500).
- Valores `Decimal` → string; datetimes → ISO 8601; `X-Request-ID` en todas
  las respuestas.
"""

from asistente.api.app import app, create_app

__all__ = ["app", "create_app"]
