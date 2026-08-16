"""ExchangeBalanceService — apply_create / apply_cancel / apply_edit_delta.

workflow.md 3.2. Дефолтные флаги заявки: получаем = deposit клиенту,
отдаём = withdraw у клиента.
"""

from __future__ import annotations

from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.uow import AsyncpgUnitOfWork
from services.exchange.balance_service import ExchangeBalanceService
from tests.db.conftest import balance_of, tx_rows

OLD_CARD = "Получаем: <code>100 USDT</code>\nОтдаём: <code>9 000 RUB</code>"


@pytest.fixture
def service(pool) -> ExchangeBalanceService:
    return ExchangeBalanceService(partial(AsyncpgUnitOfWork, pool))


def _create_kwargs(client_id: int, **overrides) -> dict:
    kwargs = {
        "client_id": client_id,
        "recv_code": "USDT",
        "recv_amount": Decimal("100"),
        "recv_comment": "100",
        "pay_code": "RUB",
        "pay_amount": Decimal("9000"),
        "pay_comment": "9000",
        "recv_is_deposit": True,
        "pay_is_withdraw": True,
        "idem_recv": "c:1:recv",
        "idem_pay": "c:1:pay",
    }
    kwargs.update(overrides)
    return kwargs


class TestApplyCreate:
    async def test_applies_both_legs(self, service, repo, client_id) -> None:
        result = await service.apply_create(**_create_kwargs(client_id))
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
        directions = [(m.currency_code, m.direction, m.amount) for m in result.movements]
        assert directions == [
            ("USDT", "IN", Decimal("100")),
            ("RUB", "OUT", Decimal("9000")),
        ]

    async def test_inverted_flags(self, service, repo, client_id) -> None:
        await service.apply_create(
            **_create_kwargs(client_id, recv_is_deposit=False, pay_is_withdraw=False)
        )
        assert await balance_of(repo, client_id, "USDT") == Decimal("-100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("9000.00")

    async def test_tracked_filter_skips_untracked_leg(self, service, repo, pool, client_id) -> None:
        result = await service.apply_create(
            **_create_kwargs(client_id, tracked_currency_codes={"USDT"})
        )
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")
        assert await tx_rows(pool, client_id, "RUB") == []
        assert [m.currency_code for m in result.movements] == ["USDT"]

    async def test_second_leg_failure_rolls_back_first(
        self, service, repo, pool, client_id
    ) -> None:
        # Счёта EUR у клиента нет: вся exchange-транзакция откатывается.
        with pytest.raises(KeyError):
            await service.apply_create(**_create_kwargs(client_id, pay_code="EUR"))
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        rows = await tx_rows(pool, client_id, "USDT")
        assert rows == []


class TestApplyCancel:
    async def test_reverts_create(self, service, repo, client_id) -> None:
        await service.apply_create(**_create_kwargs(client_id))
        recv_sign, pay_sign = await service.apply_cancel(
            client_id=client_id,
            chat_id=-100500,
            message_id=42,
            req_id="12345678",
            recv_code="USDT",
            recv_amount=Decimal("100"),
            pay_code="RUB",
            pay_amount=Decimal("9000"),
            recv_is_deposit=True,
            pay_is_withdraw=True,
        )
        assert (recv_sign, pay_sign) == ("-", "+")
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")

    async def test_double_cancel_is_idempotent(self, service, repo, pool, client_id) -> None:
        await service.apply_create(**_create_kwargs(client_id))
        cancel_kwargs = {
            "client_id": client_id,
            "chat_id": -100500,
            "message_id": 42,
            "req_id": "12345678",
            "recv_code": "USDT",
            "recv_amount": Decimal("100"),
            "pay_code": "RUB",
            "pay_amount": Decimal("9000"),
            "recv_is_deposit": True,
            "pay_is_withdraw": True,
        }
        await service.apply_cancel(**cancel_kwargs)
        n_before = len(await tx_rows(pool, client_id))
        signs = await service.apply_cancel(**cancel_kwargs)  # повторный клик «Отмена»
        assert signs == ("-", "+")
        assert len(await tx_rows(pool, client_id)) == n_before
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")

    async def test_tracked_filter(self, service, repo, client_id) -> None:
        await service.apply_create(**_create_kwargs(client_id, tracked_currency_codes={"USDT"}))
        recv_sign, pay_sign = await service.apply_cancel(
            client_id=client_id,
            chat_id=-100500,
            message_id=43,
            req_id="1",
            recv_code="USDT",
            recv_amount=Decimal("100"),
            pay_code="RUB",
            pay_amount=Decimal("9000"),
            recv_is_deposit=True,
            pay_is_withdraw=True,
            tracked_currency_codes={"USDT"},
        )
        assert (recv_sign, pay_sign) == ("-", None)
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")

    async def test_second_leg_failure_rolls_back_cancel(
        self, service, repo, pool, client_id
    ) -> None:
        await service.apply_create(**_create_kwargs(client_id))
        rows_before = await tx_rows(pool, client_id)

        with pytest.raises(KeyError):
            await service.apply_cancel(
                client_id=client_id,
                chat_id=-100500,
                message_id=44,
                req_id="2",
                recv_code="USDT",
                recv_amount=Decimal("100"),
                pay_code="EUR",
                pay_amount=Decimal("9000"),
                recv_is_deposit=True,
                pay_is_withdraw=True,
            )

        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
        assert await tx_rows(pool, client_id) == rows_before


class TestApplyEditDelta:
    def _edit_kwargs(self, client_id: int, **overrides) -> dict:
        kwargs = {
            "client_id": client_id,
            "old_request_text": OLD_CARD,
            "recv_code_new": "USDT",
            "pay_code_new": "RUB",
            "recv_amount_new": Decimal("150"),
            "pay_amount_new": Decimal("13500"),
            "recv_prec": 2,
            "pay_prec": 2,
            "chat_id": -100500,
            "target_bot_msg_id": 42,
            "cmd_msg_id": 43,
            "recv_is_deposit": True,
            "pay_is_withdraw": True,
        }
        kwargs.update(overrides)
        return kwargs

    async def _seed_original(self, service, client_id: int) -> None:
        await service.apply_create(**_create_kwargs(client_id))

    async def test_amount_increase_applies_deltas(self, service, repo, client_id) -> None:
        await self._seed_original(service, client_id)
        movements = await service.apply_edit_delta(**self._edit_kwargs(client_id))
        assert await balance_of(repo, client_id, "USDT") == Decimal("150.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-13500.00")
        assert [(m.currency_code, m.direction, m.amount) for m in movements] == [
            ("USDT", "IN", Decimal("50")),
            ("RUB", "OUT", Decimal("4500")),
        ]

    async def test_amount_decrease_applies_negative_deltas(self, service, repo, client_id) -> None:
        await self._seed_original(service, client_id)
        movements = await service.apply_edit_delta(
            **self._edit_kwargs(
                client_id, recv_amount_new=Decimal("80"), pay_amount_new=Decimal("7200")
            )
        )
        assert await balance_of(repo, client_id, "USDT") == Decimal("80.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-7200.00")
        assert [(m.currency_code, m.direction) for m in movements] == [
            ("USDT", "OUT"),
            ("RUB", "IN"),
        ]

    async def test_currency_change_reverts_old_and_applies_new(
        self, service, repo, client_id
    ) -> None:
        await self._seed_original(service, client_id)
        # USDT→BTC при том же RUB: старая recv-нога откатывается, новая применяется
        movements = await service.apply_edit_delta(
            **self._edit_kwargs(
                client_id,
                recv_code_new="BTC",
                recv_amount_new=Decimal("0.5"),
                recv_prec=8,
                pay_amount_new=Decimal("9000"),
            )
        )
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        assert await balance_of(repo, client_id, "BTC") == Decimal("0.50000000")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
        kinds = [(m.currency_code, m.direction) for m in movements]
        assert ("USDT", "OUT") in kinds and ("BTC", "IN") in kinds

    async def test_unparsable_old_text_is_noop(self, service, repo, pool, client_id) -> None:
        await self._seed_original(service, client_id)
        n_before = len(await tx_rows(pool, client_id))
        movements = await service.apply_edit_delta(
            **self._edit_kwargs(client_id, old_request_text="мусор без карточки")
        )
        assert movements == []
        assert len(await tx_rows(pool, client_id)) == n_before

    async def test_edit_is_idempotent_per_command(self, service, repo, client_id) -> None:
        await self._seed_original(service, client_id)
        kwargs = self._edit_kwargs(client_id)
        await service.apply_edit_delta(**kwargs)
        # Повтор той же команды (тот же cmd_msg_id) — дельты не задваиваются
        await service.apply_edit_delta(**kwargs)
        assert await balance_of(repo, client_id, "USDT") == Decimal("150.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-13500.00")

    async def test_late_currency_failure_rolls_back_all_edit_movements(
        self, service, repo, pool, client_id
    ) -> None:
        await self._seed_original(service, client_id)
        rows_before = await tx_rows(pool, client_id)

        with pytest.raises(KeyError):
            await service.apply_edit_delta(
                **self._edit_kwargs(
                    client_id,
                    recv_code_new="BTC",
                    recv_amount_new=Decimal("0.5"),
                    recv_prec=8,
                    pay_code_new="EUR",
                    pay_amount_new=Decimal("9000"),
                )
            )

        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "BTC") == Decimal("0.00000000")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
        assert await tx_rows(pool, client_id) == rows_before
