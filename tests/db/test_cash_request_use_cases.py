"""Cash request use-cases через execute_core — без Telegram (C0.3)."""

from __future__ import annotations

from db_asyncpg.repositories.deals import DealRepository
from services.cash_requests import (
    CreateCashRequest,
    CreateCashRequestParams,
    EditCashRequest,
    EditCashRequestParams,
    RequestDealCancelParams,
    RequestDealCancelService,
    RequestDealDoneParams,
    RequestDealDoneService,
    RequestIssueParams,
    RequestIssueService,
    RequestRouterService,
    RequestScheduleService,
    RequestTimeParams,
    RequestTimeService,
)
from services.cash_requests.parsing import ParsedRequest
from services.crm import DealEventBus, DealListFilter, DealService, TelegramDealRegistrar
from services.messaging import CollectingReplier
from tests.db.conftest import balance_of
from tests.fakes import FakeMessenger

CLIENT_CHAT = -100500
REQUEST_CHAT = -777101


def _create_uc(cash_repo, schedule_repo, *, pool=None, with_crm: bool = False) -> CreateCashRequest:
    router, schedule = _router_and_schedule(schedule_repo)
    return CreateCashRequest(
        repo=cash_repo,
        router_service=router,
        schedule_service=schedule,
        deal_registrar=(
            TelegramDealRegistrar(
                DealService(DealRepository(pool), DealEventBus()), default_city="екб"
            )
            if with_crm
            else None
        ),
    )


def _router_and_schedule(schedule_repo) -> tuple[RequestRouterService, RequestScheduleService]:
    router = RequestRouterService(
        request_chat_id=REQUEST_CHAT,
        city_cash_chats={"екб": REQUEST_CHAT},
        city_schedule_chats={"екб": -777102},
        default_city="екб",
    )
    schedule = RequestScheduleService(repo=schedule_repo, router_service=router)
    return router, schedule


def _dep_params(**overrides) -> CreateCashRequestParams:
    parsed_defaults = {
        "cmd": "депр",
        "kind": "dep",
        "city": "екб",
        "amount_expr": "1000",
        "code": "RUB",
        "contact1": "@cashier",
        "contact2": "@client",
        "comment": "test",
    }
    parsed_defaults.update(overrides.pop("parsed", {}))
    defaults = {
        "chat_id": CLIENT_CHAT,
        "chat_name": "Тестовый чат",
        "parsed": ParsedRequest(**parsed_defaults),
        "creator_name": "Менеджер",
    }
    defaults.update(overrides)
    return CreateCashRequestParams(**defaults)


class TestCreateCashCore:
    async def test_dep_posts_client_and_request_cards_and_schedule(
        self, cash_request_repo, request_schedule_repo, client_id
    ) -> None:
        uc = _create_uc(cash_request_repo, request_schedule_repo)
        messenger = FakeMessenger()
        replier = CollectingReplier()
        synced_cities: list[str] = []

        async def sync_schedule_board(city: str) -> None:
            synced_cities.append(city)

        result = await uc.execute_core(
            _dep_params(),
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule_board,
        )

        assert result.ok and result.req_id
        assert len(replier.replies) == 1
        assert result.req_id in replier.replies[0]
        assert "1’000.00" in replier.replies[0]

        request_cards = messenger.sent_to(REQUEST_CHAT)
        assert len(request_cards) == 1
        assert result.req_id in request_cards[0].text
        assert "Создал" in request_cards[0].text

        entry = await request_schedule_repo.get_request_schedule_entry_by_req_id(
            req_id=result.req_id
        )
        assert entry is not None
        assert entry["city"] == "екб"
        assert entry["request_kind"] == "dep"
        assert entry["request_chat_id"] == REQUEST_CHAT
        assert entry["request_message_id"] == request_cards[0].message_id
        assert synced_cities == ["екб"]

    async def test_dep_mirrors_cash_request_to_crm_once(
        self, pool, cash_request_repo, request_schedule_repo, client_id
    ) -> None:
        uc = _create_uc(
            cash_request_repo,
            request_schedule_repo,
            pool=pool,
            with_crm=True,
        )
        params = _dep_params(source_message_id=902)

        first = await uc.execute_core(
            params, messenger=FakeMessenger(), replier=CollectingReplier()
        )
        second = await uc.execute_core(
            params, messenger=FakeMessenger(), replier=CollectingReplier()
        )
        deals = await DealRepository(pool).list_deals(DealListFilter(client_id=client_id))

        assert first.ok and second.ok
        assert len(deals) == 1
        assert deals[0].deal_type == "deposit"
        assert deals[0].source_kind == "cash"
        assert deals[0].body.get("amount") == "1000"

    async def test_missing_account_is_reported_without_messages(
        self, cash_request_repo, request_schedule_repo, client_id
    ) -> None:
        uc = _create_uc(cash_request_repo, request_schedule_repo)
        messenger = FakeMessenger()
        replier = CollectingReplier()

        result = await uc.execute_core(
            _dep_params(parsed={"code": "EUR", "cmd": "депе"}),
            messenger=messenger,
            replier=replier,
        )

        assert not result.ok
        assert messenger.sent == []
        assert any("Счёт EUR не найден" in reply for reply in replier.replies)


class TestEditCashCore:
    async def test_dep_edits_client_and_request_cards(
        self, cash_request_repo, request_schedule_repo, client_id
    ) -> None:
        router, schedule = _router_and_schedule(request_schedule_repo)
        service = EditCashRequest(
            repo=cash_request_repo,
            router_service=router,
            schedule_service=schedule,
        )
        await request_schedule_repo.upsert_request_schedule_entry(
            req_id="Б-123456",
            city="екб",
            hhmm="10:00",
            request_kind="dep",
            line_text="+1000 RUB — Тестовый чат",
            client_name="Тестовый чат",
            request_chat_id=REQUEST_CHAT,
            request_message_id=700,
        )
        messenger = FakeMessenger()
        replier = CollectingReplier()
        synced_cities: list[str] = []
        old_text = (
            "Заявка на внесение: Б-123456\nГород: екб\nСумма: 1000 RUB\nКод: 111-222\nВремя: 10:00"
        )

        async def sync_schedule_board(city: str) -> None:
            synced_cities.append(city)

        result = await service.execute_core(
            EditCashRequestParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                parsed=ParsedRequest(
                    cmd="депр",
                    kind="dep",
                    city="екб",
                    amount_expr="2000",
                    code="RUB",
                    contact1="@cashier",
                    contact2="@client",
                    comment="changed",
                ),
                old_text=old_text,
                reply_msg_id=701,
                editor_name="Менеджер",
            ),
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule_board,
        )

        assert result.ok and result.req_id == "Б-123456"
        assert any(
            edit.chat_id == CLIENT_CHAT and edit.message_id == 701 for edit in messenger.edits
        )
        assert any(
            edit.chat_id == REQUEST_CHAT and edit.message_id == 700 for edit in messenger.edits
        )
        entry = await request_schedule_repo.get_request_schedule_entry_by_req_id(req_id="Б-123456")
        assert entry is not None
        assert entry["hhmm"] == "10:00"
        assert "2000" in str(entry["line_text"]).replace("’", "").replace(".00", "")
        assert synced_cities == ["екб"]
        assert "✅ Заявка обновлена." in replier.replies


class TestIssueCashCore:
    async def test_dep_applies_balance_and_strips_keyboard(self, repo, client_id) -> None:
        service = RequestIssueService(
            repo=repo,
            admin_chat_ids=set(),
            admin_user_ids=set(),
        )
        messenger = FakeMessenger()
        replier = CollectingReplier()

        result = await service.execute_core(
            RequestIssueParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                message_id=555,
                card_text="Заявка на внесение\nСумма: 1000 RUB",
                callback_data="req:issue_done:dep:Б-001",
            ),
            messenger=messenger,
            replier=replier,
        )

        assert result.ok and result.op_kind == "dep"
        assert await balance_of(repo, client_id, "RUB") == 1000
        assert any(
            edit.chat_id == CLIENT_CHAT and edit.message_id == 555 for edit in messenger.edits
        )
        assert any("Баланс" in reply for reply in replier.replies)
        assert "Отмечено как выдано" in replier.alerts

    async def test_missing_account_is_reported_without_balance_change(
        self, repo, client_id
    ) -> None:
        service = RequestIssueService(
            repo=repo,
            admin_chat_ids=set(),
            admin_user_ids=set(),
        )
        messenger = FakeMessenger()
        replier = CollectingReplier()

        result = await service.execute_core(
            RequestIssueParams(
                chat_id=CLIENT_CHAT,
                chat_name="Тестовый чат",
                message_id=556,
                card_text="Заявка на внесение\nСумма: 25 EUR",
                callback_data="req:issue_done:dep:Б-002",
            ),
            messenger=messenger,
            replier=replier,
        )

        assert not result.ok
        assert await balance_of(repo, client_id, "RUB") == 0
        assert messenger.edits == []
        assert any("Счёт EUR не найден" in reply for reply in replier.replies)


class TestDealStatusCore:
    async def test_done_removes_schedule_and_edits_card(
        self, managers_repo, request_schedule_repo
    ) -> None:
        router, schedule = _router_and_schedule(request_schedule_repo)
        service = RequestDealDoneService(
            repo=managers_repo,
            router_service=router,
            schedule_service=schedule,
            admin_chat_ids=set(),
            admin_user_ids=set(),
        )
        await request_schedule_repo.upsert_request_schedule_entry(
            req_id="Б-777",
            city="екб",
            hhmm=None,
            request_kind="dep",
            line_text="+100 RUB — Тестовый чат",
            client_name="Тестовый чат",
            request_chat_id=REQUEST_CHAT,
            request_message_id=777,
        )
        messenger = FakeMessenger()
        replier = CollectingReplier()
        synced_cities: list[str] = []

        async def sync_schedule_board(city: str) -> None:
            synced_cities.append(city)

        result = await service.execute_core(
            RequestDealDoneParams(
                chat_id=REQUEST_CHAT,
                message_id=777,
                card_text="Заявка\n----\nСоздал",
                is_caption=False,
                callback_data="cash:deal_done:req:Б-777",
            ),
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule_board,
        )

        assert result.ok and result.removed_from_schedule
        assert synced_cities == ["екб"]
        assert any("Сделка проведена" in (edit.text or "") for edit in messenger.edits)
        assert "Сделка завершена" in replier.alerts
        entry = await request_schedule_repo.get_request_schedule_entry_by_req_id(req_id="Б-777")
        assert entry is not None and entry["is_active"] is False

    async def test_cancel_edits_caption_without_schedule_entry(
        self, managers_repo, request_schedule_repo
    ) -> None:
        router, schedule = _router_and_schedule(request_schedule_repo)
        service = RequestDealCancelService(
            repo=managers_repo,
            router_service=router,
            schedule_service=schedule,
            admin_chat_ids=set(),
            admin_user_ids=set(),
        )
        messenger = FakeMessenger()
        replier = CollectingReplier()

        result = await service.execute_core(
            RequestDealCancelParams(
                chat_id=REQUEST_CHAT,
                message_id=778,
                card_text="Заявка",
                is_caption=True,
                callback_data="cash:deal_cancel:req:Б-778",
            ),
            messenger=messenger,
            replier=replier,
        )

        assert result.ok and not result.removed_from_schedule
        assert any("Сделка отменена" in (edit.text or "") for edit in messenger.edits)
        assert "Сделка отменена" in replier.alerts


class TestRequestTimeCore:
    async def test_sets_time_and_upserts_schedule(
        self, managers_repo, request_schedule_repo
    ) -> None:
        router, schedule = _router_and_schedule(request_schedule_repo)
        service = RequestTimeService(
            repo=managers_repo,
            router_service=router,
            schedule_service=schedule,
            admin_chat_ids=set(),
            admin_user_ids=set(),
        )
        messenger = FakeMessenger()
        replier = CollectingReplier()
        synced_cities: list[str] = []
        card_text = (
            "Заявка на внесение: Б-909090\nКлиент: Тестовый чат\nСумма: 500 RUB\nКод: 123-456"
        )

        async def sync_schedule_board(city: str) -> None:
            synced_cities.append(city)

        result = await service.execute_core(
            RequestTimeParams(
                request_chat_id=REQUEST_CHAT,
                target_chat_id=REQUEST_CHAT,
                target_message_id=909,
                target_text_html=card_text,
                target_text_plain=card_text,
                is_caption=False,
                reply_markup=None,
                hhmm="09:30",
            ),
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule_board,
        )

        assert result.ok and result.req_id == "Б-909090"
        assert any("Время" in (edit.text or "") for edit in messenger.edits)
        entry = await request_schedule_repo.get_request_schedule_entry_by_req_id(req_id="Б-909090")
        assert entry is not None
        assert entry["hhmm"] == "09:30"
        assert synced_cities == ["екб"]
        assert "✅ Время добавлено: 09:30" in replier.replies
