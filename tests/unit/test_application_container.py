from __future__ import annotations

from unittest.mock import MagicMock

from aiogram import Bot, Dispatcher

from api.app import create_api_app
from app.container import ApplicationContainer
from app.setup_handlers import setup_handlers
from config import Config
from tests.fakes import FakeMessenger


def _config() -> Config:
    return Config(
        bot_token="123456:test-token",
        database_url="postgresql://test:test@127.0.0.1:5432/test",
        converter_api_base_url=None,
        converter_api_token=None,
        tronscan_api_base_url=None,
        tronscan_api_key=None,
        tronscan_usdt_contract="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        payment_watch_poll_interval_seconds=30,
        payment_watch_timeout_seconds=900,
        admin_chat_id=-100001,
        admin_ids=[42],
        request_chat_id=-100002,
        cash_chat_map={"екб": -100003},
        city_schedule_chats={"екб": -100004},
        schedule_chat_ids=set(),
        rate_orders_chat_id=None,
        default_city="екб",
        city_cash_chat_map={"екб": -100005},
        getblock=None,
        api_enabled=True,
        api_host="127.0.0.1",
        api_port=8000,
        api_cors_origins=[],
        api_jwt_ttl_seconds=3600,
        api_dev_auth_bypass=False,
        api_dev_tg_user_id=None,
    )


async def test_container_builds_shared_api_and_telegram_graph() -> None:
    config = _config()
    messenger = FakeMessenger()
    pool = MagicMock()
    container = ApplicationContainer.build(config, pool, messenger=messenger)
    api = create_api_app(config, container=container)
    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher()
    try:
        runtime = setup_handlers(
            dp=dispatcher,
            bot=bot,
            container=container,
            ignore_chat_ids={config.request_chat_id},
        )
    finally:
        await bot.session.close()

    assert api.state.container is container
    assert api.state.client_queries is container.api_queries.clients
    assert api.state.balance_queries is container.api_queries.balances
    assert api.state.dashboard_queries is container.api_queries.dashboard
    assert container.pool is pool
    assert api.state.deal_service is container.crm.deals
    assert api.state.deal_event_bus is container.crm.event_bus
    assert api.state.deal_source_mutation_service is container.crm.source_mutation
    assert api.state.metrics is container.metrics
    assert container.crm.deals._metrics is container.metrics
    assert container.crm.telegram_registrar._deal_service is container.crm.deals
    assert container.crm.source_mutation._deals is container.crm.deals
    assert container.accounting.firm_positions is not None
    assert container.accounting.cash_chat_registry is not None
    assert runtime.tg_outbox_worker is not None
    assert runtime.tg_outbox_worker._metrics is container.metrics
    assert runtime.payment_watch_poller is not None
    assert runtime.payment_watch_poller.service._metrics is container.metrics
    assert runtime.market_ws_service is not None
    assert len(dispatcher.sub_routers) > 10
