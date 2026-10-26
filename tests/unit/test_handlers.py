"""Tests unitarios: Telegram handlers (whitelist, /start y foto/Fase 7)."""

from __future__ import annotations

from dataclasses import dataclass, field

from asistente.adapters.telegram.handlers import photo_message_handler, start_command
from asistente.application.context import OutboundMessage
from asistente.config import Settings


@dataclass
class FakeUser:
    id: int
    first_name: str = ""
    username: str | None = None
    full_name: str | None = None


@dataclass
class FakeMessage:
    replies: list[str] = field(default_factory=list)
    message_id: int = 1

    async def reply_text(self, text: str, **_: object) -> None:
        self.replies.append(text)


@dataclass
class FakeUpdate:
    effective_user: FakeUser | None
    effective_message: FakeMessage | None


@dataclass
class FakeContext:
    bot_data: dict


def make_context(owner_ids: list[int]) -> FakeContext:
    settings = Settings(owner_telegram_ids=owner_ids)
    return FakeContext(bot_data={"settings": settings})


# --------------------------------------------------------------------------- #
# Fakes para el handler de fotos                                              #
# --------------------------------------------------------------------------- #
@dataclass
class FakePhoto:
    file_id: str
    file_size: int
    width: int = 10
    height: int = 20


@dataclass
class FakePhotoMessage(FakeMessage):
    photo: list = field(default_factory=list)
    caption: str | None = None


class FakeFile:
    data: bytes = b"\x00\x01"

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.data)


class FakeBot:
    async def get_file(self, file_id: str) -> FakeFile:
        return FakeFile()


@dataclass
class FakePhotoContext:
    bot_data: dict
    bot: FakeBot = FakeBot()

    def __getitem__(self, key: str) -> object:
        return self.bot_data[key]


class FakePhotoOrchestrator:
    def __init__(self) -> None:
        self.handled: list = []

    async def handle(self, inbound: object) -> OutboundMessage:
        self.handled.append(inbound)
        return OutboundMessage(text="propuesta lista")


def make_photo_context(owner_ids: list[int], orchestrator: FakePhotoOrchestrator) -> FakePhotoContext:
    settings = Settings(owner_telegram_ids=owner_ids)
    return FakePhotoContext(bot_data={"settings": settings, "orchestrator": orchestrator})


async def test_start_command_autorizado() -> None:
    msg = FakeMessage()
    update = FakeUpdate(effective_user=FakeUser(123), effective_message=msg)
    await start_command(update, make_context(owner_ids=[123]))  # type: ignore[arg-type]

    assert len(msg.replies) == 1
    assert "asistente personal" in msg.replies[0]


async def test_start_command_no_autorizado() -> None:
    msg = FakeMessage()
    update = FakeUpdate(effective_user=FakeUser(999), effective_message=msg)
    await start_command(update, make_context(owner_ids=[123]))  # type: ignore[arg-type]

    assert len(msg.replies) == 1
    assert "No estás autorizado" in msg.replies[0]


async def test_start_command_sin_usuario() -> None:
    msg = FakeMessage()
    update = FakeUpdate(effective_user=None, effective_message=msg)
    await start_command(update, make_context(owner_ids=[]))  # type: ignore[arg-type]

    assert len(msg.replies) == 1
    assert "identificar" in msg.replies[0]


async def test_whitelist_vacia_rechaza_a_todos() -> None:
    """Sin whitelist configurada nadie debe pasar."""
    msg = FakeMessage()
    update = FakeUpdate(effective_user=FakeUser(123), effective_message=msg)

    await start_command(update, make_context(owner_ids=[]))  # type: ignore[arg-type]
    assert "No estás autorizado" in msg.replies[0]


@dataclass
class _PhotoUpdate:
    effective_user: FakeUser | None
    effective_message: FakePhotoMessage | None


async def _photo_update(
    *, owner_ids: list[int], user_id: int, caption: str | None = None, photo: list | None = None
) -> tuple[FakePhotoContext, FakePhotoOrchestrator, FakePhotoMessage]:
    orchestrator = FakePhotoOrchestrator()
    ctx = make_photo_context(owner_ids=owner_ids, orchestrator=orchestrator)
    msg = FakePhotoMessage(photo=photo or [FakePhoto(file_id="f1", file_size=100)], caption=caption)
    update = _PhotoUpdate(FakeUser(user_id), msg)
    await photo_message_handler(update, ctx)  # type: ignore[arg-type]
    return ctx, orchestrator, msg


async def test_photo_handler_reenvia_imagen_autorizada_al_orquestador() -> None:
    _, orchestrator, msg = await _photo_update(owner_ids=[123], user_id=123, caption="ticket del súper")
    assert len(msg.replies) == 1
    assert msg.replies[0] == "propuesta lista"
    assert len(orchestrator.handled) == 1
    inbound = orchestrator.handled[0]
    assert inbound.text == "ticket del súper"
    assert len(inbound.attachments) == 1
    attachment = inbound.attachments[0]
    assert attachment.mime_type == "image/jpeg"
    assert attachment.data == b"\x00\x01"
    assert attachment.size == 2


async def test_photo_handler_elige_la_foto_de_mayor_resolucion() -> None:
    _, orchestrator, _ = await _photo_update(
        owner_ids=[123],
        user_id=123,
        photo=[
            FakePhoto(file_id="small", file_size=10, width=5, height=5),
            FakePhoto(file_id="big", file_size=500, width=50, height=50),
        ],
    )
    assert orchestrator.handled[0].attachments[0].file_id == "big"


async def test_photo_handler_no_autorizado_rechaza_sin_tocar_orquestador() -> None:
    _, orchestrator, msg = await _photo_update(owner_ids=[123], user_id=999)
    assert "No estás autorizado" in msg.replies[0]
    assert orchestrator.handled == []


async def test_photo_handler_sin_mensaje_no_crashea() -> None:
    orchestrator = FakePhotoOrchestrator()
    ctx = make_photo_context(owner_ids=[123], orchestrator=orchestrator)
    update = _PhotoUpdate(FakeUser(123), None)
    await photo_message_handler(update, ctx)  # type: ignore[arg-type]
    assert orchestrator.handled == []
