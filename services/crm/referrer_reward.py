from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def calculate_referrer_reward(deal_type: str, body: Mapping[str, Any]) -> Decimal | None:
    """RUB per unit of the foreign currency, rounded to kopeks."""
    if deal_type not in {"sale", "purchase"} or not body.get("referrer_client_id"):
        return None
    try:
        spread = Decimal(str(body["referrer_spread_rub"]))
        quantity = Decimal(str(body["pay_amount" if deal_type == "sale" else "recv_amount"]))
        if not spread.is_finite() or not quantity.is_finite() or spread < 0 or quantity < 0:
            return None
        return (spread * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (KeyError, InvalidOperation, ValueError):
        return None
