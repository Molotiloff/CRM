from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from db_asyncpg.repositories.firm_positions import FirmPositionsRepo


@dataclass(slots=True, frozen=True)
class FirmPosition:
    """Позиция фирмы по валюте («Главная»: кол-во / в рубле / курс)."""

    currency_code: str
    qty: Decimal            # «кол-во» = Σ покупок − Σ продаж
    rub_cost: Decimal       # «в рубле» = рублёвая себестоимость остатка
    avg_rate: Decimal       # «курс» = rub_cost / qty — это «Вход» новой продажи


class FirmPositionService:
    """
    Средневзвешенная себестоимость валютной позиции (спека 4.2 workflow_crm.md).

    Покупка:  qty += кол-во, rub_cost += кол-во × курс.
    Продажа:  «Вход» = текущий средний курс (менеджер может переопределить),
              qty −= кол-во, rub_cost −= вход × кол-во.
    """

    def __init__(self, repo: FirmPositionsRepo) -> None:
        self.repo = repo

    @staticmethod
    def _to_position(row: dict | None, currency_code: str) -> FirmPosition:
        if row is None:
            return FirmPosition(
                currency_code=currency_code.strip().upper(),
                qty=Decimal(0),
                rub_cost=Decimal(0),
                avg_rate=Decimal(0),
            )
        qty = Decimal(str(row["qty_after"]))
        rub_cost = Decimal(str(row["rub_cost_after"]))
        avg = rub_cost / qty if qty else Decimal(0)
        return FirmPosition(
            currency_code=str(row["currency_code"]),
            qty=qty,
            rub_cost=rub_cost,
            avg_rate=avg,
        )

    async def position(self, currency_code: str) -> FirmPosition:
        row = await self.repo.get_position(currency_code)
        return self._to_position(row, currency_code)

    async def positions(self) -> list[FirmPosition]:
        rows = await self.repo.list_positions()
        return [self._to_position(r, str(r["currency_code"])) for r in rows]

    async def avg_rate(self, currency_code: str) -> Decimal:
        """Средний курс фирмы — автозаполнение поля «Вход» в карточке продажи."""
        return (await self.position(currency_code)).avg_rate

    async def apply_purchase(
        self,
        *,
        currency_code: str,
        qty: Decimal,
        rate: Decimal,
        deal_id: int | None = None,
        con=None,
    ) -> FirmPosition:
        """Лист «Покупка»: пополнение позиции, «В рубле» = сумма × курс."""
        rub = qty * rate
        row = await self._apply(
            con,
            currency_code=currency_code,
            kind="purchase",
            qty=qty,
            rub_amount=rub,
            deal_id=deal_id,
        )
        return self._to_position(row, currency_code)

    async def apply_sale(
        self,
        *,
        currency_code: str,
        qty: Decimal,
        entry_rate: Decimal | None = None,
        deal_id: int | None = None,
        con=None,
    ) -> tuple[Decimal, FirmPosition]:
        """
        Лист «Продажа»: списание позиции по «Входу».
        entry_rate=None → берём текущий средний курс фирмы.
        Возвращает (использованный вход, позиция после списания).
        """
        if entry_rate is None:
            entry_rate = await self.avg_rate(currency_code)
        rub = qty * entry_rate
        row = await self._apply(
            con,
            currency_code=currency_code,
            kind="sale",
            qty=-qty,
            rub_amount=-rub,
            deal_id=deal_id,
        )
        return entry_rate, self._to_position(row, currency_code)

    async def adjust(
        self,
        *,
        currency_code: str,
        qty_delta: Decimal,
        rub_delta: Decimal,
        deal_id: int | None = None,
        con=None,
    ) -> FirmPosition:
        """Ручная корректировка (грязные строки истории, сверки)."""
        row = await self._apply(
            con,
            currency_code=currency_code,
            kind="adjust",
            qty=qty_delta,
            rub_amount=rub_delta,
            deal_id=deal_id,
        )
        return self._to_position(row, currency_code)

    async def _apply(self, con, **kwargs) -> dict:
        if con is not None:
            return await self.repo.apply_move_in(con, **kwargs)
        return await self.repo.apply_move(**kwargs)
