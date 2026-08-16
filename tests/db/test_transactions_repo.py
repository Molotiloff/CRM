"""TransactionsRepo._apply_delta — атомарность, идемпотентность, balance_after.

workflow.md 3.2: это ядро всех денежных операций (deposit/withdraw идут через него).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from tests.db.conftest import balance_of, tx_rows


class TestDepositWithdraw:
    async def test_deposit_updates_balance_and_journal(self, repo, pool, client_id) -> None:
        tx_id = await repo.deposit(
            client_id=client_id,
            currency_code="usdt",
            amount=Decimal("100"),
            comment="тест",
            source="test",
        )
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        (row,) = await tx_rows(pool, client_id, "USDT")
        assert row["id"] == tx_id
        assert row["amount"] == Decimal("100.00")
        assert row["balance_after"] == Decimal("100.00")
        assert row["comment"] == "тест" and row["source"] == "test"

    async def test_withdraw_negates_amount(self, repo, pool, client_id) -> None:
        await repo.deposit(client_id=client_id, currency_code="RUB", amount=Decimal("500"))
        await repo.withdraw(client_id=client_id, currency_code="RUB", amount=Decimal("200"))
        assert await balance_of(repo, client_id, "RUB") == Decimal("300.00")
        rows = await tx_rows(pool, client_id, "RUB")
        assert [r["amount"] for r in rows] == [Decimal("500.00"), Decimal("-200.00")]

    async def test_balance_after_chain(self, repo, pool, client_id) -> None:
        for amount in ("10", "20", "30"):
            await repo.deposit(client_id=client_id, currency_code="USDT", amount=Decimal(amount))
        await repo.withdraw(client_id=client_id, currency_code="USDT", amount=Decimal("15"))
        rows = await tx_rows(pool, client_id, "USDT")
        assert [r["balance_after"] for r in rows] == [
            Decimal("10.00"),
            Decimal("30.00"),
            Decimal("60.00"),
            Decimal("45.00"),
        ]

    async def test_amount_quantized_to_account_precision(self, repo, pool, client_id) -> None:
        await repo.deposit(client_id=client_id, currency_code="RUB", amount=Decimal("0.005"))
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.01")  # HALF_UP
        await repo.deposit(client_id=client_id, currency_code="BTC", amount=Decimal("0.123456785"))
        assert await balance_of(repo, client_id, "BTC") == Decimal("0.12345679")

    async def test_negative_balance_allowed(self, repo, client_id) -> None:
        # Кошельки клиентов знаковые: уход в минус — валидное состояние (долг)
        await repo.withdraw(client_id=client_id, currency_code="RUB", amount=Decimal("100"))
        assert await balance_of(repo, client_id, "RUB") == Decimal("-100.00")

    async def test_missing_account_raises(self, repo, client_id) -> None:
        with pytest.raises(KeyError):
            await repo.deposit(client_id=client_id, currency_code="XYZ", amount=Decimal("1"))


class TestIdempotency:
    async def test_same_key_applies_once_and_returns_same_id(self, repo, pool, client_id) -> None:
        kwargs = {
            "client_id": client_id,
            "currency_code": "USDT",
            "amount": Decimal("100"),
            "idempotency_key": "op:1:1",
        }
        first = await repo.deposit(**kwargs)
        second = await repo.deposit(**kwargs)
        assert first == second
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert len(await tx_rows(pool, client_id, "USDT")) == 1

    async def test_different_keys_apply_separately(self, repo, client_id) -> None:
        for key in ("op:a", "op:b"):
            await repo.deposit(
                client_id=client_id,
                currency_code="USDT",
                amount=Decimal("50"),
                idempotency_key=key,
            )
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")

    async def test_concurrent_same_key_single_effect(self, repo, pool, client_id) -> None:
        """Гонка двух одинаковых операций (фикс 7-bis): эффект ровно один раз."""
        kwargs = {
            "client_id": client_id,
            "currency_code": "USDT",
            "amount": Decimal("100"),
            "idempotency_key": "op:race",
        }
        ids = await asyncio.gather(*(repo.deposit(**kwargs) for _ in range(5)))
        assert len(set(ids)) == 1
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert len(await tx_rows(pool, client_id, "USDT")) == 1

    async def test_no_key_means_no_dedup(self, repo, client_id) -> None:
        for _ in range(2):
            await repo.deposit(client_id=client_id, currency_code="USDT", amount=Decimal("10"))
        assert await balance_of(repo, client_id, "USDT") == Decimal("20.00")
