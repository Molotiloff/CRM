# app/db_asyncpg/utils.py
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, getcontext
from typing import Any

getcontext().prec = 50  # безопасная общая точность Decimal


class SqlParams:
    """Накопитель позиционных параметров для asyncpg с авто-нумерацией ``$N``.

    Вызов :meth:`add` регистрирует значение и возвращает его плейсхолдер
    (``$1``, ``$2``…), поэтому вызывающий код не считает индексы руками.
    Это убирает хрупкое ``"$%d" % (len(params) + 1)`` при динамической
    сборке WHERE/LIMIT.

    Пример::

        p = SqlParams()
        where = [f"account_id = {p.add(account_id)}"]
        if since is not None:
            where.append(f"txn_at >= {p.add(since)}")
        sql = f"SELECT ... WHERE {' AND '.join(where)} LIMIT {p.add(limit)}"
        rows = await con.fetch(sql, *p.values)
    """

    __slots__ = ("values",)

    def __init__(self) -> None:
        self.values: list[Any] = []

    def add(self, value: Any) -> str:
        """Зарегистрировать значение и вернуть его ``$N``-плейсхолдер."""
        self.values.append(value)
        return f"${len(self.values)}"


def to_upper(code: str) -> str:
    return (code or "").upper()


def quantize_amount(value: Decimal | str | int | float, precision: int) -> Decimal:
    d = Decimal(str(value))
    q = Decimal(10) ** (-precision)
    return d.quantize(q, rounding=ROUND_HALF_UP)
