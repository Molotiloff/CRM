"""Exchange use-cases через execute_core — без Telegram (C0.3).

Проверяем связку целиком: деньги (balance_service) + персист линка
(exchange_request_links) + маршрутизация сообщений через MessengerPort.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import partial

import pytest

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.exchange_requests import ExchangeRequestsRepo
from db_asyncpg.repositories.tg_outbox import TgOutboxRepository
from db_asyncpg.uow import AsyncpgUnitOfWork
from services.crm import DealEventBus, DealListFilter, DealService, TelegramDealRegistrar
from services.crm.deal_service import DealStatusCommand
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.cancel_exchange_request import CancelExchangeParams, CancelExchangeRequest
from services.exchange.create_exchange_request import CreateExchangeParams, CreateExchangeRequest
from services.exchange.edit_exchange_request import EditExchangeParams, EditExchangeRequest
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.text_builder import ExchangeTextBuilder
from services.messaging import CollectingReplier
from tests.db.conftest import balance_of
from tests.fakes import FakeExchangeKeyboardPresenter, FakeMessenger

CLIENT_CHAT = -100500
REQUEST_CHAT = -777001


def _common_deps(repo, pool, exchange_requests_repo) -> dict:
    unit_of_work_factory = partial(AsyncpgUnitOfWork, pool)
    return {
        "repo": repo,
        "request_chat_id": REQUEST_CHAT,
        "balance_service": ExchangeBalanceService(unit_of_work_factory),
        "calculator": ExchangeCalculator(),
        "text_builder": ExchangeTextBuilder(),
        "source_links": ExchangeSourceLinkService(exchange_requests_repo),
        "act_counter_service": None,
        "unit_of_work_factory": unit_of_work_factory,
        "keyboards": FakeExchangeKeyboardPresenter(),
    }


def _deal_registrar(pool) -> TelegramDealRegistrar:
    return TelegramDealRegistrar(
        DealService(DealRepository(pool), DealEventBus()), default_city="екб"
    )


@pytest.fixture
def create_uc(pool, cash_request_repo, exchange_requests_repo) -> CreateExchangeRequest:
    return CreateExchangeRequest(**_common_deps(cash_request_repo, pool, exchange_requests_repo))


@pytest.fixture
def cancel_uc(pool, cash_request_repo, exchange_requests_repo) -> CancelExchangeRequest:
    return CancelExchangeRequest(**_common_deps(cash_request_repo, pool, exchange_requests_repo))


@pytest.fixture
def edit_uc(pool, cash_request_repo, exchange_requests_repo) -> EditExchangeRequest:
    return EditExchangeRequest(**_common_deps(cash_request_repo, pool, exchange_requests_repo))


def _create_params(**overrides) -> CreateExchangeParams:
    defaults = {
        "chat_id": CLIENT_CHAT,
        "chat_name": "Тестовый чат",
        "source_message_id": 11,
        "recv_code": "usdt",
        "recv_amount_expr": "100",
        "pay_code": "rub",
        "pay_amount_expr": "9000",
        "creator_name": "Менеджер",
    }
    defaults.update(overrides)
    return CreateExchangeParams(**defaults)


# Плоский текст карточки — как его отдаёт Telegram в message.text (без HTML)
PLAIN_CARD = "Заявка: 12345678\nПолучаем: 100 usdt\nКурс: 90\nОтдаём: 9 000 rub"


class TestCreateCore:
    async def test_happy_path(self, create_uc, repo, exchange_requests_repo, client_id) -> None:
        messenger = FakeMessenger()
        replier = CollectingReplier()
        result = await create_uc.execute_core(
            _create_params(), messenger=messenger, replier=replier
        )

        assert result.ok and result.req_id and result.table_req_id
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")

        # карточка клиенту + копия в чат заявок
        client_cards = messenger.sent_to(CLIENT_CHAT)
        request_cards = messenger.sent_to(REQUEST_CHAT)
        assert len(client_cards) == 1 and client_cards[0].reply_markup is not None
        assert len(request_cards) == 1
        assert "Получаем" in request_cards[0].text

        # линк заявки сохранён
        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=result.req_id)
        assert link is not None
        assert str(link["table_req_id"]) == str(result.table_req_id)
        assert Decimal(str(link["table_in_amount"])) == Decimal("100.00")
        assert Decimal(str(link["table_out_amount"])) == Decimal("9000.00")
        assert link["status"] == "active"

        # инициатору — сводка по кошельку
        assert any("Средств у" in r for r in replier.replies)

    async def test_happy_path_mirrors_exchange_to_crm_once(
        self, cash_request_repo, exchange_requests_repo, pool, client_id
    ) -> None:
        uc = CreateExchangeRequest(
            **_common_deps(cash_request_repo, pool, exchange_requests_repo),
            deal_registrar=_deal_registrar(pool),
        )
        params = _create_params(source_message_id=901)

        first = await uc.execute_core(
            params, messenger=FakeMessenger(), replier=CollectingReplier()
        )
        second = await uc.execute_core(
            params, messenger=FakeMessenger(), replier=CollectingReplier()
        )
        deals = await DealRepository(pool).list_deals(DealListFilter(client_id=client_id))

        assert first.ok and second.ok
        assert len(deals) == 1
        assert deals[0].source == "tg_bot"
        assert deals[0].source_kind == "exchange"
        assert deals[0].exchange_client_req_id == first.req_id

        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=first.req_id)
        assert link is not None
        assert deals[0].body.get("table_req_id") == int(link["table_req_id"])

        changed = await DealRepository(pool).change_status(
            deals[0].id,
            DealStatusCommand(status="fixed", actor_user_id=None, payload={"rate": "90"}),
        )
        assert changed is not None and changed[1] is True
        async with pool.acquire() as con:
            outbox = await con.fetchrow(
                "SELECT kind, payload FROM tg_outbox WHERE kind = 'deal_status_changed'"
            )
        assert outbox is not None
        payload = json.loads(outbox["payload"])
        assert payload["dealId"] == deals[0].id
        delivery_context = await TgOutboxRepository(pool).get_deal_delivery_context(
            payload["dealId"]
        )
        assert delivery_context is not None
        assert delivery_context["source_kind"] == "exchange"
        assert delivery_context["exchange_client_message_id"] == link["client_message_id"]
        assert delivery_context["exchange_request_message_id"] == link["request_message_id"]

    async def test_validation_error_no_side_effects(self, create_uc, repo, client_id) -> None:
        messenger = FakeMessenger()
        replier = CollectingReplier()
        result = await create_uc.execute_core(
            _create_params(recv_code="xyz"), messenger=messenger, replier=replier
        )
        assert not result.ok
        assert any("Счёт XYZ не найден" in r for r in replier.replies)
        assert messenger.sent == []
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")

    async def test_client_card_failure_still_posts_request_copy(
        self, create_uc, repo, exchange_requests_repo, client_id
    ) -> None:
        # Клиентский чат недоступен: деньги применены, копия в чат заявок ушла
        messenger = FakeMessenger(fail_chats={CLIENT_CHAT})
        replier = CollectingReplier()
        result = await create_uc.execute_core(
            _create_params(), messenger=messenger, replier=replier
        )
        assert result.ok
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert len(messenger.sent_to(REQUEST_CHAT)) == 1
        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=result.req_id)
        assert link is not None and link["request_chat_id"] == REQUEST_CHAT

    async def test_source_link_failure_rolls_back_money_before_telegram(
        self, create_uc, repo, client_id, monkeypatch
    ) -> None:
        async def fail_upsert(*args, **kwargs) -> None:
            raise RuntimeError("source link unavailable")

        monkeypatch.setattr(
            ExchangeRequestsRepo,
            "upsert_exchange_request_link",
            fail_upsert,
        )
        messenger = FakeMessenger()
        result = await create_uc.execute_core(
            _create_params(), messenger=messenger, replier=CollectingReplier()
        )

        assert result.ok is False
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")
        assert messenger.sent == []


class TestCancelCore:
    async def _create(self, create_uc) -> tuple[str, FakeMessenger]:
        messenger = FakeMessenger()
        result = await create_uc.execute_core(
            _create_params(), messenger=messenger, replier=CollectingReplier()
        )
        assert result.ok
        return result.req_id, messenger

    async def test_cancel_reverts_and_marks_link(
        self, create_uc, cancel_uc, repo, exchange_requests_repo, client_id
    ) -> None:
        req_id, create_messenger = await self._create(create_uc)
        card = create_messenger.sent_to(CLIENT_CHAT)[0]

        messenger = FakeMessenger()
        replier = CollectingReplier()
        result = await cancel_uc.execute_core(
            CancelExchangeParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                card_message_id=card.message_id,
                card_text=PLAIN_CARD.replace("12345678", req_id),
                req_id=req_id,
            ),
            messenger=messenger,
            replier=replier,
        )

        assert result.ok
        assert await balance_of(repo, client_id, "USDT") == Decimal("0.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("0.00")

        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=req_id)
        assert link is not None and link["status"] == "cancelled"

        # карточка клиента аннотирована, копия в чате заявок помечена отменённой
        client_edit = next(e for e in messenger.edits if e.chat_id == CLIENT_CHAT)
        assert "Отмена" in (client_edit.text or "")
        request_edit = next(e for e in messenger.edits if e.chat_id == REQUEST_CHAT)
        assert "Заявка отменена" in (request_edit.text or "")

        assert any("отменена" in r for r in replier.replies)
        assert "Заявка отменена" in replier.alerts

    async def test_unparsable_card_rejected(self, cancel_uc, repo, client_id) -> None:
        replier = CollectingReplier()
        result = await cancel_uc.execute_core(
            CancelExchangeParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                card_message_id=1,
                card_text="мусор",
                req_id="1",
            ),
            messenger=FakeMessenger(),
            replier=replier,
        )
        assert not result.ok
        assert "Не удалось распознать заявку" in replier.alerts

    async def test_status_failure_rolls_back_cancel_before_telegram(
        self, create_uc, cancel_uc, repo, exchange_requests_repo, client_id, monkeypatch
    ) -> None:
        req_id, create_messenger = await self._create(create_uc)
        card = create_messenger.sent_to(CLIENT_CHAT)[0]

        async def fail_status(*args, **kwargs) -> bool:
            raise RuntimeError("source status unavailable")

        monkeypatch.setattr(
            ExchangeRequestsRepo,
            "set_exchange_request_status",
            fail_status,
        )
        messenger = FakeMessenger()
        result = await cancel_uc.execute_core(
            CancelExchangeParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                card_message_id=card.message_id,
                card_text=PLAIN_CARD.replace("12345678", req_id),
                req_id=req_id,
            ),
            messenger=messenger,
            replier=CollectingReplier(),
        )

        assert result.ok is False
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=req_id)
        assert link is not None and link["status"] == "active"
        assert messenger.edits == []


class TestEditCore:
    async def test_edit_amounts_updates_money_cards_and_link(
        self, create_uc, edit_uc, repo, exchange_requests_repo, client_id
    ) -> None:
        create_messenger = FakeMessenger()
        created = await create_uc.execute_core(
            _create_params(), messenger=create_messenger, replier=CollectingReplier()
        )
        assert created.ok
        card = create_messenger.sent_to(CLIENT_CHAT)[0]

        messenger = FakeMessenger()
        replier = CollectingReplier()
        result = await edit_uc.execute_core(
            EditExchangeParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                edit_req_id=created.req_id,
                target_bot_msg_id=card.message_id,
                old_card_text=PLAIN_CARD.replace("12345678", created.req_id),
                cmd_msg_id=21,
                recv_code="USDT",
                pay_code="RUB",
                recv_amount=Decimal("150.00"),
                pay_amount=Decimal("13500.00"),
                recv_prec=2,
                pay_prec=2,
                rate_str="90",
                creator_name="Менеджер",
            ),
            messenger=messenger,
            replier=replier,
        )

        assert result.ok
        assert await balance_of(repo, client_id, "USDT") == Decimal("150.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-13500.00")

        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=created.req_id)
        assert link is not None
        assert Decimal(str(link["table_in_amount"])) == Decimal("150.00")
        assert Decimal(str(link["table_out_amount"])) == Decimal("13500.00")

        # правки: карточка клиента + копия в чате заявок; плюс уведомление «изменена»
        assert any(
            e.chat_id == CLIENT_CHAT and e.message_id == card.message_id for e in messenger.edits
        )
        assert any(e.chat_id == REQUEST_CHAT for e in messenger.edits)
        assert any("была изменена" in s.text for s in messenger.sent_to(REQUEST_CHAT))
        assert any("Средств у" in r for r in replier.replies)

    async def test_card_edit_failure_reports_error(
        self, create_uc, edit_uc, repo, client_id
    ) -> None:
        create_messenger = FakeMessenger()
        created = await create_uc.execute_core(
            _create_params(), messenger=create_messenger, replier=CollectingReplier()
        )
        card = create_messenger.sent_to(CLIENT_CHAT)[0]

        replier = CollectingReplier()
        result = await edit_uc.execute_core(
            EditExchangeParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                edit_req_id=created.req_id,
                target_bot_msg_id=card.message_id,
                old_card_text=PLAIN_CARD.replace("12345678", created.req_id),
                cmd_msg_id=22,
                recv_code="USDT",
                pay_code="RUB",
                recv_amount=Decimal("150.00"),
                pay_amount=Decimal("13500.00"),
                recv_prec=2,
                pay_prec=2,
                rate_str="90",
            ),
            messenger=FakeMessenger(fail_chats={CLIENT_CHAT}),
            replier=replier,
        )
        assert not result.ok
        assert any("Не удалось изменить заявку" in r for r in replier.replies)
        # деньги при этом уже пересчитаны (как и в текущем боте: дельта — до правки карточки)
        assert await balance_of(repo, client_id, "USDT") == Decimal("150.00")

    async def test_source_link_failure_rolls_back_edit_before_telegram(
        self, create_uc, edit_uc, repo, exchange_requests_repo, client_id, monkeypatch
    ) -> None:
        create_messenger = FakeMessenger()
        created = await create_uc.execute_core(
            _create_params(), messenger=create_messenger, replier=CollectingReplier()
        )
        card = create_messenger.sent_to(CLIENT_CHAT)[0]

        async def fail_upsert(*args, **kwargs) -> None:
            raise RuntimeError("source link unavailable")

        monkeypatch.setattr(
            ExchangeRequestsRepo,
            "upsert_exchange_request_link",
            fail_upsert,
        )
        messenger = FakeMessenger()
        result = await edit_uc.execute_core(
            EditExchangeParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                edit_req_id=created.req_id,
                target_bot_msg_id=card.message_id,
                old_card_text=PLAIN_CARD.replace("12345678", created.req_id),
                cmd_msg_id=23,
                recv_code="USDT",
                pay_code="RUB",
                recv_amount=Decimal("150.00"),
                pay_amount=Decimal("13500.00"),
                recv_prec=2,
                pay_prec=2,
                rate_str="90",
            ),
            messenger=messenger,
            replier=CollectingReplier(),
        )

        assert result.ok is False
        assert await balance_of(repo, client_id, "USDT") == Decimal("100.00")
        assert await balance_of(repo, client_id, "RUB") == Decimal("-9000.00")
        link = await exchange_requests_repo.get_exchange_request_link(client_req_id=created.req_id)
        assert link is not None
        assert Decimal(str(link["table_in_amount"])) == Decimal("100.00")
        assert messenger.edits == []
