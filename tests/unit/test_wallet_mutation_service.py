from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from services.wallets.mutation_service import CurrencyMutationService


async def test_withdraw_all_uses_current_positive_wallet_balance() -> None:
    repo = MagicMock()
    repo.ensure_client = AsyncMock(return_value=1)
    repo.snapshot_wallet = AsyncMock(
        side_effect=[
            [{"currency_code": "USDT", "precision": 6, "balance": Decimal("125.5")}],
            [{"currency_code": "USDT", "precision": 6, "balance": Decimal("0")}],
            [{"currency_code": "USDT", "precision": 6, "balance": Decimal("0")}],
        ]
    )
    repo.withdraw = AsyncMock()
    service = CurrencyMutationService(repo=repo)

    result = await service.withdraw_all(
        chat_id=-200,
        chat_name="Поэты",
        code="USDT",
        comment="/баотпр",
        source="partner_chat_command",
        idempotency_key="-200:12",
    )

    assert result.ok is True
    repo.withdraw.assert_awaited_once_with(
        client_id=1,
        currency_code="USDT",
        amount=Decimal("125.500000"),
        comment="/баотпр",
        source="partner_chat_command",
        idempotency_key="-200:12",
    )
