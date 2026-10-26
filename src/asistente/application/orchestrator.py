"""Orquestador principal de conversación (Fase 1).

Flujo canal-agnóstico:

  InboundMessage
    → ensure_user (identidad desde contexto autenticado, nunca del LLM)
    → rate limit básico (si está activado), clave = usuario interno
    → si hay confirmación pendiente, se gestiona el 'sí'/'no'
    → construir mensajes (system + historial reciente + reglas de memoria)
    → bucle LLM (máx. `llm_max_tool_iterations`)
        → tool calls => registry.execute() con validación Pydantic
        → si la tool requiere confirmación => se crea pendiente y se responde
    → persistir el resultado en historial
    → OutboundMessage

El LLM NUNCA toca la BD: `ToolContext` solo expone servicios de dominio.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from asistente.ai.llm import LLMClient, LLMError
from asistente.ai.prompts import build_local_system_prompt, build_system_prompt
from asistente.ai.vision import ImageExtractionError, ImageUnderstandingProvider, OpenAIImageUnderstandingClient
from asistente.application.answers import (
    FINAL_ANSWER_INSTRUCTION,
    build_fallback_reply,
    looks_like_raw_payload,
)
from asistente.application.confirmations import classify_user_intent
from asistente.application.context import ImageAttachment, InboundMessage, OutboundMessage
from asistente.application.local_mode import (
    LOCAL_HISTORY_MESSAGES,
    cap_tool_result,
    compact_specs,
    is_local_mode,
    local_user_text,
    trim_history,
)
from asistente.application.services import Services
from asistente.application.tool_router import (
    Announcement,
    compose_announcement,
    groups_for_tool,
    route_groups,
)
from asistente.config import Settings
from asistente.domain.images import ImageAnalysisService, friendly_image_error
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)

IMAGE_APPLY_TOOL = "apply_extracted_data"


class Orchestrator:
    def __init__(
        self,
        *,
        services: Services,
        llm: LLMClient,
        tools: ToolRegistry,
        vision: ImageUnderstandingProvider | None = None,
    ) -> None:
        self._services = services
        self._llm = llm
        self._tools = tools
        self._settings: Settings = services.settings
        self._vision: ImageUnderstandingProvider | None = vision
        self._images: ImageAnalysisService | None = None
        self._last_local_groups: dict[int, tuple[str, ...]] = {}
        self._last_local_user_text: dict[int, str] = {}

    def _image_service(self) -> ImageAnalysisService:
        if self._images is None:
            if self._vision is None:
                self._vision = OpenAIImageUnderstandingClient(self._settings)
            self._images = ImageAnalysisService(
                provider=self._vision,
                settings=self._settings,
                transactions=self._services.transactions,
            )
        return self._images

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    async def handle(self, inbound: InboundMessage) -> OutboundMessage:
        started = time.perf_counter()
        outcome = "ok"
        try:
            # Identidad primero: el rate limit va por USUARIO interno, nunca por
            # identificador de canal (una identidad WhatsApp pendiente no llega
            # aquí: la gestiona el adapter antes de orquestar).
            user = await self._services.users.ensure_user(
                channel=inbound.channel,
                external_user_id=str(inbound.user_id),
                name=inbound.display_name or inbound.metadata.get("name"),
                username=inbound.metadata.get("username"),
                timezone=self._settings.default_timezone,
                lang=self._settings.default_lang,
            )

            if not await self._rate_limited(user.id):
                return OutboundMessage(
                    text=("Has superado el límite de mensajes por minuto. Espera un momento y vuelve a intentarlo.")
                )

            pending = await self._services.confirmations.find_open(user.id)
            if pending is not None:
                handled = await self._maybe_resolve_confirmation(inbound.text, user_id=user.id, pending=pending)
                if handled is not None:
                    return handled

            if inbound.attachments:
                return await self._image_turn(
                    user_id=user.id,
                    caption=inbound.text,
                    attachments=inbound.attachments,
                )

            return await self._normal_turn(user_id=user.id, text=inbound.text)
        except LLMError as exc:
            outcome = "llm_error"
            logger.warning("Error del LLM: %s", exc)
            return OutboundMessage(
                text=("No he podido procesar tu mensaje en este momento. Intenta de nuevo en unos segundos.")
            )
        except Exception:  # noqa: BLE001
            outcome = "unexpected_error"
            logger.exception("Error inesperado en el orquestador")
            return OutboundMessage(text="Ha ocurrido un error interno. Inténtalo de nuevo pasados unos segundos.")
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "turn_done channel=%s outcome=%s duration_ms=%.1f",
                inbound.channel,
                outcome,
                duration_ms,
            )

    # ------------------------------------------------------------------ #
    # Rate limit                                                          #
    # ------------------------------------------------------------------ #
    async def _rate_limited(self, user_id: int) -> bool:
        if not self._settings.rate_limit_enabled:
            return True
        return self._services.rate_limit.is_allowed(user_id)

    # ------------------------------------------------------------------ #
    # Confirmaciones                                                      #
    # ------------------------------------------------------------------ #
    async def _maybe_resolve_confirmation(self, text: str, *, user_id: int, pending: dict) -> OutboundMessage | None:
        intent = classify_user_intent(text)
        if intent is None:
            return None  # no es 'sí' ni 'no' -> conversación normal

        if intent == "no":
            await self._services.confirmations.cancel(user_id, pending["id"])
            return OutboundMessage(text="Entendido, lo dejo cancelado.")

        # intent == "yes"
        confirmed = await self._services.confirmations.confirm(user_id, pending["id"])
        if confirmed is None:
            logger.warning("Confirmación %s no encontrada o ya resuelta", pending["id"])
            return OutboundMessage(text="Esa confirmación ya no está vigente.")

        tool_name = (pending["payload"] or {}).get("tool")
        arguments = (pending["payload"] or {}).get("arguments") or {}
        if not tool_name:
            logger.error("Confirmación %s sin tool en payload", pending["id"])
            return OutboundMessage(text="La operación confirmada no es válida.")

        result = await self._execute_confirmed_tool(user_id, tool_name, arguments)
        if not result.ok:
            return OutboundMessage(
                text=f"Lo siento, no he podido completar la acción: {result.error or 'error desconocido'}."
            )

        # Pedir al LLM que resuma el resultado de la acción confirmada.
        messages = await self._conversation_messages(user_id)
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "confirm_action",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": self._tool_result_content(result.data or {"ok": True}),
                    "tool_call_id": "confirm_action",
                },
            ]
        )
        response = self._llm.chat(
            messages=messages,
            tools=self._announced_tools(text=text, messages=messages, tool_hint=tool_name),
        )
        reply = await self._final_reply(messages=messages, response=response, tool_results=[result])
        await self._record_conversation(user_id=user_id, user_text=text, reply=reply, tool_results=[result])
        return OutboundMessage(text=reply)

    async def _execute_confirmed_tool(self, user_id: int, tool_name: str, arguments: dict):
        ctx = ToolContext(user_id=user_id, services=self._services)
        return await self._tools.execute(ctx, tool_name, arguments, confirmed=True)

    # ------------------------------------------------------------------ #
    # Imágenes (Fase 7)                                                   #
    # ------------------------------------------------------------------ #
    async def _image_turn(
        self,
        *,
        user_id: int,
        caption: str,
        attachments: list[ImageAttachment],
    ) -> OutboundMessage:
        """Imagen → extracción → validación → propuesta → confirmación.

        NUNCA imagen → LLM → escritura directa: el usuario confirma la
        propuesta y `apply_extracted_data` (con la confirmación ya creada)
        persiste reutilizando los servicios de dominio existentes.
        """
        user_tz = (await self._services.users.timezone_for(user_id)) or self._settings.default_timezone
        try:
            analysis = await self._image_service().analyze(
                user_id=user_id,
                user_tz=user_tz,
                attachments=attachments,
                caption=caption or None,
            )
        except ImageExtractionError as exc:
            return OutboundMessage(text=friendly_image_error(exc))

        await self._services.confirmations.create_pending(
            user_id=user_id,
            topic="extracción desde imagen",
            payload={"tool": IMAGE_APPLY_TOOL, "arguments": analysis.proposal},
            proposed_reply=analysis.reply,
        )
        await self._record_conversation(
            user_id=user_id,
            user_text=caption or "(imagen recibida)",
            reply=analysis.reply,
            tool_results=[],
        )
        return OutboundMessage(text=analysis.reply)

    # ------------------------------------------------------------------ #
    # Turno normal                                                        #
    # ------------------------------------------------------------------ #
    async def _normal_turn(self, *, user_id: int, text: str) -> OutboundMessage:
        messages = await self._conversation_messages(user_id)
        local = is_local_mode(self._settings)
        if local:
            now, tz = await self._user_clock(user_id)
            user_content = local_user_text(text, now=now, timezone=tz)
        else:
            user_content = text

        tool_results: list[Any] = []
        iterations = 0
        max_iter = self._settings.llm_max_tool_iterations
        pending_reply: str | None = None
        local_state = None
        if local:
            local_state = self._local_turn_state(
                user_id=user_id, text=text, messages=messages, user_content=user_content
            )
            announced = compact_specs(self._tools.specs_for(local_state.announcement.tool_names))
            messages = [messages[0], *local_state.history]
            self._last_local_groups[user_id] = local_state.announcement.groups
        else:
            announced = self._announced_tools()
        messages.append({"role": "user", "content": user_content})

        while iterations < max_iter:
            response = self._llm.chat(messages=messages, tools=announced)
            iterations += 1

            if not response.tool_calls:
                break

            for idx, tc in enumerate(response.tool_calls):
                tool = self._tools.get(tc.function_name)
                tool_call_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": tc.function_name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                    ],
                }
                messages.append(tool_call_msg)

                if tool is None:
                    messages.append(
                        {
                            "role": "tool",
                            "content": json.dumps(
                                {"error": f"Herramienta desconocida: {tc.function_name}"},
                                ensure_ascii=False,
                            ),
                            "tool_call_id": f"call_{idx}",
                        }
                    )
                    continue

                if tool.requires_confirmation:
                    # Tool que requiere confirmación: no se ejecuta aún.
                    pending_reply = await self._create_pending_confirmation(
                        user_id=user_id, tool=tool, arguments=tc.arguments
                    )
                    break

                result = await self._tools.execute(
                    ToolContext(user_id=user_id, services=self._services),
                    tc.function_name,
                    tc.arguments,
                )
                tool_results.append(result)
                if result.needs_confirmation:
                    pending_reply = await self._create_pending_confirmation(
                        user_id=user_id,
                        tool=tool,
                        arguments=tc.arguments,
                        confirmation=(result.data or {}).get("confirmation"),
                        proposed=result.proposed_reply,
                    )
                    break
                messages.append(
                    {
                        "role": "tool",
                        "content": self._tool_result_content(result.data if result.ok else {"error": result.error}),
                        "tool_call_id": f"call_{idx}",
                    }
                )

            if pending_reply is not None:
                break

        if iterations >= max_iter and not pending_reply:
            logger.warning(
                "Bucle de herramientas agotado (máx %s) para user=%s",
                max_iter,
                user_id,
            )
            reply = (
                "He tenido que detenerme porque la conversación encadenó demasiadas "
                "acciones. Vuelve a intentarlo por partes."
            )
            await self._record_conversation(user_id=user_id, user_text=text, reply=reply, tool_results=tool_results)
            return OutboundMessage(text=reply)

        if pending_reply is not None:
            await self._record_conversation(
                user_id=user_id, user_text=text, reply=pending_reply, tool_results=tool_results
            )
            return OutboundMessage(text=pending_reply)

        reply = await self._final_reply(messages=messages, response=response, tool_results=tool_results)
        await self._record_conversation(user_id=user_id, user_text=text, reply=reply, tool_results=tool_results)
        return OutboundMessage(text=reply)

    # ------------------------------------------------------------------ #
    # Respuesta final                                                      #
    # ------------------------------------------------------------------ #
    async def _final_reply(
        self,
        *,
        messages: list[dict[str, Any]],
        response: Any,
        tool_results: list[Any],
    ) -> str:
        """Devuelve la respuesta al usuario, nunca el payload crudo de una tool.

        Si el modelo devuelve el JSON de la herramienta (habitual con modelos
        pequeños) se le pide una versión en lenguaje natural sin anunciar
        herramientas; si tampoco lo consigue, se devuelve un resumen legible
        y determinista del resultado.
        """
        content = (getattr(response, "content", None) or "").strip()
        if not tool_results:
            return content or "Hecho."
        if content and not looks_like_raw_payload(content):
            return content

        logger.warning("Respuesta final con payload crudo de herramienta; se regenera")
        repaired = self._repair_reply(messages)
        if repaired:
            return repaired
        return build_fallback_reply(tool_results)

    def _repair_reply(self, messages: list[dict[str, Any]]) -> str | None:
        """Regenera la respuesta en lenguaje natural, sin herramientas."""
        prompt = [*messages, {"role": "user", "content": FINAL_ANSWER_INSTRUCTION}]
        try:
            response = self._llm.chat(messages=prompt, tools=[])
        except LLMError:
            logger.warning("No se pudo regenerar la respuesta final tras un payload crudo")
            return None
        content = (getattr(response, "content", None) or "").strip()
        if not content or looks_like_raw_payload(content):
            return None
        return content

    async def _create_pending_confirmation(
        self,
        *,
        user_id: int,
        tool: ToolDefinition,
        arguments: dict,
        confirmation: dict | None = None,
        proposed: str | None = None,
    ) -> str:
        target = confirmation or {"tool": tool.name, "arguments": arguments}
        reply = proposed or f"¿Confirmas que ejecute la acción '{tool.description or tool.name}'?"
        await self._services.confirmations.create_pending(
            user_id=user_id,
            topic=tool.description or tool.name,
            payload=target,
            proposed_reply=reply,
        )
        return reply

    # ------------------------------------------------------------------ #
    # Contexto e historial                                                #
    # ------------------------------------------------------------------ #
    async def _conversation_messages(self, user_id: int) -> list[dict[str, Any]]:
        local = is_local_mode(self._settings)
        window = self._settings.conversation_window_size
        if local:
            window = min(window, LOCAL_HISTORY_MESSAGES)
        recent = await self._services.conversations.recent(user_id, limit=window)
        memory = await self._services.memory.active_rules(user_id)
        pending = await self._services.confirmations.find_open(user_id)

        now, tz = await self._user_clock(user_id)
        if local:
            system = build_local_system_prompt(
                active_memory_summary=self._summarize_memory(memory),
                pending_summary=pending["topic"] if pending else None,
            )
        else:
            system = build_system_prompt(
                current_datetime=now,
                timezone=tz,
                active_memory_summary=self._summarize_memory(memory),
                pending_summary=pending["topic"] if pending else None,
            )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for row in recent:
            if row.role in ("user", "assistant"):
                messages.append({"role": row.role, "content": row.content})
        return messages

    async def _user_clock(self, user_id: int) -> tuple[datetime, str]:
        """Fecha/hora y zona horaria del usuario (para el prompt y el mensaje)."""
        from asistente.shared.timeutil import now_in_timezone

        user_tz = await self._services.users.timezone_for(user_id)
        tz = user_tz or self._settings.default_timezone
        return now_in_timezone(tz), tz

    def _announced_tools(
        self,
        *,
        text: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        tool_hint: str | None = None,
    ) -> list[dict[str, Any]]:
        """Tools que se anuncian al modelo.

        En modo local se anuncia el núcleo fijo + grupos bajo demanda (router
        determinista). El prefijo del núcleo se mantiene estable para reutilizar
        la cache de prefijo. En el proveedor oficial se anuncian todas.
        """
        if is_local_mode(self._settings):
            names = self._local_tool_names(text=text, messages=messages, tool_hint=tool_hint)
            return compact_specs(self._tools.specs_for(names))
        return self._tools.specs()

    def _local_tool_names(
        self,
        *,
        text: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        tool_hint: str | None = None,
    ) -> tuple[str, ...]:
        from asistente.tools.catalog import tool_names_for_groups

        groups: list[str] = []
        if text:
            # Sin user_id aquí: usar pegajoso global por último texto no es posible;
            # el router sin anterior solo devuelve grupos del texto actual.
            groups = list(route_groups(text, None))
        if tool_hint:
            for g in groups_for_tool(tool_hint):
                if g not in groups:
                    groups.append(g)
        return tool_names_for_groups(groups)

    class _LocalTurnState:
        __slots__ = ("announcement", "history")

        def __init__(self, announcement: Announcement, history: list[dict[str, Any]]) -> None:
            self.announcement = announcement
            self.history = history

    def _local_turn_state(
        self,
        *,
        user_id: int,
        text: str,
        messages: list[dict[str, Any]],
        user_content: str,
    ):

        routed = route_groups(text, self._last_local_user_text.get(user_id))
        system_chars = len(messages[0]["content"]) if messages else 0
        user_chars = len(user_content)

        def measure_tools_chars(names):
            specs = compact_specs(self._tools.specs_for(names))
            return sum(len(json.dumps(spec, ensure_ascii=False)) for spec in specs)

        announcement = compose_announcement(
            routed,
            system_chars=system_chars,
            user_chars=user_chars,
            measure_tools_chars=measure_tools_chars,
        )
        history = trim_history(
            messages[1:],
            system_chars=system_chars,
            tools_chars=announcement.tools_chars,
            budget_chars=announcement.history_budget_chars,
        )
        self._last_local_user_text[user_id] = text
        return self._LocalTurnState(announcement=announcement, history=history)

    def _tool_result_content(self, data: Any) -> str:
        """Contenido del mensaje de tool (recortado en modo local)."""
        if is_local_mode(self._settings):
            return cap_tool_result(data)
        return json.dumps(data, ensure_ascii=False)

    def _summarize_memory(self, rules: list) -> str | None:
        if not rules:
            return None
        lines = [f"- {r.kind}: {r.key} => {json.dumps(r.value, ensure_ascii=False)}" for r in rules]
        return "\n".join(lines)

    async def _record_conversation(
        self,
        *,
        user_id: int,
        user_text: str,
        reply: str,
        tool_results: list[Any],
    ) -> None:
        rows: list[tuple[str, str, dict]] = [("user", user_text, {})]
        for result in tool_results:
            rows.append(
                (
                    "tool",
                    json.dumps(result.data if result.ok else {"error": result.error}, ensure_ascii=False),
                    {},
                )
            )
        rows.append(("assistant", reply, {}))
        await self._services.conversations.add_many(user_id, rows)
