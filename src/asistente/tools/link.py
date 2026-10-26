"""Herramientas de enlace de canales: `link_whatsapp` (Fase 9) y `link_web` (Fase 10.0).

`link_whatsapp` se ejecuta SIEMPRE desde Telegram (chat autenticado). El usuario
pega el código de 8 caracteres que recibió al escribir por primera vez al bot en
WhatsApp; el servicio consume la identidad pendiente (single-use, 30 min).

`link_web` genera el código de enlace para que un usuario que YA habla por
Telegram/WhatsApp cree sus credenciales Web (email + contraseña) sin volver a
registrarse. El código va atado al email que el usuario escribirá en el panel.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


class LinkWhatsAppArgs(BaseModel):
    code: str = Field(min_length=1, max_length=20, description="Código de 8 caracteres recibido por WhatsApp")


async def _link_whatsapp(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkWhatsAppArgs(**kwargs)
    identities = ctx.services.channel_identities
    if identities is None:
        return ToolResult(ok=False, error="La vinculación de canales no está disponible.")
    try:
        linked = await identities.link_with_code(
            code=args.code,
            user_id=ctx.user_id,
            channel=CHANNEL_WHATSAPP,
        )
    except Exception:  # noqa: BLE001
        return ToolResult(ok=False, error="Error interno al vincular el número. Inténtalo de nuevo.")
    if linked is None:
        return ToolResult(
            ok=False,
            error=(
                "El código no es válido, ha caducado o ya se ha usado. "
                "Escribe de nuevo por WhatsApp para que se genere uno nuevo."
            ),
        )
    return ToolResult(
        ok=True,
        data={
            "channel": CHANNEL_WHATSAPP,
            "description_text": (
                "¡Listo! Tu número de WhatsApp queda vinculado a tu asistente. Ya puedes escribirme por allí."
            ),
        },
    )


class LinkWebArgs(BaseModel):
    email: str = Field(min_length=3, max_length=255, description="Email que el usuario usará en el panel Web")


async def _link_web(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkWebArgs(**kwargs)
    web_auth = ctx.services.web_auth
    if web_auth is None:
        return ToolResult(ok=False, error="La vinculación Web no está disponible.")
    try:
        code = await web_auth.issue_binding_code(ctx.user_id, email=args.email)
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "args", None):
            message = str(exc.args[0]) if len(exc.args) == 1 else "No se pudo generar el código."
        else:
            message = "No se pudo generar el código."
        return ToolResult(ok=False, error=message)
    ttl = ctx.services.settings.web_binding_ttl_minutes
    return ToolResult(
        ok=True,
        data={
            "channel": "web",
            "code": code,
            "email": args.email,
            "ttl_minutes": ttl,
            "description_text": (
                f"Para entrar por Web usa el email {args.email} y este código: "
                f"**{code}** (caduca en {ttl} minutos y es de un solo uso). "
                "Ábrelo en el panel de acceso Web cuando te lo pida."
            ),
        },
    )


LINK_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="link_whatsapp",
        description=(
            "Vincula tu número de WhatsApp a tu usuario mediante el código de 8 caracteres que "
            "recibiste al escribir por primera vez en WhatsApp (caduca en 30 minutos y es de un "
            "solo uso). Solo tiene sentido cuando hay un código pendiente. "
            "'Vincular mi WhatsApp' o 'tengo un código' -> esta tool con el código."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Código de 8 caracteres recibido por WhatsApp",
                },
            },
            "required": ["code"],
        },
        handler=_link_whatsapp,
        args_model=LinkWhatsAppArgs,
    ),
    ToolDefinition(
        name="link_web",
        description=(
            "Genera un código de enlace para que tu usuario pueda crear sus credenciales Web "
            "(email y contraseña) en el panel del asistente sin necesidad de registrarse de nuevo. "
            "Solo hace falta si el usuario ya habla contigo por Telegram/WhatsApp y quiere entrar "
            "también por Web. Debes pedirle el email que va a usar. "
            "'Quiero entrar por web' o 'vincular mi email' -> esta tool con el email."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Email que el usuario escribirá en el formulario Web (debe coincidir exacto)",
                },
            },
            "required": ["email"],
        },
        handler=_link_web,
        args_model=LinkWebArgs,
    ),
]
