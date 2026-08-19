from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UTILS_ROOT = PROJECT_ROOT / "utils"
SERVICES_ROOT = PROJECT_ROOT / "services"
DOMAIN_ROOT = PROJECT_ROOT / "domain"
HANDLERS_ROOT = PROJECT_ROOT / "handlers"
API_ROUTERS_ROOT = PROJECT_ROOT / "api" / "routers"
API_READ_ROUTERS = (
    PROJECT_ROOT / "api" / "routers" / "balances.py",
    PROJECT_ROOT / "api" / "routers" / "clients.py",
    PROJECT_ROOT / "api" / "routers" / "dashboard.py",
)
REPOSITORIES_ROOT = PROJECT_ROOT / "db_asyncpg" / "repositories"
REPOSITORY_PORTS_ROOT = PROJECT_ROOT / "db_asyncpg" / "ports"
LEGACY_REPOSITORY_FACADE = PROJECT_ROOT / "db_asyncpg" / "repo.py"
LEGACY_REPOSITORY_PORTS_MODULE = PROJECT_ROOT / "db_asyncpg" / "ports.py"
POOL_MODULE = PROJECT_ROOT / "db_asyncpg" / "pool.py"
API_REPOSITORY_MODULES = (
    PROJECT_ROOT / "api" / "repositories.py",
    PROJECT_ROOT / "api" / "read_repositories" / "balances.py",
    PROJECT_ROOT / "api" / "read_repositories" / "clients.py",
    PROJECT_ROOT / "api" / "read_repositories" / "dashboard.py",
)
API_MONOLITH_MODULES = (PROJECT_ROOT / "api" / "models.py",)
LEGACY_API_PRESENTER = PROJECT_ROOT / "api" / "presenters.py"
LEGACY_API_READ_REPOSITORY = PROJECT_ROOT / "api" / "read_repositories.py"
LEGACY_SHEETS_GATEWAY = PROJECT_ROOT / "gutils" / "sheets.py"
LEGACY_REQUEST_INDEX = PROJECT_ROOT / "utils" / "req_index.py"
LEGACY_PROCESS_GLOBALS = (
    PROJECT_ROOT / "utils" / "locks.py",
    PROJECT_ROOT / "utils" / "undos.py",
)
LEGACY_CASH_REQUEST_UTILS = (
    PROJECT_ROOT / "utils" / "request_audit.py",
    PROJECT_ROOT / "utils" / "request_cards.py",
    PROJECT_ROOT / "utils" / "request_parsing.py",
    PROJECT_ROOT / "utils" / "request_text_parser.py",
)
LEGACY_CASH_REQUEST_WRAPPERS = (
    SERVICES_ROOT / "cash_requests" / "legacy_request_messages.py",
    SERVICES_ROOT / "cash_requests" / "legacy_request_parsing.py",
)
LEGACY_TELEGRAM_UTILS = (
    PROJECT_ROOT / "utils" / "auth.py",
    PROJECT_ROOT / "utils" / "errors.py",
    PROJECT_ROOT / "utils" / "info.py",
    PROJECT_ROOT / "telegram_adapters" / "message_actor.py",
)
LEGACY_NUMBER_FORMATTERS = (
    PROJECT_ROOT / "utils" / "formatting.py",
    SERVICES_ROOT / "exchange" / "rate_formatting.py",
)
EXCHANGE_ORCHESTRATORS = (
    SERVICES_ROOT / "exchange" / "create_exchange_request.py",
    SERVICES_ROOT / "exchange" / "edit_exchange_request.py",
    SERVICES_ROOT / "exchange" / "cancel_exchange_request.py",
)
CASH_ORCHESTRATORS = (
    SERVICES_ROOT / "cash_requests" / "create_cash_request.py",
    SERVICES_ROOT / "cash_requests" / "edit_cash_request.py",
)
CRM_SOURCE_MUTATION = SERVICES_ROOT / "crm" / "deal_source_mutation.py"

INFRASTRUCTURE_IMPORTS = frozenset({"asyncpg", "fastapi", "google", "googleapiclient", "gspread"})
TRANSPORT_IMPORTS = frozenset({"aiogram", "fastapi"})
INTERFACE_LAYER_IMPORTS = frozenset(
    {"aiogram", "fastapi", "handlers", "keyboards", "telegram_adapters"}
)
CENTRALLY_MAPPED_API_EXCEPTIONS = frozenset(
    {
        "AuthError",
        "ClientNotFoundError",
        "DealNotFoundError",
        "DealStatusConflictError",
        "DealValidationError",
        "DomainStateError",
        "DomainValidationError",
    }
)


def _python_files(root: Path) -> Iterable[Path]:
    return (path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.partition(".")[0])
    return imports


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _package_exports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    assert isinstance(assignment.value, ast.List | ast.Tuple)
    return {
        element.value
        for element in assignment.value.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def test_service_layer_does_not_import_persistence_or_web_frameworks() -> None:
    violations = {
        _relative(path): sorted(_top_level_imports(path) & INFRASTRUCTURE_IMPORTS)
        for path in _python_files(SERVICES_ROOT)
        if _top_level_imports(path) & INFRASTRUCTURE_IMPORTS
    }

    assert violations == {}


def test_domain_does_not_import_infrastructure_or_transport_frameworks() -> None:
    forbidden = INFRASTRUCTURE_IMPORTS | TRANSPORT_IMPORTS
    violations = {
        _relative(path): sorted(_top_level_imports(path) & forbidden)
        for path in _python_files(DOMAIN_ROOT)
        if _top_level_imports(path) & forbidden
    }

    assert violations == {}


def test_crm_application_core_is_transport_agnostic() -> None:
    crm_root = SERVICES_ROOT / "crm"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(crm_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_exchange_application_is_transport_agnostic() -> None:
    exchange_root = SERVICES_ROOT / "exchange"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(exchange_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_cash_application_is_transport_agnostic() -> None:
    cash_root = SERVICES_ROOT / "cash_requests"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(cash_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_exchange_and_cash_do_not_import_telegram_keyboards() -> None:
    roots = (SERVICES_ROOT / "exchange", SERVICES_ROOT / "cash_requests")
    violations = {
        _relative(path)
        for root in roots
        for path in _python_files(root)
        if "keyboards" in _top_level_imports(path)
    }

    assert violations == set()


def test_wallet_application_is_transport_agnostic() -> None:
    wallet_root = SERVICES_ROOT / "wallets"
    forbidden = TRANSPORT_IMPORTS | {"keyboards"}
    violations = {
        _relative(path): sorted(_top_level_imports(path) & forbidden)
        for path in _python_files(wallet_root)
        if _top_level_imports(path) & forbidden
    }

    assert violations == {}


def test_broadcast_application_is_transport_agnostic() -> None:
    broadcast_root = SERVICES_ROOT / "broadcast"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(broadcast_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_payment_watch_application_is_transport_agnostic() -> None:
    payment_watch_root = SERVICES_ROOT / "payment_watch"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(payment_watch_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_rate_order_application_is_transport_agnostic() -> None:
    rate_order_root = SERVICES_ROOT / "rate_order"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(rate_order_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_request_table_application_is_transport_agnostic() -> None:
    request_table_root = SERVICES_ROOT / "request_table"
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for path in _python_files(request_table_root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_reporting_application_services_are_transport_agnostic() -> None:
    roots = (SERVICES_ROOT / "admin_client", SERVICES_ROOT / "client_balances")
    violations = {
        _relative(path): sorted(_top_level_imports(path) & TRANSPORT_IMPORTS)
        for root in roots
        for path in _python_files(root)
        if _top_level_imports(path) & TRANSPORT_IMPORTS
    }

    assert violations == {}


def test_services_do_not_import_aiogram() -> None:
    offenders = {
        _relative(path)
        for path in _python_files(SERVICES_ROOT)
        if "aiogram" in _top_level_imports(path)
    }

    assert offenders == set(), (
        "aiogram dependencies in services are forbidden; convert transport "
        f"objects in handlers or telegram_adapters instead: {sorted(offenders)}"
    )


def test_services_do_not_depend_on_interface_layer() -> None:
    violations = {
        _relative(path): sorted(_top_level_imports(path) & INTERFACE_LAYER_IMPORTS)
        for path in _python_files(SERVICES_ROOT)
        if _top_level_imports(path) & INTERFACE_LAYER_IMPORTS
    }

    assert violations == {}


def test_service_packages_import_in_fresh_interpreters() -> None:
    for package in (
        "services.crm",
        "services.exchange",
        "services.cash_requests",
        "services.broadcast",
        "services.payment_watch",
        "services.rate_order",
        "services.request_table",
        "services.messaging",
    ):
        completed = subprocess.run(
            [sys.executable, "-c", f"import {package}"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_handlers_do_not_construct_application_services() -> None:
    violations: list[str] = []
    for path in _python_files(HANDLERS_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callable_name = ""
            if isinstance(node.func, ast.Name):
                callable_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callable_name = node.func.attr
            if callable_name.endswith("Service"):
                violations.append(f"{_relative(path)}:{node.lineno}:{callable_name}")

    assert violations == []


def test_concrete_repositories_do_not_access_global_pool() -> None:
    violations: list[str] = []
    repository_modules = [*_python_files(REPOSITORIES_ROOT), *API_REPOSITORY_MODULES]
    for path in repository_modules:
        if path.name == "base.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "db_asyncpg.pool":
                continue
            if any(alias.name == "get_pool" for alias in node.names):
                violations.append(f"{_relative(path)}:{node.lineno}")

    assert violations == [], (
        f"Concrete repositories must obtain connections through ConnectionBoundRepo: {violations}"
    )


def test_legacy_repository_facade_is_removed() -> None:
    violations: list[str] = []
    for root in (
        PROJECT_ROOT / "api",
        PROJECT_ROOT / "app",
        PROJECT_ROOT / "db_asyncpg",
        HANDLERS_ROOT,
        SERVICES_ROOT,
        PROJECT_ROOT / "tests",
    ):
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "db_asyncpg.repo":
                    violations.append(f"{_relative(path)}:{node.lineno}")

    assert not LEGACY_REPOSITORY_FACADE.exists()
    assert violations == []


def test_repository_ports_are_split_by_bounded_context() -> None:
    expected_modules = {
        "administration.py",
        "cash.py",
        "clients.py",
        "exchange.py",
        "ledger.py",
        "market.py",
        "payment_watch.py",
        "workflows.py",
    }
    modules = {
        path.name for path in REPOSITORY_PORTS_ROOT.glob("*.py") if path.name != "__init__.py"
    }
    definitions: dict[str, str] = {}
    duplicates: list[str] = []
    for path in _python_files(REPOSITORY_PORTS_ROOT):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Port"):
                continue
            if node.name in definitions:
                duplicates.append(node.name)
            definitions[node.name] = path.name

    assert not LEGACY_REPOSITORY_PORTS_MODULE.exists()
    assert modules == expected_modules
    assert duplicates == []
    assert definitions


def test_repository_ports_root_has_no_compatibility_exports() -> None:
    package_init = REPOSITORY_PORTS_ROOT / "__init__.py"
    tree = ast.parse(package_init.read_text(encoding="utf-8"), filename=str(package_init))

    assert not any(
        isinstance(node, ast.Import | ast.ImportFrom | ast.ClassDef | ast.FunctionDef)
        for node in tree.body
    )


def test_wallet_package_exposes_only_application_entry_points() -> None:
    package_init = SERVICES_ROOT / "wallets" / "__init__.py"
    models_tree = ast.parse((SERVICES_ROOT / "wallets" / "models.py").read_text(encoding="utf-8"))
    model_names = {node.name for node in models_tree.body if isinstance(node, ast.ClassDef)}

    assert _package_exports(package_init) == {
        "WalletInteractionService",
        "WalletService",
    }
    assert "CityTransferResultView" not in model_names


def test_workflow_packages_expose_only_consumed_entry_points() -> None:
    assert _package_exports(SERVICES_ROOT / "exchange" / "__init__.py") == {
        "AcceptShortCommand",
        "AcceptShortService",
        "CancelExchangeParams",
        "ExchangeReplyContext",
    }
    assert _package_exports(SERVICES_ROOT / "payment_watch" / "__init__.py") == {
        "PaymentWatchError",
        "PaymentWatchPoller",
        "PaymentWatchService",
        "StartPaymentWatchCommand",
        "TronscanGateway",
        "TronscanSettings",
    }
    assert _package_exports(SERVICES_ROOT / "cash_requests" / "__init__.py") == {
        "CMD_MAP",
        "FX_CMD_MAP",
        "CashCardParser",
        "CashDealStatusCardPresenter",
        "CashRequestCalculationError",
        "CashRequestCalculator",
        "CashRequestService",
        "CashScheduleCoordinator",
        "CreateCashRequest",
        "CreateCashRequestParams",
        "EditCashRequest",
        "EditCashRequestParams",
        "EditCashRequestResult",
        "RequestDealCancelParams",
        "RequestDealCancelService",
        "RequestDealDoneParams",
        "RequestDealDoneService",
        "RequestIssueParams",
        "RequestIssueService",
        "RequestRouterService",
        "RequestScheduleService",
        "RequestTimeParams",
        "RequestTimeService",
        "ScheduleEntry",
    }
    cash_parser = ast.parse(
        (SERVICES_ROOT / "cash_requests" / "card_text_parser.py").read_text(encoding="utf-8")
    )
    assert "extract_req_id" not in {
        node.name
        for node in cash_parser.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_remaining_service_packages_expose_only_consumed_entry_points() -> None:
    assert _package_exports(SERVICES_ROOT / "admin_client" / "__init__.py") == {
        "USDT_WALLET_SETTING_KEY",
        "ClientBootstrapService",
        "ClientDirectoryService",
        "ClientGroupService",
        "ManagerAdminService",
        "NonZeroWalletQueryService",
        "UsdtWalletService",
    }
    assert _package_exports(SERVICES_ROOT / "client_balances" / "__init__.py") == {
        "MINUS_CHARS",
        "PLUS_CHARS",
        "ClientBalancesFilterService",
        "ClientBalancesQueryService",
        "ClientBalancesReportBuilder",
        "DailyBalancesReportService",
        "ScheduledBalancesReportService",
    }
    assert _package_exports(SERVICES_ROOT / "crm" / "__init__.py") == {
        "DealEventBus",
        "DealListFilter",
        "DealService",
        "FirmPositionService",
        "StatisticsService",
        "TelegramDealRegistrar",
    }
    crm_package = ast.parse((SERVICES_ROOT / "crm" / "__init__.py").read_text(encoding="utf-8"))
    assert "__getattr__" not in {
        node.name
        for node in crm_package.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert _package_exports(SERVICES_ROOT / "rate_order" / "__init__.py") == {
        "LiveMessageEditResult",
        "LiveMessageEditStatus",
        "OrderbookService",
        "RapiraWsService",
        "RateOrderService",
    }
    assert _package_exports(SERVICES_ROOT / "request_table" / "__init__.py") == {
        "AsyncSheetsTradeGateway"
    }
    assert _package_exports(SERVICES_ROOT / "messaging" / "__init__.py") == {
        "CollectingReplier",
        "DeferredMessenger",
        "MessengerError",
        "MessengerPort",
        "ReplierPort",
        "SentMessageRef",
        "suppress_benign_messenger_errors",
    }


def test_background_service_packages_expose_only_consumed_entry_points() -> None:
    assert _package_exports(SERVICES_ROOT / "act_counter" / "__init__.py") == {
        "ActCounterService",
        "AppliedExchangeMovement",
    }
    assert _package_exports(SERVICES_ROOT / "aml" / "__init__.py") == {
        "AMLQueueService",
        "AMLQueueTask",
        "AMLService",
        "ThreadedAMLChecker",
    }
    assert _package_exports(SERVICES_ROOT / "broadcast" / "__init__.py") == {
        "BroadcastCommand",
        "BroadcastDeliveryStatus",
        "BroadcastService",
    }
    assert _package_exports(SERVICES_ROOT / "tg_outbox" / "__init__.py") == {
        "DealTelegramSyncService",
        "TgOutboxWorker",
    }


def test_infrastructure_packages_expose_only_composition_entry_points() -> None:
    assert _package_exports(PROJECT_ROOT / "db_asyncpg" / "adapters" / "__init__.py") == {
        "ActCounterLedgerRepositoryAdapter",
        "ClientWalletScheduleContextAdapter",
        "ClientWalletTransactionRepositoryAdapter",
        "DealSourceRecordReaderAdapter",
        "DealSourceRepositoryAdapter",
        "ManagedClientWalletTransactionRepositoryAdapter",
    }
    assert _package_exports(PROJECT_ROOT / "db_asyncpg" / "repositories" / "__init__.py") == {
        "ActCounterRepo",
        "ClientsRepo",
        "ExchangeRequestsRepo",
        "FirmPositionsRepo",
        "LiveMessagesRepo",
        "ManagersRepo",
        "PaymentWatchRepo",
        "RateOrdersRepo",
        "RequestScheduleRepo",
        "SettingsRepo",
        "TransactionsRepo",
    }
    assert _package_exports(PROJECT_ROOT / "api" / "queries" / "__init__.py") == {
        "BalanceQueryService",
        "ClientNotFoundError",
        "ClientQueryService",
        "DashboardQueryService",
    }
    assert _package_exports(PROJECT_ROOT / "api" / "read_repositories" / "__init__.py") == {
        "BalanceReadRepository",
        "ClientReadRepository",
        "DashboardReadRepository",
    }


def test_domain_and_transport_packages_expose_only_cross_context_types() -> None:
    assert _package_exports(PROJECT_ROOT / "domain" / "__init__.py") == {
        "FIRM_POSITION_CURRENCIES",
        "CashDealBody",
        "CashRequestKind",
        "CityCode",
        "CurrencyCode",
        "Deal",
        "DealBody",
        "DealEventPayload",
        "DealSource",
        "DealStatus",
        "DealTransitionPolicy",
        "DealType",
        "DomainStateError",
        "DomainValidationError",
        "ExchangeDealBody",
        "ExchangeRequestSource",
        "ExchangeRequestStatus",
        "FirmPosition",
        "FirmPositionMove",
        "FirmPositionMoveKind",
        "InvalidDealTransitionError",
        "Money",
        "NewFirmPositionMove",
        "ScheduleEntry",
        "SourceKind",
        "TelegramMessageRef",
        "firm_position_currency",
    }
    assert _package_exports(PROJECT_ROOT / "handlers" / "__init__.py") == {
        "AMLHandler",
        "AcceptShortHandler",
        "ActHandler",
        "AdminRequestHandler",
        "BroadcastAllHandler",
        "CalcHandler",
        "CashRequestsHandler",
        "CityAssignHandler",
        "ClientsBalancesHandler",
        "ClientsHandler",
        "GrinexBookHandler",
        "ManagersHandler",
        "NonZeroHandler",
        "OfficeCardsHandler",
        "PaymentWatchHandler",
        "RateOrderHandler",
        "StartHandler",
        "UsdtWalletHandler",
        "WalletsHandler",
        "XEHandler",
        "debug_router",
        "get_table_delete_router",
        "get_table_done_router",
    }
    assert _package_exports(PROJECT_ROOT / "telegram_adapters" / "__init__.py") == {
        "AiogramBroadcastDelivery",
        "AiogramBroadcastPresenter",
        "AiogramBroadcastSessionStore",
        "AiogramCallbackReplier",
        "AiogramCashKeyboardPresenter",
        "AiogramExchangeKeyboardPresenter",
        "AiogramMessageReplier",
        "AiogramMessenger",
        "AiogramOrderbookLiveMessageEditor",
        "AiogramPaymentWatchNotifier",
        "AiogramPaymentWatchPresenter",
        "AiogramRequestTableKeyboardPresenter",
        "AiogramWalletKeyboardPresenter",
        "ChatLockRegistry",
        "CityCashMediaStore",
        "request_table_callback_command",
    }


def test_legacy_sheets_gateway_is_removed() -> None:
    assert not LEGACY_SHEETS_GATEWAY.exists()


def test_production_imports_repository_ports_from_bounded_contexts() -> None:
    violations: list[str] = []
    roots = (
        PROJECT_ROOT / "api",
        PROJECT_ROOT / "app",
        PROJECT_ROOT / "db_asyncpg",
        HANDLERS_ROOT,
        SERVICES_ROOT,
        PROJECT_ROOT / "utils",
    )
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "db_asyncpg.ports":
                    violations.append(f"{_relative(path)}:{node.lineno}")

    assert violations == [], (
        f"Production code must import ports from their bounded-context modules: {violations}"
    )


def test_application_code_does_not_import_sync_google_sheets_gateway() -> None:
    violations: list[str] = []
    for root in (SERVICES_ROOT, PROJECT_ROOT / "api"):
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "gutils.requests_sheet_gateway"
                ):
                    violations.append(f"{_relative(path)}:{node.lineno}")

    assert violations == [], (
        "Application code must depend on AsyncSheetsTradeGateway; "
        f"concrete sync adapter belongs to composition root: {violations}"
    )


def test_aml_queue_depends_on_async_checker_instead_of_thread_primitives() -> None:
    queue_module = SERVICES_ROOT / "aml" / "aml_queue_service.py"
    source = queue_module.read_text(encoding="utf-8")

    assert "asyncio.to_thread" not in source
    assert "AMLService" not in source
    assert "AsyncAMLChecker" in source


def test_application_entrypoints_use_structured_logging_configuration() -> None:
    for path in (PROJECT_ROOT / "bot_app.py", PROJECT_ROOT / "api" / "run.py"):
        source = path.read_text(encoding="utf-8")
        assert "configure_logging()" in source
        assert "logging.basicConfig" not in source


def test_pool_module_has_no_global_service_locator() -> None:
    tree = ast.parse(POOL_MODULE.read_text(encoding="utf-8"), filename=str(POOL_MODULE))
    function_names = {
        node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    global_pool_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and target.id == "_pool"
    }

    assert "get_pool" not in function_names
    assert global_pool_assignments == set()


def test_extracted_api_contexts_do_not_return_to_monoliths() -> None:
    forbidden_definitions = {
        "BalanceClientDto",
        "BalancesSnapshotResponse",
        "ClientDto",
        "ClientsPageResponse",
        "ClientTransactionsResponse",
        "DealCreateRequest",
        "DealDetailsResponse",
        "DealItemDto",
        "DealSourceEditRequest",
        "DealsPageResponse",
        "DashboardResponse",
        "build_balances_snapshot",
        "build_client",
        "build_clients_page",
        "build_deal_details",
        "build_deals_page",
        "build_dashboard",
        "build_transactions",
    }
    violations: list[str] = []
    for path in API_MONOLITH_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in forbidden_definitions
            ):
                violations.append(f"{_relative(path)}:{node.lineno}:{node.name}")

    assert violations == []
    assert not LEGACY_API_PRESENTER.exists()
    assert not LEGACY_API_READ_REPOSITORY.exists()


def test_read_routers_depend_on_queries_not_repositories_or_presenters() -> None:
    forbidden_modules = {"api.presentation", "api.read_repositories"}
    violations: list[str] = []
    for path in API_READ_ROUTERS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if any(
                node.module == module or node.module.startswith(f"{module}.")
                for module in forbidden_modules
            ):
                violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")

    assert violations == []


def test_api_routers_do_not_remap_centralized_exceptions() -> None:
    violations: list[str] = []
    for path in _python_files(API_ROUTERS_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            caught_names = {
                child.id for child in ast.walk(node.type) if isinstance(child, ast.Name)
            }
            mapped_names = caught_names & CENTRALLY_MAPPED_API_EXCEPTIONS
            for exception_name in sorted(mapped_names):
                violations.append(f"{_relative(path)}:{node.lineno}:{exception_name}")

    assert violations == []


def test_exchange_workflows_do_not_use_in_memory_request_index() -> None:
    roots = (SERVICES_ROOT / "exchange", SERVICES_ROOT / "request_table", HANDLERS_ROOT)
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "utils.req_index":
                    violations.append(f"{_relative(path)}:{node.lineno}")

    assert violations == []
    assert not LEGACY_REQUEST_INDEX.exists()


def test_operation_state_does_not_use_legacy_process_global_registries() -> None:
    forbidden_modules = {"utils.locks", "utils.undos"}
    roots = (SERVICES_ROOT, HANDLERS_ROOT, PROJECT_ROOT / "app")
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")

    assert violations == []
    assert not any(path.exists() for path in LEGACY_PROCESS_GLOBALS)


def test_cash_request_helpers_belong_to_cash_context() -> None:
    forbidden_modules = {
        "utils.request_audit",
        "utils.request_cards",
        "utils.request_parsing",
        "utils.request_text_parser",
    }
    roots = (PROJECT_ROOT / "app", HANDLERS_ROOT, SERVICES_ROOT)
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")

    assert violations == []
    assert not any(path.exists() for path in LEGACY_CASH_REQUEST_UTILS)


def test_telegram_helpers_belong_to_transport_adapters() -> None:
    forbidden_modules = {
        "utils.auth",
        "utils.errors",
        "utils.info",
        "telegram_adapters.message_actor",
    }
    roots = (PROJECT_ROOT / "app", HANDLERS_ROOT, SERVICES_ROOT, PROJECT_ROOT / "utils")
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")

    assert violations == []
    assert not any(path.exists() for path in LEGACY_TELEGRAM_UTILS)


def test_number_formatting_has_one_application_owner() -> None:
    forbidden_modules = {
        "utils.formatting",
        "services.exchange.rate_formatting",
    }
    roots = (PROJECT_ROOT / "app", HANDLERS_ROOT, SERVICES_ROOT, PROJECT_ROOT / "utils")
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")

    assert violations == []
    assert not any(path.exists() for path in LEGACY_NUMBER_FORMATTERS)


def test_cash_request_compatibility_wrappers_are_removed() -> None:
    forbidden_modules = {
        "services.cash_requests.legacy_request_messages",
        "services.cash_requests.legacy_request_parsing",
    }
    violations: list[str] = []
    for root in (PROJECT_ROOT / "app", HANDLERS_ROOT, SERVICES_ROOT):
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                    violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")

    assert violations == []
    assert not any(path.exists() for path in LEGACY_CASH_REQUEST_WRAPPERS)


def test_legacy_utils_package_has_no_python_modules_or_imports() -> None:
    roots = (
        PROJECT_ROOT / "api",
        PROJECT_ROOT / "app",
        HANDLERS_ROOT,
        SERVICES_ROOT,
        PROJECT_ROOT / "telegram_adapters",
        PROJECT_ROOT / "tests",
    )
    violations: list[str] = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "utils" or node.module.startswith("utils."):
                        violations.append(f"{_relative(path)}:{node.lineno}:{node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "utils" or alias.name.startswith("utils."):
                            violations.append(f"{_relative(path)}:{node.lineno}:{alias.name}")

    assert list(UTILS_ROOT.glob("*.py")) == []
    assert violations == []


def test_statement_xlsx_generation_runs_outside_event_loop() -> None:
    statements_module = PROJECT_ROOT / "telegram_adapters" / "statements.py"
    source = statements_module.read_text(encoding="utf-8")

    assert "await asyncio.to_thread(" in source


def test_exchange_orchestrators_delegate_atomic_persistence() -> None:
    forbidden_fragments = (
        "unit_of_work_factory",
        "balance_service.apply_",
        "upsert_exchange_request_link",
        "set_exchange_request_status",
    )
    violations = {
        _relative(path): [
            fragment
            for fragment in forbidden_fragments
            if fragment in path.read_text(encoding="utf-8")
        ]
        for path in EXCHANGE_ORCHESTRATORS
    }

    assert violations == {path: [] for path in map(_relative, EXCHANGE_ORCHESTRATORS)}


def test_cash_orchestrators_delegate_parsing_cards_and_schedule_persistence() -> None:
    forbidden_fragments = (
        "evaluate(",
        "build_city_card_",
        "build_client_card_",
        "parse_dep_wd_snapshot",
        "parse_fx_snapshot",
        "schedule_service.upsert_entry",
    )
    violations = {
        _relative(path): [
            fragment
            for fragment in forbidden_fragments
            if fragment in path.read_text(encoding="utf-8")
        ]
        for path in CASH_ORCHESTRATORS
    }

    assert violations == {path: [] for path in map(_relative, CASH_ORCHESTRATORS)}


def test_crm_source_mutation_dispatches_without_source_specific_branches() -> None:
    source = CRM_SOURCE_MUTATION.read_text(encoding="utf-8")
    forbidden_fragments = (
        "ExchangeDealBody",
        "CashDealBody",
        "apply_edit_delta",
        "exchange_requests.",
        "request_schedule.",
        "if source_kind",
        "elif source_kind",
    )

    assert [fragment for fragment in forbidden_fragments if fragment in source] == []
