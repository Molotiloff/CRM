from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from api.queries import BalanceQueryService, ClientQueryService, DashboardQueryService
from api.rate_providers import GutilsFirmRateProvider
from api.read_repositories import (
    BalanceReadRepository,
    ClientReadRepository,
    DashboardReadRepository,
)
from api.repositories import UserRepository
from config import Config
from db_asyncpg.adapters import (
    ActCounterLedgerRepositoryAdapter,
    ClientWalletScheduleContextAdapter,
    ClientWalletTransactionRepositoryAdapter,
    DealSourceRecordReaderAdapter,
    DealSourceRepositoryAdapter,
    ManagedClientWalletTransactionRepositoryAdapter,
)
from db_asyncpg.ports.administration import ManagerRepositoryPort, SettingsRepositoryPort
from db_asyncpg.ports.clients import ClientRepositoryPort, WalletRepositoryPort
from db_asyncpg.ports.exchange import ExchangeRequestRepositoryPort
from db_asyncpg.ports.market import LiveMessageRepositoryPort, RateOrderRepositoryPort
from db_asyncpg.ports.payment_watch import PaymentWatchRepositoryPort
from db_asyncpg.ports.workflows import (
    ActCounterLedgerRepositoryPort,
    CashRequestContextRepositoryPort,
    ClientTransferRepositoryPort,
    ClientWalletRepositoryPort,
    ClientWalletTransactionRepositoryPort,
    ExchangeCommandRepositoryPort,
    ManagedClientWalletTransactionRepositoryPort,
)
from db_asyncpg.repositories import (
    ActCounterRepo,
    ClientsRepo,
    ExchangeRequestsRepo,
    LiveMessagesRepo,
    ManagersRepo,
    PaymentWatchRepo,
    RateOrdersRepo,
    RequestScheduleRepo,
    SettingsRepo,
    TransactionsRepo,
)
from db_asyncpg.repositories.cash_chat_registry import CashChatRegistryRepo
from db_asyncpg.repositories.deal_workflow import DealWorkflowRepository
from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.repositories.tg_outbox import TgOutboxRepository
from db_asyncpg.uow import AsyncpgUnitOfWork
from gutils.requests_sheet_gateway import (
    GutilsSheetsTradeGateway,
    ThreadedSheetsTradeGateway,
)
from observability import InMemoryMetrics, MetricsPort
from services.accounting import CashChatRegistrySyncService, FirmPositionAccountingService
from services.act_counter import ActCounterService
from services.cash_requests.calculator import CashRequestCalculator
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.card_presenter import CashCardPresenter
from services.cash_requests.deal_status_workflow import CashDealStatusWorkflow
from services.cash_requests.edit_cash_request import EditCashRequest
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator
from services.crm.cash_deal_source_adapter import CashDealSourceAdapter
from services.crm.deal_events import DealEventBus
from services.crm.deal_service import DealService
from services.crm.deal_source_adapter import DealSourceAdapterRegistry
from services.crm.deal_source_mutation import DealSourceMutationService
from services.crm.deal_status_policy import DealStatusPolicy
from services.crm.exchange_deal_source_adapter import ExchangeDealSourceAdapter
from services.crm.telegram_deal_registrar import TelegramDealRegistrar
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.notification_builder import ExchangeNotificationBuilder
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.text_builder import ExchangeTextBuilder
from services.exchange.transaction_service import ExchangeTransactionService
from services.exchange.wallet_presenter import ExchangeWalletPresenter
from services.messaging import DeferredMessenger, MessengerPort
from services.request_table import AsyncSheetsTradeGateway
from services.unit_of_work import UnitOfWorkFactory
from telegram_adapters import AiogramCashKeyboardPresenter


@dataclass(frozen=True, slots=True)
class OperationalRepositories:
    """Narrow repository views used by application and Telegram services."""

    managers: ManagerRepositoryPort
    settings: SettingsRepositoryPort
    clients: ClientRepositoryPort
    wallets: WalletRepositoryPort
    client_wallets: ClientWalletRepositoryPort
    cash_requests: CashRequestContextRepositoryPort
    client_wallet_transactions: ClientWalletTransactionRepositoryPort
    managed_client_wallet_transactions: ManagedClientWalletTransactionRepositoryPort
    client_transfers: ClientTransferRepositoryPort
    exchange_requests: ExchangeRequestRepositoryPort
    exchange_commands: ExchangeCommandRepositoryPort
    live_messages: LiveMessageRepositoryPort
    rate_orders: RateOrderRepositoryPort
    act_counter_ledger: ActCounterLedgerRepositoryPort
    payment_watches: PaymentWatchRepositoryPort

    @classmethod
    def build(cls, pool: asyncpg.Pool) -> OperationalRepositories:
        clients = ClientsRepo(pool)
        transactions = TransactionsRepo(pool)
        managers = ManagersRepo(pool)
        schedule = RequestScheduleRepo(pool)
        client_transactions = ClientWalletTransactionRepositoryAdapter(clients, transactions)
        return cls(
            managers=managers,
            settings=SettingsRepo(pool),
            clients=clients,
            wallets=clients,
            client_wallets=clients,
            cash_requests=ClientWalletScheduleContextAdapter(clients, schedule),
            client_wallet_transactions=client_transactions,
            managed_client_wallet_transactions=(
                ManagedClientWalletTransactionRepositoryAdapter(clients, transactions, managers)
            ),
            client_transfers=client_transactions,
            exchange_requests=ExchangeRequestsRepo(pool),
            exchange_commands=ClientWalletScheduleContextAdapter(clients, schedule),
            live_messages=LiveMessagesRepo(pool),
            rate_orders=RateOrdersRepo(pool),
            act_counter_ledger=ActCounterLedgerRepositoryAdapter(
                clients, transactions, ActCounterRepo(pool)
            ),
            payment_watches=PaymentWatchRepo(pool),
        )


@dataclass(frozen=True, slots=True)
class CrmRepositories:
    users: UserRepository
    client_reads: ClientReadRepository
    balance_reads: BalanceReadRepository
    dashboard_reads: DashboardReadRepository
    deals: DealRepository
    deal_workflow: DealWorkflowRepository
    tg_outbox: TgOutboxRepository
    deal_sources: DealSourceRepositoryAdapter


@dataclass(frozen=True, slots=True)
class CrmServices:
    event_bus: DealEventBus
    deals: DealService
    telegram_registrar: TelegramDealRegistrar
    source_mutation: DealSourceMutationService


@dataclass(frozen=True, slots=True)
class ApiQueryServices:
    clients: ClientQueryService
    balances: BalanceQueryService
    dashboard: DashboardQueryService


@dataclass(frozen=True, slots=True)
class CashServices:
    router: RequestRouterService
    schedule: RequestScheduleService
    schedule_coordinator: CashScheduleCoordinator
    calculator: CashRequestCalculator
    card_parser: CashCardParser
    card_presenter: CashCardPresenter
    status_workflow: CashDealStatusWorkflow


@dataclass(frozen=True, slots=True)
class ExchangeServices:
    unit_of_work_factory: UnitOfWorkFactory
    balance: ExchangeBalanceService
    calculator: ExchangeCalculator
    text_builder: ExchangeTextBuilder
    transaction: ExchangeTransactionService
    source_links: ExchangeSourceLinkService
    notifications: ExchangeNotificationBuilder
    wallet_presenter: ExchangeWalletPresenter
    act_counter: ActCounterService


@dataclass(frozen=True, slots=True)
class AccountingServices:
    firm_positions: FirmPositionAccountingService
    cash_chat_registry: CashChatRegistrySyncService


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    config: Config
    pool: asyncpg.Pool
    operational_repositories: OperationalRepositories
    crm_repositories: CrmRepositories
    crm: CrmServices
    api_queries: ApiQueryServices
    cash: CashServices
    exchange: ExchangeServices
    accounting: AccountingServices
    sheets_gateway: AsyncSheetsTradeGateway
    rate_provider: GutilsFirmRateProvider
    messenger: MessengerPort
    metrics: MetricsPort

    @classmethod
    def build(
        cls,
        config: Config,
        pool: asyncpg.Pool,
        *,
        messenger: MessengerPort | None = None,
    ) -> ApplicationContainer:
        transport_messenger = messenger or DeferredMessenger()
        metrics = InMemoryMetrics()
        operational = OperationalRepositories.build(pool)
        source_reader = DealSourceRecordReaderAdapter(
            operational.exchange_requests,
            RequestScheduleRepo(pool),
        )
        crm_repositories = CrmRepositories(
            users=UserRepository(pool),
            client_reads=ClientReadRepository(pool),
            balance_reads=BalanceReadRepository(pool),
            dashboard_reads=DashboardReadRepository(pool),
            deals=DealRepository(pool),
            deal_workflow=DealWorkflowRepository(pool),
            tg_outbox=TgOutboxRepository(pool),
            deal_sources=DealSourceRepositoryAdapter(source_reader),
        )
        event_bus = DealEventBus()
        deal_service = DealService(
            crm_repositories.deals,
            event_bus,
            DealStatusPolicy(crm_repositories.deal_workflow),
            metrics=metrics,
        )
        telegram_registrar = TelegramDealRegistrar(
            deal_service,
            default_city=config.default_city,
        )
        request_router = RequestRouterService(
            request_chat_id=config.request_chat_id,
            city_cash_chats=config.cash_chat_map,
            city_schedule_chats=config.city_schedule_chats,
            default_city=config.default_city,
        )
        request_schedule = RequestScheduleService(
            repo=RequestScheduleRepo(pool),
            router_service=request_router,
        )
        cash_schedule_coordinator = CashScheduleCoordinator(
            router_service=request_router,
            schedule_service=request_schedule,
        )
        cash_calculator = CashRequestCalculator()
        cash_card_parser = CashCardParser()
        cash_card_presenter = CashCardPresenter(AiogramCashKeyboardPresenter())
        cash_status_workflow = CashDealStatusWorkflow(
            router_service=request_router,
            schedule_coordinator=cash_schedule_coordinator,
        )

        def build_unit_of_work() -> AsyncpgUnitOfWork:
            return AsyncpgUnitOfWork(pool)

        unit_of_work_factory: UnitOfWorkFactory = build_unit_of_work
        exchange_balance = ExchangeBalanceService(unit_of_work_factory)
        request_chat_ids = set(config.cash_chat_map.values())
        if config.request_chat_id is not None:
            request_chat_ids.add(config.request_chat_id)
        accounting = AccountingServices(
            firm_positions=FirmPositionAccountingService(unit_of_work_factory),
            cash_chat_registry=CashChatRegistrySyncService(
                CashChatRegistryRepo(pool),
                city_cash_chats=config.city_cash_chat_map,
                request_chat_ids=frozenset(request_chat_ids),
            ),
        )
        exchange = ExchangeServices(
            unit_of_work_factory=unit_of_work_factory,
            balance=exchange_balance,
            calculator=ExchangeCalculator(),
            text_builder=ExchangeTextBuilder(),
            transaction=ExchangeTransactionService(
                unit_of_work_factory=unit_of_work_factory,
                balance_service=exchange_balance,
            ),
            source_links=ExchangeSourceLinkService(operational.exchange_requests),
            notifications=ExchangeNotificationBuilder(),
            wallet_presenter=ExchangeWalletPresenter(),
            act_counter=ActCounterService(operational.act_counter_ledger),
        )
        cash_source_edit = EditCashRequest(
            repo=operational.cash_requests,
            router_service=request_router,
            schedule_service=request_schedule,
            calculator=cash_calculator,
            card_presenter=cash_card_presenter,
            card_parser=cash_card_parser,
            schedule_coordinator=cash_schedule_coordinator,
            metrics=metrics,
        )
        source_mutation = DealSourceMutationService(
            deal_service=deal_service,
            unit_of_work_factory=unit_of_work_factory,
            adapters=DealSourceAdapterRegistry(
                (
                    ExchangeDealSourceAdapter(
                        repository=crm_repositories.deal_sources,
                        balance_service=exchange.balance,
                    ),
                    CashDealSourceAdapter(
                        repository=crm_repositories.deal_sources,
                        cash_edit=cash_source_edit,
                    ),
                )
            ),
        )
        sheets_gateway = ThreadedSheetsTradeGateway(GutilsSheetsTradeGateway())
        dashboard_provider = GutilsFirmRateProvider(sheets_gateway)
        api_queries = ApiQueryServices(
            clients=ClientQueryService(crm_repositories.client_reads),
            balances=BalanceQueryService(crm_repositories.balance_reads),
            dashboard=DashboardQueryService(
                balance_repository=crm_repositories.balance_reads,
                dashboard_repository=crm_repositories.dashboard_reads,
                rate_provider=dashboard_provider,
                sheet_provider=dashboard_provider,
            ),
        )
        return cls(
            config=config,
            pool=pool,
            operational_repositories=operational,
            crm_repositories=crm_repositories,
            crm=CrmServices(
                event_bus=event_bus,
                deals=deal_service,
                telegram_registrar=telegram_registrar,
                source_mutation=source_mutation,
            ),
            api_queries=api_queries,
            cash=CashServices(
                router=request_router,
                schedule=request_schedule,
                schedule_coordinator=cash_schedule_coordinator,
                calculator=cash_calculator,
                card_parser=cash_card_parser,
                card_presenter=cash_card_presenter,
                status_workflow=cash_status_workflow,
            ),
            exchange=exchange,
            accounting=accounting,
            sheets_gateway=sheets_gateway,
            rate_provider=dashboard_provider,
            messenger=transport_messenger,
            metrics=metrics,
        )
