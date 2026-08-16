"""WalletUndoService — откат ручных операций кошелька (workflow.md 3.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.wallets.undo_service import WalletUndoService
from tests.db.conftest import balance_of, tx_rows

CHAT_ID = -100500
CHAT_NAME = "Тестовый чат"


@pytest.fixture
def service(repo) -> WalletUndoService:
    return WalletUndoService(repo=repo)


class TestUndoOperation:
    async def test_undo_of_plus_withdraws(self, service, repo, client_id) -> None:
        await repo.deposit(client_id=client_id, currency_code="USDT", amount=Decimal("100"))
        result = await service.undo_operation(
            chat_id=CHAT_ID,
            chat_name=CHAT_NAME,
            message_id=1,
            code_raw="USDT",
            sign="+",
            amt_str="100",
        )
        assert result.ok is True
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")

    async def test_undo_of_minus_deposits(self, service, repo, client_id) -> None:
        await repo.withdraw(client_id=client_id, currency_code="RUB", amount=Decimal("500"))
        result = await service.undo_operation(
            chat_id=CHAT_ID,
            chat_name=CHAT_NAME,
            message_id=2,
            code_raw="RUB",
            sign="-",
            amt_str="500",
        )
        assert result.ok is True
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")

    async def test_second_undo_same_message_is_rejected(self, service, repo, client_id) -> None:
        await repo.deposit(client_id=client_id, currency_code="USDT", amount=Decimal("100"))
        common = {
            "chat_id": CHAT_ID,
            "chat_name": CHAT_NAME,
            "message_id": 3,
            "code_raw": "USDT",
            "sign": "+",
            "amt_str": "100",
        }
        assert (await service.undo_operation(**common)).ok is True
        second = await service.undo_operation(**common)
        assert second.ok is False
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")

    async def test_db_idempotency_survives_service_restart(
        self, service, repo, pool, client_id
    ) -> None:
        await repo.deposit(client_id=client_id, currency_code="USDT", amount=Decimal("100"))
        common = {
            "chat_id": CHAT_ID,
            "chat_name": CHAT_NAME,
            "message_id": 4,
            "code_raw": "USDT",
            "sign": "+",
            "amt_str": "100",
        }
        await service.undo_operation(**common)
        restarted_service = WalletUndoService(repo=repo)
        n_before = len(await tx_rows(pool, client_id, "USDT"))
        result = await restarted_service.undo_operation(**common)
        assert result.ok is False
        assert "уже отменена" in result.message_text
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        assert len(await tx_rows(pool, client_id, "USDT")) == n_before

    async def test_bad_amount_rejected(self, service, client_id) -> None:
        result = await service.undo_operation(
            chat_id=CHAT_ID,
            chat_name=CHAT_NAME,
            message_id=5,
            code_raw="USDT",
            sign="+",
            amt_str="сто",
        )
        assert result.ok is False

    async def test_bad_sign_rejected(self, service, client_id) -> None:
        result = await service.undo_operation(
            chat_id=CHAT_ID,
            chat_name=CHAT_NAME,
            message_id=6,
            code_raw="USDT",
            sign="?",
            amt_str="100",
        )
        assert result.ok is False
