"""Servicio de dominio de cuentas (Fase 3).

Reglas:
- Tipos exactos: bank | savings | cash | credit_card | other.
- Máximo UNA cuenta por defecto por usuario. La primera cuenta creada pasa a ser
  la default automáticamente.
- `initial_balance` fija el saldo inicial; el saldo actual es derivado (el
  servicio de cuentas NO lo calcula; lo hace `FinanceService`).
- Desactivar una cuenta quita su marca de default si la tenía (y no se
  promueve otra automáticamente: el usuario elige).
- Las cuentas con histórico NO se borran físicamente: `deactivate`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from asistente.domain.amounts import CENTS
from asistente.domain.currencies import normalize_currency
from asistente.persistence.models import Account
from asistente.persistence.repositories import AccountRepository, TransactionRepository, TransferRepository
from asistente.shared.errors import AuthorizationError, NotFoundError

ACCOUNT_TYPES = ("bank", "savings", "cash", "credit_card", "other")


def validate_initial_balance(account_type: str, balance: Decimal) -> None:
    """Regla de saldo inicial según tipo de cuenta (Fase 3.1.1).

    - banco/ahorro/efectivo/otras → `initial_balance >= 0` (saldo positivo).
    - tarjeta de crédito → `initial_balance <= 0` (saldo negativo = deuda).
    """
    if account_type == "credit_card":
        if balance > 0:
            raise ValueError("Una tarjeta de crédito representa deuda: el saldo inicial debe ser 0 o negativo.")
    elif balance < 0:
        raise ValueError(f"El saldo inicial no puede ser negativo para una cuenta de tipo '{account_type}'.")


class AccountService:
    def __init__(
        self,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        transfer_repo: TransferRepository,
    ) -> None:
        self._accounts = account_repo
        self._transactions = transaction_repo
        self._transfers = transfer_repo

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        type: str,
        currency: str = "EUR",
        initial_balance: Decimal = Decimal("0.00"),
        sort_order: int = 0,
        set_default: bool = False,
    ) -> Account:
        name = (name or "").strip()
        if not name:
            raise ValueError("El nombre de la cuenta no puede estar vacío.")
        if type not in ACCOUNT_TYPES:
            raise ValueError(f"Tipo de cuenta no válido: {type}. Válidos: {', '.join(ACCOUNT_TYPES)}.")
        currency = normalize_currency(currency)
        balance = (initial_balance or Decimal("0.00")).quantize(CENTS)
        validate_initial_balance(type, balance)
        accounts = await self._accounts.list_for_user(user_id, only_active=True)
        first_account = not accounts

        # La primera cuenta se marca default; si se pide default, se desactiva
        # cualquier otra default del usuario (invariante: máximo una).
        becomes_default = set_default or first_account
        if becomes_default:
            await self._accounts.clear_default(user_id)

        return await self._accounts.create(
            user_id=user_id,
            name=name,
            type=type,
            currency=currency,
            initial_balance=balance,
            is_default=becomes_default,
            active=True,
            sort_order=sort_order,
        )

    async def get(self, user_id: int, account_id: int) -> Account:
        account = await self._accounts.find_one(user_id, account_id)
        if account is None:
            raise NotFoundError("La cuenta no existe o no te pertenece.")
        return account

    async def list_for_user(self, user_id: int, *, only_active: bool = False) -> list[Account]:
        return await self._accounts.list_for_user(user_id, only_active=only_active)

    async def set_default(self, user_id: int, account_id: int) -> Account:
        account = await self.get(user_id, account_id)
        if not account.active:
            raise ValueError("No se puede marcar como principal una cuenta desactivada.")
        updated = await self._accounts.set_default(user_id, account_id)
        assert updated is not None
        return updated

    async def update(self, user_id: int, account_id: int, **fields) -> Account:
        account = await self.get(user_id, account_id)
        if "currency" in fields and fields["currency"] is not None:
            fields["currency"] = normalize_currency(fields["currency"])
        if "type" in fields and fields["type"] not in ACCOUNT_TYPES:
            raise ValueError(f"Tipo de cuenta no válido: {fields['type']}.")
        if "name" in fields:
            fields["name"] = (fields["name"] or "").strip()
            if not fields["name"]:
                raise ValueError("El nombre de la cuenta no puede estar vacío.")

        new_type = fields.get("type", account.type)
        if "initial_balance" in fields and fields["initial_balance"] is not None:
            # Protección Fase 3.1.1: no se puede reescribir el histórico.
            if await self._has_movements(user_id, account_id):
                raise ValueError(
                    "No se puede modificar el saldo inicial: la cuenta ya tiene movimientos o traspasos registrados. "
                    "Un ajuste de saldo requiere una operación contable específica."
                )
            balance = Decimal(str(fields["initial_balance"])).quantize(CENTS, rounding=ROUND_HALF_UP)
            fields["initial_balance"] = balance
            validate_initial_balance(new_type, balance)
        else:
            # Aunque no se cambie el saldo, el resultado final debe ser coherente
            # con el tipo resultante (p. ej. banco con saldo positivo → credit_card).
            validate_initial_balance(new_type, account.initial_balance)

        updated = await self._accounts.update(user_id, account_id, **fields)
        if updated is None:
            raise NotFoundError("La cuenta no existe o no te pertenece.")
        return updated

    async def deactivate(self, user_id: int, account_id: int) -> Account:
        account = await self.get(user_id, account_id)
        if not account.active:
            raise ValueError("La cuenta ya está desactivada.")
        if account.is_default:
            # Nunca promovemos otra automáticamente: el usuario elige.
            await self._accounts.clear_default(user_id)
        updated = await self._accounts.update(user_id, account_id, active=False, is_default=False)
        assert updated is not None
        return updated

    async def default_active(self, user_id: int) -> Account | None:
        """Cuenta por defecto activa (o la única activa, si es el caso)."""
        accounts = await self._accounts.list_for_user(user_id, only_active=True)
        default = next((a for a in accounts if a.is_default), None)
        if default is not None:
            return default
        if len(accounts) == 1:
            return accounts[0]
        return None

    async def require_active(self, user_id: int, account_id: int) -> Account:
        """Devuelve una cuenta activa del usuario o lanza un error claro."""
        account = await self.get(user_id, account_id)
        if not account.active:
            raise AuthorizationError("La cuenta está desactivada y no puede recibir nuevos movimientos.")
        return account

    async def _has_movements(self, user_id: int, account_id: int) -> bool:
        """¿La cuenta tiene histórico (movimientos o traspasos de entrada/salida)?"""
        if await self._transactions.list_for_user(user_id, account_id=account_id, limit=1):
            return True
        if await self._transfers.list_for_user(user_id, account_id=account_id, limit=1):
            return True
        return False
