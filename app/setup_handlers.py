from __future__ import annotations

from aiogram import Bot, Dispatcher

from app.container import ApplicationContainer
from app.lifecycle import SchedulerLifecycleAdapter
from app.office_cards import OFFICE_CARDS
from app.runtime import BotRuntimeServices
from handlers import (
    AcceptShortHandler,
    ActHandler,
    AdminRequestHandler,
    AMLHandler,
    BroadcastAllHandler,
    CalcHandler,
    CashRequestsHandler,
    CityAssignHandler,
    ClientsBalancesHandler,
    ClientsHandler,
    GrinexBookHandler,
    ManagersHandler,
    NonZeroHandler,
    OfficeCardsHandler,
    PaymentWatchHandler,
    RateOrderHandler,
    StartHandler,
    UsdtWalletHandler,
    WalletsHandler,
    XEHandler,
    debug_router,
    get_table_delete_router,
    get_table_done_router,
)
from services.admin_client import (
    ClientBootstrapService,
    ClientDirectoryService,
    ClientGroupService,
    ManagerAdminService,
    NonZeroWalletQueryService,
    UsdtWalletService,
)
from services.aml import AMLQueueService, AMLService, ThreadedAMLChecker
from services.broadcast import BroadcastService
from services.cash_requests import (
    CMD_MAP,
    FX_CMD_MAP,
    CashRequestService,
    RequestDealCancelService,
    RequestDealDoneService,
    RequestDealReadyService,
    RequestIssueService,
    RequestTimeService,
)
from services.client_balances import (
    ClientBalancesFilterService,
    ClientBalancesQueryService,
    ClientBalancesReportBuilder,
    DailyBalancesReportService,
    ScheduledBalancesReportService,
)
from services.daily_balances_scheduler import setup_daily_balances_scheduler
from services.exchange import AcceptShortService
from services.payment_watch import (
    PaymentWatchPoller,
    PaymentWatchService,
    TronscanGateway,
    TronscanSettings,
)
from services.rate_order import (
    OrderbookService,
    RapiraWsService,
    RateOrderService,
)
from services.request_table.delete_interaction_service import RequestTableDeleteInteractionService
from services.request_table.done_interaction_service import RequestTableDoneInteractionService
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.table_done_service import RequestTableDoneService
from services.tg_outbox import DealTelegramSyncService, TgOutboxWorker
from services.wallets import WalletInteractionService, WalletService
from services.xe_api import ConverterAPIService
from telegram_adapters import (
    AiogramBroadcastPresenter,
    AiogramBroadcastSessionStore,
    AiogramExchangeKeyboardPresenter,
    AiogramOrderbookLiveMessageEditor,
    AiogramPaymentWatchNotifier,
    AiogramPaymentWatchPresenter,
    AiogramRequestTableKeyboardPresenter,
    AiogramWalletKeyboardPresenter,
    ChatLockRegistry,
    CityCashMediaStore,
)


def setup_handlers(
    *,
    dp: Dispatcher,
    bot: Bot,
    container: ApplicationContainer,
    ignore_chat_ids: set[int] | None = None,
) -> BotRuntimeServices:
    config = container.config
    repositories = container.operational_repositories
    admin_chat_list = [config.admin_chat_id] if config.admin_chat_id else None
    admin_user_list = config.admin_ids if config.admin_ids else None

    manager_repo = repositories.managers
    settings_repo = repositories.settings
    client_repo = repositories.clients
    wallet_repo = repositories.wallets
    client_wallet_repo = repositories.client_wallets
    client_wallet_tx_repo = repositories.client_wallet_transactions
    live_message_repo = repositories.live_messages
    rate_order_repo = repositories.rate_orders
    exchange_request_repo = repositories.exchange_requests
    payment_watch_repo = repositories.payment_watches
    client_transfer_repo = repositories.client_transfers

    request_chat_id = config.request_chat_id
    city_cash_chats = config.city_cash_chat_map
    ignore_chat_ids = set(ignore_chat_ids or [])

    services = BotRuntimeServices()
    tg_outbox_repository = container.crm_repositories.tg_outbox
    services.tg_outbox_worker = TgOutboxWorker(
        repository=tg_outbox_repository,
        delivery_service=DealTelegramSyncService(
            repository=tg_outbox_repository,
            messenger=container.messenger,
        ),
        metrics=container.metrics,
    )
    act_counter_service = container.exchange.act_counter
    payment_watch_service = PaymentWatchService(
        repo=payment_watch_repo,
        settings=settings_repo,
        tronscan_gateway=TronscanGateway(
            settings=TronscanSettings(
                base_url=config.tronscan_api_base_url or "https://apilist.tronscanapi.com",
                api_key=config.tronscan_api_key,
                usdt_contract=config.tronscan_usdt_contract,
            )
        ),
        settlement_service=container.accounting.deal_settlements,
        fulfillment_queue_service=container.accounting.fulfillment_queue,
        timeout_seconds=config.payment_watch_timeout_seconds,
        metrics=container.metrics,
    )
    payment_watch_presenter = AiogramPaymentWatchPresenter()
    services.payment_watch_poller = PaymentWatchPoller(
        notifier=AiogramPaymentWatchNotifier(
            messenger=container.messenger,
            presenter=payment_watch_presenter,
        ),
        service=payment_watch_service,
        interval_seconds=config.payment_watch_poll_interval_seconds,
    )

    managers_handler = ManagersHandler(ManagerAdminService(manager_repo), config.admin_chat_id)
    dp.include_router(managers_handler.router)

    usdt_wallet_handler = UsdtWalletHandler(
        UsdtWalletService(settings_repo),
        admin_chat_ids={config.admin_chat_id} if getattr(config, "admin_chat_id", None) else set(),
    )
    dp.include_router(usdt_wallet_handler.router)

    start_handler = StartHandler(ClientBootstrapService(client_wallet_repo))
    calc_handler = CalcHandler()
    xe_handler = None
    if config.converter_api_base_url and config.converter_api_token:
        xe_handler = XEHandler(
            repo=manager_repo,
            converter_service=ConverterAPIService(
                base_url=config.converter_api_base_url,
                api_token=config.converter_api_token,
            ),
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
        )

    office_handler = OfficeCardsHandler(OFFICE_CARDS)
    dp.include_router(office_handler.router)
    dp.include_router(debug_router)

    payment_watch_handler = PaymentWatchHandler(
        repo=manager_repo,
        payment_watch_service=payment_watch_service,
        presenter=payment_watch_presenter,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
    )
    dp.include_router(payment_watch_handler.router)

    nonzero_handler = NonZeroHandler(NonZeroWalletQueryService(client_wallet_tx_repo))

    services.market_ws_service = RapiraWsService()

    services.orderbook_service = OrderbookService(
        ws_service=services.market_ws_service,
        repo=live_message_repo,
        live_message_editor=AiogramOrderbookLiveMessageEditor(bot=bot),
        exchange_name="Rapira",
        symbol_label="USDT/RUB",
    )

    services.market_ws_service.on_orderbook_update = services.orderbook_service.refresh_live_message

    grinex_book_handler = GrinexBookHandler(
        manager_repo,
        orderbook_service=services.orderbook_service,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
    )
    dp.include_router(grinex_book_handler.router)

    if config.rate_orders_chat_id:
        services.rate_order_service = RateOrderService(
            repo=rate_order_repo,
            orders_chat_id=config.rate_orders_chat_id,
            get_current_best_ask=lambda: (
                services.market_ws_service.best_ask if services.market_ws_service else None
            ),
        )

        rate_order_handler = RateOrderHandler(
            manager_repo,
            rate_order_service=services.rate_order_service,
            messenger=container.messenger,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
            orders_chat_id=config.rate_orders_chat_id,
        )
        dp.include_router(rate_order_handler.router)

        services.market_ws_service.on_best_ask = (
            lambda ask: services.rate_order_service.process_best_ask(
                messenger=container.messenger,
                best_ask=ask,
            )
        )

    city_cash_media_store = CityCashMediaStore()
    wallet_interaction_service = WalletInteractionService(
        wallet_service=WalletService(
            repo=client_transfer_repo,
            city_cash_chats=city_cash_chats,
            keyboards=AiogramWalletKeyboardPresenter(),
        )
    )
    wallets_handler = WalletsHandler(
        repositories.managed_client_wallet_transactions,
        interaction_service=wallet_interaction_service,
        city_cash_media_store=city_cash_media_store,
        chat_locks=ChatLockRegistry(),
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
        request_chat_id=request_chat_id,
        ignore_chat_ids=None,
        city_cash_chats=city_cash_chats,
        cash_settlement_service=container.accounting.cash_settlements,
    )

    accept_short_service = AcceptShortService(
        repositories.exchange_commands,
        request_chat_id=request_chat_id,
        act_counter_service=act_counter_service,
        deal_registrar=container.crm.telegram_registrar,
        unit_of_work_factory=container.exchange.unit_of_work_factory,
        balance_service=container.exchange.balance,
        calculator=container.exchange.calculator,
        text_builder=container.exchange.text_builder,
        transaction_service=container.exchange.transaction,
        source_links=container.exchange.source_links,
        notification_builder=container.exchange.notifications,
        wallet_presenter=container.exchange.wallet_presenter,
        keyboards=AiogramExchangeKeyboardPresenter(),
        metrics=container.metrics,
    )
    accept_short_handler = AcceptShortHandler(
        manager_repo,
        accept_short_service,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
        ignore_chat_ids=None,
    )
    dp.include_router(accept_short_handler.router)

    if request_chat_id:
        act_handler = ActHandler(
            repo=manager_repo,
            act_counter_service=act_counter_service,
            request_chat_ids=[request_chat_id],
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
        )
        dp.include_router(act_handler.router)

    cash_request_service = CashRequestService(
        repo=repositories.cash_requests,
        router_service=container.cash.router,
        schedule_service=container.cash.schedule,
        cmd_map=CMD_MAP,
        fx_cmd_map=FX_CMD_MAP,
        admin_chat_ids=set(admin_chat_list or []),
        admin_user_ids=set(admin_user_list or []),
        deal_registrar=container.crm.telegram_registrar,
        calculator=container.cash.calculator,
        card_presenter=container.cash.card_presenter,
        card_parser=container.cash.card_parser,
        schedule_coordinator=container.cash.schedule_coordinator,
        metrics=container.metrics,
    )
    cash_requests_handler = CashRequestsHandler(
        repo=manager_repo,
        admin_chat_ids=set(admin_chat_list or []),
        admin_user_ids=set(admin_user_list or []),
        request_service=cash_request_service,
        request_time_service=RequestTimeService(
            repo=manager_repo,
            router_service=container.cash.router,
            schedule_service=container.cash.schedule,
            admin_chat_ids=set(admin_chat_list or []),
            admin_user_ids=set(admin_user_list or []),
            schedule_coordinator=container.cash.schedule_coordinator,
        ),
        request_issue_service=RequestIssueService(
            repo=repositories.client_wallet_transactions,
            admin_chat_ids=set(admin_chat_list or []),
            admin_user_ids=set(admin_user_list or []),
            card_parser=container.cash.card_parser,
        ),
        request_deal_ready_service=RequestDealReadyService(
            repo=manager_repo,
            router_service=container.cash.router,
            schedule_service=container.cash.schedule,
            admin_chat_ids=set(admin_chat_list or []),
            admin_user_ids=set(admin_user_list or []),
            workflow=container.cash.status_workflow,
            card_parser=container.cash.card_parser,
        ),
        request_deal_done_service=RequestDealDoneService(
            repo=manager_repo,
            router_service=container.cash.router,
            schedule_service=container.cash.schedule,
            admin_chat_ids=set(admin_chat_list or []),
            admin_user_ids=set(admin_user_list or []),
            workflow=container.cash.status_workflow,
            card_parser=container.cash.card_parser,
        ),
        request_deal_cancel_service=RequestDealCancelService(
            repo=manager_repo,
            router_service=container.cash.router,
            schedule_service=container.cash.schedule,
            admin_chat_ids=set(admin_chat_list or []),
            admin_user_ids=set(admin_user_list or []),
            workflow=container.cash.status_workflow,
            card_parser=container.cash.card_parser,
        ),
    )
    dp.include_router(cash_requests_handler.router)

    admin_request_handler = AdminRequestHandler(
        repositories.client_wallets,
        admin_chat_id=config.admin_chat_id,
        request_chat_id=request_chat_id,
        admin_user_ids=config.admin_ids,
    )
    dp.include_router(admin_request_handler.router)

    balances_query_service = ClientBalancesQueryService(wallet_repo)
    balances_filter_service = ClientBalancesFilterService()
    balances_report_builder = ClientBalancesReportBuilder()
    daily_balances_report_service = DailyBalancesReportService(
        query_service=balances_query_service,
        filter_service=balances_filter_service,
        report_builder=balances_report_builder,
    )

    clients_balances_handler = ClientsBalancesHandler(
        report_service=daily_balances_report_service,
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(clients_balances_handler.router)

    services.daily_balances_scheduler = SchedulerLifecycleAdapter(
        setup_daily_balances_scheduler(
            report_service=ScheduledBalancesReportService(
                report_service=daily_balances_report_service,
                messenger=container.messenger,
                admin_chat_ids=config.schedule_chat_ids,
            ),
            timezone="Asia/Yekaterinburg",
        )
    )

    clients_handler = ClientsHandler(
        ClientDirectoryService(client_repo),
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(clients_handler.router)

    broadcast_all_handler = BroadcastAllHandler(
        manager_repo,
        broadcast_service=BroadcastService(repo=client_repo),
        session_store=AiogramBroadcastSessionStore(),
        preview_builder=AiogramBroadcastPresenter(),
        admin_chat_ids=set(admin_chat_list or []),
        admin_user_ids=set(admin_user_list or []),
    )
    dp.include_router(broadcast_all_handler.router)

    city_handler = CityAssignHandler(
        ClientGroupService(client_repo),
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(city_handler.router)

    if config.getblock:
        services.aml_queue_service = AMLQueueService(
            checker=ThreadedAMLChecker(
                AMLService(settings=config.getblock),
            ),
            metrics=container.metrics,
        )
        aml_handler = AMLHandler(
            manager_repo,
            aml_queue_service=services.aml_queue_service,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
        )
        dp.include_router(aml_handler.router)

    dp.include_router(start_handler.router)
    dp.include_router(calc_handler.router)
    if xe_handler:
        dp.include_router(xe_handler.router)
    dp.include_router(nonzero_handler.router)
    dp.include_router(wallets_handler.router)

    if request_chat_id:
        sheets_gateway = container.sheets_gateway
        message_builder = RequestTableMessageBuilder()
        request_table_keyboards = AiogramRequestTableKeyboardPresenter()
        dp.include_router(
            get_table_done_router(
                interaction_service=RequestTableDoneInteractionService(
                    repo=exchange_request_repo,
                    request_chat_ids=[request_chat_id],
                    done_service=RequestTableDoneService(sheets_gateway=sheets_gateway),
                    session_store=RequestTableSessionStore(),
                    message_builder=message_builder,
                    sheets_gateway=sheets_gateway,
                    keyboards=request_table_keyboards,
                )
            )
        )
        dp.include_router(
            get_table_delete_router(
                interaction_service=RequestTableDeleteInteractionService(
                    request_chat_ids=[request_chat_id],
                    session_store=RequestTableSessionStore(),
                    message_builder=message_builder,
                    sheets_gateway=sheets_gateway,
                    keyboards=request_table_keyboards,
                )
            )
        )

    return services
