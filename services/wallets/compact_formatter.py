from __future__ import annotations

from decimal import Decimal

from services.number_formatting import format_amount_core


def label_for(code: str) -> str:
    code = code.upper().strip()
    if code == "RUB":
        return "руб"
    if code == "UAH":
        return "грн"
    return code.lower()


def format_wallet_compact(rows: list[dict], *, only_nonzero: bool) -> str:
    """Строка для <code>…</code>:
    «  <amount right-aligned> <label>», без кода валюты слева.
    """
    items: list[tuple[str, str]] = []
    for row in rows:
        bal = Decimal(str(row["balance"]))
        if only_nonzero and bal == 0:
            continue
        prec = int(row["precision"])
        amount_str = format_amount_core(bal, prec)
        label = label_for(str(row["currency_code"]))
        items.append((amount_str, label))

    if not items:
        return "Пусто"

    width = max(len(a) for a, _ in items)
    lines = [f"  {amount.rjust(width)} {label}" for amount, label in items]
    return "\n".join(lines)
