"""Servicio de dominio de gastos compartidos (Fase 3.4).

Un gasto compartido NO es una deuda: es un gasto real que pagaron una o
varias personas y que se reparte entre participantes. Las posiciones
derivadas (quién debe a quién) se CALCULAN a partir de shares y pagos; este
servicio es la fuente de verdad y NO materializa deudas duplicadas en `debts`.

Reparto:
- Igualitario (por defecto): `split_equal` reparte el total EXACTO al céntimo
  usando números enteros de céntimos (100/3 → 33.34, 33.33, 33.33; la suma
  SIEMPRE es el total). Los céntimos sobrantes se reparten de uno en uno
  empezando por el primer participante.
- Manual: TODOS los participantes llevan su parte explícita (`share` como
  texto). No se puede mezclar (algunos con parte y otros sin ella). Todo
  imports > 0 y SUM == total, céntimo exacto.

El modo se INFIERE: si ningún participante trae `share` → reparto igualitario;
si todos lo traen → manual; si solo algunos → error.

Pagadores:
- `paid_by`: "user" | entero(persona) | nombre(resuelto). Un único pagador.
- `payments`: lista opcional {who, amount} para repartir el pago entre varios
  (quién aportó cuánto). SUM(payments) == total. Permite pagadores que NO son
  participantes (p. ej. el usuario paga la pizza entera de tres amigos).

Balance (por gasto, por participante):
    balance = share_amount − SUM(aportación como pagador)
    balance > 0 → debe dinero al grupo (debe pagarse).
    balance < 0 → le deben dinero (es acreedor del grupo).
    SUM(balances de participantes + pagadores no participantes) == 0.

Todas las cantidades son Decimal; todos los ids se validan contra el usuario.
"""

from __future__ import annotations

from decimal import Decimal

from asistente.domain.currencies import normalize_currency
from asistente.domain.people import PersonService
from asistente.persistence.models import SharedExpense
from asistente.persistence.repositories import (
    PersonRepository,
    SharedExpensePaymentRepository,
    SharedExpenseRepository,
    SharedExpenseShareRepository,
)
from asistente.shared.errors import NotFoundError
from asistente.shared.timeutil import utc_now


def _cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def split_equal(total: Decimal, participants: int) -> list[Decimal]:
    """Reparto igualitario EXACTO al céntimo: la suma SIEMPRE es `total`.

    Usa céntimos enteros; reparte el residuo de uno en uno al céntimo de los
    primeros participantes. Determinista y sin errores de redondeo float.
    """
    if not isinstance(participants, int) or participants <= 0:
        raise ValueError("El reparto requiere al menos un participante.")
    total_cents = _cents(total)
    base = total_cents // participants
    extra = total_cents % participants
    amounts = [Decimal(base) / Decimal(100)] * participants
    for i in range(extra):
        amounts[i] += Decimal("0.01")
    return amounts


class SharedExpenseService:
    def __init__(
        self,
        expense_repo: SharedExpenseRepository,
        share_repo: SharedExpenseShareRepository,
        payment_repo: SharedExpensePaymentRepository,
        person_repo: PersonRepository,
    ) -> None:
        self._expenses = expense_repo
        self._shares = share_repo
        self._payments = payment_repo
        self._people = PersonService(person_repo)

    # ------------------------------------------------------------------ #
    # Creación                                                            #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        user_id: int,
        title: str,
        total_amount: Decimal,
        participants: list[dict],
        paid_by,
        payments: list[dict] | None = None,
        occurred_at=None,
        note: str | None = None,
    ) -> dict:
        if total_amount <= 0:
            raise ValueError("El importe total del gasto compartido debe ser mayor que 0.")
        title = (title or "").strip()
        if not title:
            raise ValueError("El gasto compartido necesita un nombre ('Cena con Juan'.")
        currency = normalize_currency(None)

        resolved_participants = await self._resolve_participants(user_id, participants)
        payers = await self._resolve_payers(user_id, paid_by, payments, total_amount=total_amount)

        has_share = [bool(p.get("share")) for p in participants]
        if any(has_share) and not all(has_share):
            raise ValueError(
                "El reparto manual debe indicar la parte de TODOS los participantes "
                "(y sin compartir: o todos con parte o ninguno)."
            )
        if all(has_share):
            share_amounts = self._validate_manual_shares(
                total_amount, resolved_participants, [p["share"] for p in participants]
            )
        else:
            share_amounts = split_equal(total_amount, len(resolved_participants))

        async with self._expenses.transaction() as session:
            expense = await self._expenses.create(
                user_id=user_id,
                title=title[:300],
                total_amount=total_amount,
                currency=currency,
                occurred_at=occurred_at or utc_now(),
                note=(note or "").strip()[:2000] or None,
                session=session,
            )
            for participant, amount in zip(resolved_participants, share_amounts, strict=True):
                await self._shares.create(
                    shared_expense_id=expense.id,
                    person_id=participant["person_id"],
                    is_user=participant["is_user"],
                    share_amount=amount,
                    session=session,
                )
            for payer in payers:
                await self._payments.create(
                    shared_expense_id=expense.id,
                    person_id=payer["person_id"],
                    is_user=payer["is_user"],
                    amount=payer["amount"],
                    session=session,
                )

        return {
            "expense": expense,
            "participants": [
                {**p, "share_amount": amount} for p, amount in zip(resolved_participants, share_amounts, strict=True)
            ],
            "payers": payers,
        }

    async def _resolve_participants(self, user_id: int, participants: list[dict]) -> list[dict]:
        if not participants:
            raise ValueError("El gasto compartido necesita al menos un participante.")
        result: list[dict] = []
        seen_user = False
        seen_person_ids: set[int] = set()
        for item in participants:
            is_user = bool(item.get("is_user"))
            person_id = item.get("person_id")
            name = item.get("name") or item.get("person_name")
            if is_user:
                if seen_user:
                    raise ValueError("El usuario solo puede aparecer UNA vez como participante.")
                seen_user = True
                result.append({"is_user": True, "person_id": None, "name": "user"})
                continue
            if person_id is not None and not isinstance(person_id, bool):
                person = await self._people.resolve(user_id, int(person_id))
            elif name:
                person = await self._people.resolve(user_id, str(name))
            else:
                raise ValueError("Participante sin identificar: indica is_user, person_id o person_name.")
            if person.id in seen_person_ids:
                raise ValueError(f"La persona '{person.name}' aparece más de una vez como participante.")
            seen_person_ids.add(person.id)
            result.append({"is_user": False, "person_id": person.id, "name": person.name})
        return result

    async def _resolve_payers(self, user_id: int, paid_by, payments, *, total_amount: Decimal) -> list[dict]:
        """Devuelve la lista de pagadores {is_user, person_id, amount} que
        suman EXACTAMENTE `total_amount`."""
        key_amounts: dict[str, Decimal] = {}

        if payments:
            for p in payments:
                who = p.get("who")
                amount = Decimal(str(p.get("amount")))
                if amount <= 0:
                    raise ValueError("Las aportaciones deben ser mayores que 0.")
                key = await self._payer_key(user_id, who)
                key_amounts[key] = key_amounts.get(key, Decimal("0.00")) + amount
        else:
            if paid_by is None:
                raise ValueError("Indica quién pagó el gasto ('yo'/user, person_id o name).")
            key = await self._payer_key(user_id, paid_by)
            key_amounts[key] = total_amount

        if sum(key_amounts.values()) != total_amount:
            raise ValueError(
                "La suma de las aportaciones no coincide con el importe total "
                f"({sum(key_amounts.values())} ≠ {total_amount})."
            )

        # Convertir keys a la representación de persona
        payers: list[dict] = []
        for key, amount in key_amounts.items():
            if key == "user":
                payers.append({"is_user": True, "person_id": None, "amount": amount})
            else:
                person_id = int(key.removeprefix("person:"))
                payers.append({"is_user": False, "person_id": person_id, "amount": amount})
        return payers

    async def _payer_key(self, user_id: int, who) -> str:
        """Devuelve "user" o "person:<id>" para un identificador de pagador."""
        if isinstance(who, bool):
            raise ValueError("El identificador del pagador no es válido.")
        if isinstance(who, str):
            value = who.strip().lower()
            if value in {"user", "yo", "me"}:
                return "user"
            if value.isdigit():
                who = int(value)
            else:
                person = await self._people.resolve(user_id, who)
                return f"person:{person.id}"
        if isinstance(who, int):
            person = await self._people.resolve(user_id, who)
            return f"person:{person.id}"
        raise ValueError("Indica quién pagó con 'user', el ID de la persona o su nombre.")

    def _validate_manual_shares(
        self, total_amount: Decimal, participants: list[dict], shares: list[Decimal] | None
    ) -> list[Decimal]:
        if shares is None:
            raise ValueError("Con reparto manual hay que indicar la parte (share) de cada participante.")
        if len(shares) != len(participants):
            raise ValueError(
                f"El reparto manual debe indicar {len(participants)} partes "
                f"(una por participante) pero has dado {len(shares)}."
            )
        amounts = [Decimal(str(s)) for s in shares]
        if any(a <= 0 for a in amounts):
            raise ValueError("Las partes del reparto manual deben ser mayores que 0.")
        if sum(amounts) != total_amount:
            raise ValueError(f"Las partes del reparto manual suman {sum(amounts)} pero el gasto es {total_amount}.")
        return amounts

    # ------------------------------------------------------------------ #
    # Estado                                                              #
    # ------------------------------------------------------------------ #
    async def get_status(
        self,
        user_id: int,
        *,
        person_id: int | None = None,
        occurred_from=None,
        occurred_until=None,
    ) -> dict:
        """Estado de los gastos compartidos (activos).

        - `occurred_from`/`occurred_until` (UTC, opcionales): rango temporal.
        - `person_id` opcional: solo gastos donde esa persona participó o pagó.
        """
        expenses = await self._expenses.list_for_user(
            user_id,
            status="active",
            person_id=person_id,
            occurred_from=occurred_from,
            occurred_until=occurred_until,
            limit=200,
        )
        result = []
        for expense in expenses:
            shares = await self._shares.list_for_expense(expense.id)
            payments = await self._payments.list_for_expense(expense.id)
            result.append(await self._build_expense_status(user_id, expense, shares, payments))
        return {"expenses": result}

    async def _build_expense_status(self, user_id: int, expense, shares, payments) -> dict:
        paid = Decimal("0.00")
        for payment in payments:
            paid += Decimal(str(payment.amount))

        names: dict[int, str] = {}

        async def person_name(person_id: int) -> str:
            if person_id not in names:
                person = await self._people.resolve(user_id, person_id)
                names[person_id] = person.name
            return names[person_id]

        participants = []
        for share in shares:
            if share.is_user:
                name, is_user, pid = "Tú", True, None
            else:
                pid = share.person_id
                is_user = False
                name = await person_name(pid)
            contribution = Decimal("0.00")
            for payment in payments:
                if payment.is_user and share.is_user:
                    contribution += Decimal(str(payment.amount))
                elif payment.person_id is not None and payment.person_id == pid:
                    contribution += Decimal(str(payment.amount))
            share_amount = Decimal(str(share.share_amount))
            participants.append(
                {
                    "name": name,
                    "is_user": is_user,
                    "person_id": pid,
                    "share": share_amount,
                    "paid": contribution,
                    "balance": share_amount - contribution,
                }
            )

        # Pagadores que NO son participantes (frentes del gasto)
        external = []
        pids = [p["person_id"] for p in participants]
        for payment in payments:
            if payment.is_user:
                continue
            pid = payment.person_id
            if pid is not None and pid not in pids:
                external.append(
                    {
                        "name": await person_name(pid),
                        "person_id": pid,
                        "is_user": False,
                        "amount": Decimal(str(payment.amount)),
                    }
                )

        user_rows = [p for p in participants if p["is_user"]]
        if user_rows:
            # El usuario es participante: su posición ya captura TODAS sus
            # aportaciones (paid). Usar su balance evita contar dos veces.
            user_balance = user_rows[0]["balance"]
        else:
            # El usuario no participa pero pagó: cada euro puesto es dinero que
            # los participantes le deben.
            user_external_paid = sum((Decimal(str(p.amount)) for p in payments if p.is_user), Decimal("0.00"))
            user_balance = Decimal("0.00") - user_external_paid

        return {
            "expense_id": expense.id,
            "title": expense.title,
            "total": Decimal(str(expense.total_amount)),
            "currency": expense.currency,
            "occurred_at": expense.occurred_at,
            "note": expense.note,
            "status": expense.status,
            "participants": participants,
            "external_payers": external,
            "user_balance": user_balance,
        }

    async def cancel(self, user_id: int, expense_id: int) -> SharedExpense:
        expense = await self._expenses.find_one(user_id, expense_id)
        if expense is None:
            raise NotFoundError("El gasto compartido no existe o no te pertenece.")
        if expense.status != "active":
            raise ValueError("El gasto compartido ya está cancelado.")
        updated = await self._expenses.settle_cancel(user_id, expense_id)
        assert updated is not None
        return updated
