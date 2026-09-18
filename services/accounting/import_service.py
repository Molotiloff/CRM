from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from domain import DealType, DomainStateError, DomainValidationError
from services.unit_of_work import UnitOfWorkFactory, UnitOfWorkPort

from .firm_position_service import FirmPositionAccountingService
from .import_models import (
    AccountingImportManifest,
    AccountingImportRecord,
    AccountingImportResult,
    ImportEntityKind,
)
from .models import (
    RecordAdjustment,
    RecordOpening,
    RecordPurchase,
    RecordSale,
    WalletFactSource,
)

_CURRENCY_ALIASES = {"USD": "USD_BL", "USDW": "USD_WH"}
_EMPLOYEE_CLIENT_BALANCE_NAMES = frozenset(
    {
        "баланс вв",
        "баланс никита",
        "баланс влад",
        "баланс лев",
        "баланс ваня support",
        "баланс монах",
        "баланс миша",
        "баланс саша члб",
        "баланса мэтью",
        "баланс тенаклиус",
    }
)
_LEGACY_EMPLOYEE_INTERNAL_SOURCES = frozenset(
    {
        # Already applied and retained verbatim so its reviewed checksum remains
        # reproducible. Migrations 0027-0028 reverse the resulting duplicates.
        "skyex-main-2026-09-07-20-40-10",
    }
)


class AccountingImportService:
    """Resumable normalized accounting import; source adapters only build manifests."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        position_service: FirmPositionAccountingService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._positions = position_service

    async def run(
        self,
        manifest: AccountingImportManifest,
        *,
        dry_run: bool = False,
    ) -> AccountingImportResult:
        totals = self._validate(manifest)
        checksum = manifest.checksum
        if dry_run:
            return AccountingImportResult(
                dry_run=True,
                status="validated",
                manifest_checksum=checksum,
                record_count=len(manifest.records),
                applied_count=0,
                repeated_count=0,
                checkpoint=0,
                control_totals=totals,
            )

        run = await self._start(manifest, totals)
        if run.status == "completed":
            return AccountingImportResult(
                dry_run=False,
                status="completed",
                manifest_checksum=checksum,
                record_count=len(manifest.records),
                applied_count=0,
                repeated_count=len(manifest.records),
                checkpoint=run.checkpoint,
                control_totals=totals,
                run_id=run.id,
            )

        applied = 0
        repeated = 0
        try:
            for sequence_no, record in enumerate(manifest.records, start=1):
                was_applied = await self._apply_one(
                    manifest,
                    run_id=run.id,
                    sequence_no=sequence_no,
                    record=record,
                )
                applied += int(was_applied)
                repeated += int(not was_applied)
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.accounting_imports.complete(run.id)
                await unit_of_work.commit()
        except Exception as exc:
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.accounting_imports.fail(
                    run.id,
                    error_kind=type(exc).__name__,
                )
                await unit_of_work.commit()
            raise
        return AccountingImportResult(
            dry_run=False,
            status="completed",
            manifest_checksum=checksum,
            record_count=len(manifest.records),
            applied_count=applied,
            repeated_count=repeated,
            checkpoint=len(manifest.records),
            control_totals=totals,
            run_id=run.id,
        )

    async def _start(self, manifest, totals):
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.accounting_imports
            await repository.acquire_source_lock(manifest.source_name)
            run = await repository.start_or_resume(
                source_name=manifest.source_name,
                manifest_checksum=manifest.checksum,
                record_count=len(manifest.records),
                strategy=dict(manifest.position_strategy),
                control_totals={key: str(value) for key, value in totals.items()},
            )
            await unit_of_work.commit()
            return run

    async def _apply_one(
        self,
        manifest: AccountingImportManifest,
        *,
        run_id: int,
        sequence_no: int,
        record: AccountingImportRecord,
    ) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.accounting_imports
            await repository.acquire_source_lock(manifest.source_name)
            existing = await repository.imported_record(
                source_name=manifest.source_name,
                entity_kind=record.kind.value,
                source_key=record.key,
            )
            if existing is not None:
                if existing.payload_checksum != record.checksum:
                    raise DomainStateError(
                        f"Imported source record {record.kind.value}:{record.key} changed"
                    )
                await repository.advance_checkpoint(run_id, sequence_no=sequence_no)
                await unit_of_work.commit()
                return False
            target_table, target_id = await self._apply_record(
                unit_of_work,
                manifest=manifest,
                record=record,
            )
            await repository.record_applied(
                run_id=run_id,
                source_name=manifest.source_name,
                entity_kind=record.kind.value,
                source_key=record.key,
                payload_checksum=record.checksum,
                sequence_no=sequence_no,
                target_table=target_table,
                target_id=target_id,
            )
            await unit_of_work.commit()
            return True

    async def _apply_record(
        self,
        unit_of_work: UnitOfWorkPort,
        *,
        manifest: AccountingImportManifest,
        record: AccountingImportRecord,
    ) -> tuple[str, int]:
        payload = record.payload
        key = f"import:{manifest.source_name}:{record.kind.value}:{record.key}"
        repository = unit_of_work.accounting_imports
        if record.kind is ImportEntityKind.DEAL:
            target_id = await repository.deal(
                deal_type=self._text(payload, "dealType"),
                city=self._text(payload, "city"),
                deal_at=self._date(payload, "date"),
                profit=self._decimal(payload, "profit"),
                body=self._mapping(payload, "body"),
                comment=str(payload["comment"]) if payload.get("comment") else None,
                source_ref=f"{manifest.source_name}:{record.key}",
            )
            return "deals", target_id
        if record.kind is ImportEntityKind.CASH_REGISTRY:
            target_id = await repository.cash_registry(
                chat_id=self._integer(payload, "chatId"),
                city=self._text(payload, "city"),
                location_name=self._text(payload, "locationName"),
            )
            return "cash_chat_registry", target_id
        if record.kind in {ImportEntityKind.CASH_BALANCE, ImportEntityKind.CLIENT_BALANCE}:
            client_id = await repository.resolve_client_id(self._integer(payload, "chatId"))
            amount = self._decimal(payload, "amount")
            currency = self._currency(payload)
            method = unit_of_work.transactions.deposit if amount >= 0 else unit_of_work.transactions.withdraw
            target_id = await method(
                client_id=client_id,
                currency_code=currency,
                amount=abs(amount),
                comment=f"Accounting import opening {record.kind.value}",
                source="import",
                txn_at=manifest.cutover_at,
                idempotency_key=key,
            )
            return "transactions", target_id
        if record.kind is ImportEntityKind.INTERNAL_BALANCE:
            currency = self._currency(payload, default="RUB")
            target_id = await repository.internal_balance(
                name=self._text(payload, "name"),
                kind=str(payload.get("accountKind") or "tech"),
                currency=currency,
                amount=self._decimal(payload, "amount"),
                idempotency_key=key,
            )
            return "internal_account_moves", target_id
        if record.kind is ImportEntityKind.CAPITAL:
            target_id = await repository.capital(
                owner=self._text(payload, "owner"),
                amount=self._decimal(payload, "amount"),
                move_at=self._date(payload, "date"),
                monthly_rate=(
                    self._decimal(payload, "monthlyRate")
                    if payload.get("monthlyRate") is not None
                    else None
                ),
                idempotency_key=key,
            )
            return "capital_moves", target_id
        if record.kind is ImportEntityKind.EXPENSE:
            target_id = await repository.expense(
                kind=str(payload.get("expenseKind") or "variable"),
                category=self._text(payload, "category"),
                city=str(payload["city"]) if payload.get("city") else None,
                amount=self._decimal(payload, "amount"),
                expense_at=self._date(payload, "date"),
                comment=str(payload["comment"]) if payload.get("comment") else None,
                idempotency_key=key,
            )
            return "expenses", target_id
        if record.kind in {ImportEntityKind.POSITION_OPENING, ImportEntityKind.POSITION_MOVE}:
            move = await self._position_record(
                unit_of_work,
                manifest=manifest,
                record=record,
                idempotency_key=key,
            )
            return "firm_position_moves", move.id
        if record.kind is ImportEntityKind.WALLET_FACT:
            currency = self._currency(payload)
            observed_at = self._datetime(payload.get("observedAt") or manifest.cutover_at)
            await unit_of_work.wallet_facts.acquire_fact_lock(currency)
            snapshot = await unit_of_work.wallet_facts.append_snapshot(
                currency=currency,
                actual_qty=self._decimal(payload, "qty"),
                observed_at=observed_at,
                source=WalletFactSource.IMPORT,
                address_id=None,
                actor_user_id=None,
                comment="Accounting import",
                idempotency_key=key,
            )
            return "firm_wallet_fact_snapshots", snapshot.id
        if record.kind is ImportEntityKind.PROFIT_ACCRUAL:
            accrual = await unit_of_work.profit_accruals.append(
                deal_id=await self._profit_deal_id(
                    unit_of_work, manifest=manifest, payload=payload
                ),
                qty=self._decimal(payload, "qty"),
                settlement_status=str(payload.get("settlementStatus") or "in_transit"),
                idempotency_key=key,
                received_at=(
                    manifest.cutover_at
                    if str(payload.get("settlementStatus") or "in_transit") == "received"
                    else None
                ),
            )
            return "profit_usdt_accruals", accrual.id
        raise DomainValidationError(f"Unsupported import entity: {record.kind.value}")

    async def _profit_deal_id(self, unit_of_work, *, manifest, payload) -> int:
        if payload.get("dealId") is not None:
            return self._integer(payload, "dealId")
        deal_key = self._text(payload, "dealKey")
        imported = await unit_of_work.accounting_imports.imported_record(
            source_name=manifest.source_name,
            entity_kind=ImportEntityKind.DEAL.value,
            source_key=deal_key,
        )
        if imported is None or imported.target_table != "deals":
            raise DomainValidationError(f"Imported profit deal was not found: {deal_key}")
        return imported.target_id

    async def _position_record(self, unit_of_work, *, manifest, record, idempotency_key):
        payload = record.payload
        currency = self._currency(payload)
        effective_at = self._datetime(payload.get("effectiveAt") or manifest.cutover_at)
        if record.kind is ImportEntityKind.POSITION_OPENING:
            return await self._positions.record_opening(
                RecordOpening(
                    currency=currency,
                    qty=self._decimal(payload, "qty"),
                    rub_cost=self._decimal(payload, "rubCost"),
                    reason=self._text(payload, "reason", default="Approved import opening"),
                    effective_at=effective_at,
                    idempotency_key=idempotency_key,
                ),
                unit_of_work=unit_of_work,
            )
        move_kind = str(payload.get("moveKind") or "").strip().lower()
        common = {
            "currency": currency,
            "effective_at": effective_at,
            "idempotency_key": idempotency_key,
        }
        if move_kind == "purchase":
            return await self._positions.record_purchase(
                RecordPurchase(
                    **common,
                    qty=self._decimal(payload, "qty"),
                    rate=self._decimal(payload, "rate"),
                ),
                unit_of_work=unit_of_work,
            )
        if move_kind == "sale":
            return await self._positions.record_sale(
                RecordSale(**common, qty=self._decimal(payload, "qty")),
                unit_of_work=unit_of_work,
            )
        if move_kind == "adjust":
            return await self._positions.record_adjustment(
                RecordAdjustment(
                    **common,
                    qty_delta=self._decimal(payload, "qty"),
                    rub_cost_delta=self._decimal(payload, "rubCost"),
                    reason=self._text(payload, "reason"),
                ),
                unit_of_work=unit_of_work,
            )
        raise DomainValidationError(f"Unsupported imported position move: {move_kind}")

    def _validate(self, manifest: AccountingImportManifest) -> dict[str, Decimal]:
        if not manifest.source_name:
            raise DomainValidationError("Import source name is required")
        if manifest.cutover_at.tzinfo is None:
            raise DomainValidationError("Import cutover time must be timezone-aware")
        keys = [(record.kind, record.key) for record in manifest.records]
        if any(not key for _, key in keys) or len(keys) != len(set(keys)):
            raise DomainValidationError("Import record keys must be nonempty and unique")
        for record in manifest.records:
            self._validate_record_payload(
                record,
                cutover_at=manifest.cutover_at,
                source_name=manifest.source_name,
            )
        self._validate_profit_deal_references(manifest)
        self._validate_position_strategy(manifest)
        totals = self._control_totals(manifest)
        for key, expected in manifest.expected_totals.items():
            actual = totals.get(key)
            tolerance = Decimal("0.00000001") if key.endswith(".qty") else Decimal("0.01")
            if actual is None or expected is None or abs(actual - expected) > tolerance:
                raise DomainValidationError(f"Import control total mismatch: {key}")
        return totals

    def _validate_record_payload(
        self,
        record: AccountingImportRecord,
        *,
        cutover_at: datetime,
        source_name: str,
    ) -> None:
        payload = record.payload
        if record.kind is ImportEntityKind.DEAL:
            try:
                DealType(self._text(payload, "dealType"))
            except ValueError:
                raise DomainValidationError("Invalid imported deal type") from None
            self._text(payload, "city")
            self._date(payload, "date")
            self._decimal(payload, "profit")
            body = self._mapping(payload, "body")
            if body.get("rub_cost") is not None:
                self._decimal(body, "rub_cost")
            return
        if record.kind is ImportEntityKind.CASH_REGISTRY:
            self._integer(payload, "chatId")
            self._text(payload, "city")
            self._text(payload, "locationName")
            return
        if record.kind in {ImportEntityKind.CASH_BALANCE, ImportEntityKind.CLIENT_BALANCE}:
            self._integer(payload, "chatId")
            self._currency(payload)
            self._decimal(payload, "amount")
            return
        if record.kind is ImportEntityKind.INTERNAL_BALANCE:
            name = self._text(payload, "name")
            currency = self._currency(payload, default="RUB")
            if (
                currency == "RUB"
                and self._normalized_name(name) in _EMPLOYEE_CLIENT_BALANCE_NAMES
                and source_name not in _LEGACY_EMPLOYEE_INTERNAL_SOURCES
            ):
                raise DomainValidationError(
                    "Employee RUB balance must be imported through client_accounts"
                )
            account_kind = str(payload.get("accountKind") or "tech")
            if account_kind not in {
                "employee",
                "partner",
                "owner_pledge",
                "tech",
            }:
                raise DomainValidationError("Invalid imported internal account kind")
            if currency != "RUB" and account_kind != "tech":
                raise DomainValidationError(
                    "Foreign-currency internal account must be a technical adjustment"
                )
            self._decimal(payload, "amount")
            return
        if record.kind is ImportEntityKind.CAPITAL:
            self._text(payload, "owner")
            self._decimal(payload, "amount")
            self._date(payload, "date")
            if payload.get("monthlyRate") is not None:
                self._decimal(payload, "monthlyRate")
            return
        if record.kind is ImportEntityKind.EXPENSE:
            if str(payload.get("expenseKind") or "variable") not in {"fixed", "variable"}:
                raise DomainValidationError("Invalid imported expense kind")
            self._text(payload, "category")
            self._decimal(payload, "amount")
            self._date(payload, "date")
            return
        if record.kind in {ImportEntityKind.POSITION_OPENING, ImportEntityKind.POSITION_MOVE}:
            self._currency(payload)
            self._datetime(payload.get("effectiveAt") or cutover_at)
            qty = self._decimal(payload, "qty")
            if record.kind is ImportEntityKind.POSITION_OPENING:
                if qty < 0 or self._decimal(payload, "rubCost") < 0:
                    raise DomainValidationError("Imported position opening must not be negative")
                return
            move_kind = str(payload.get("moveKind") or "").strip().lower()
            if move_kind in {"purchase", "sale"} and qty <= 0:
                raise DomainValidationError("Imported position movement must be positive")
            if move_kind == "purchase" and self._decimal(payload, "rate") <= 0:
                raise DomainValidationError("Imported purchase rate must be positive")
            if move_kind == "adjust":
                self._decimal(payload, "rubCost")
                self._text(payload, "reason")
            elif move_kind not in {"purchase", "sale"}:
                raise DomainValidationError(
                    f"Unsupported imported position move: {move_kind}"
                )
            return
        if record.kind is ImportEntityKind.WALLET_FACT:
            self._currency(payload)
            if self._decimal(payload, "qty") < 0:
                raise DomainValidationError("Imported wallet fact must not be negative")
            self._datetime(payload.get("observedAt") or cutover_at)
            return
        if record.kind is ImportEntityKind.PROFIT_ACCRUAL:
            has_id = payload.get("dealId") is not None
            has_key = bool(str(payload.get("dealKey") or "").strip())
            if has_id == has_key:
                raise DomainValidationError(
                    "Imported profit accrual requires exactly one deal reference"
                )
            if has_id:
                self._integer(payload, "dealId")
            if self._decimal(payload, "qty") <= 0:
                raise DomainValidationError("Imported profit accrual must be positive")
            if str(payload.get("settlementStatus") or "in_transit") not in {
                "in_transit",
                "received",
            }:
                raise DomainValidationError("Invalid imported profit settlement status")

    @staticmethod
    def _normalized_name(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _validate_profit_deal_references(manifest: AccountingImportManifest) -> None:
        prior_deals: set[str] = set()
        for record in manifest.records:
            if record.kind is ImportEntityKind.DEAL:
                prior_deals.add(record.key)
                continue
            if record.kind is not ImportEntityKind.PROFIT_ACCRUAL:
                continue
            deal_key = str(record.payload.get("dealKey") or "").strip()
            if deal_key and deal_key not in prior_deals:
                raise DomainValidationError(
                    f"Imported profit deal must precede its accrual: {deal_key}"
                )

    def _validate_position_strategy(self, manifest: AccountingImportManifest) -> None:
        latest: dict[str, datetime] = {}
        openings: set[str] = set()
        for record in manifest.records:
            if record.kind not in {
                ImportEntityKind.POSITION_OPENING,
                ImportEntityKind.POSITION_MOVE,
            }:
                continue
            currency = self._currency(record.payload)
            strategy = manifest.position_strategy.get(str(currency))
            expected = "opening" if record.kind is ImportEntityKind.POSITION_OPENING else "history"
            if strategy != expected:
                raise DomainValidationError(
                    f"Position {currency} mixes or omits import strategy"
                )
            effective = self._datetime(
                record.payload.get("effectiveAt") or manifest.cutover_at
            )
            if effective < latest.get(str(currency), effective):
                raise DomainValidationError(f"Position history for {currency} is not chronological")
            latest[str(currency)] = effective
            if record.kind is ImportEntityKind.POSITION_OPENING:
                if str(currency) in openings:
                    raise DomainValidationError(f"Position {currency} has multiple openings")
                openings.add(str(currency))

    def _control_totals(self, manifest) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for record in manifest.records:
            payload = record.payload
            if record.kind is ImportEntityKind.CASH_REGISTRY:
                continue
            if record.kind is ImportEntityKind.DEAL:
                profit_key = "deal.RUB.profit"
                totals[profit_key] = totals.get(profit_key, Decimal(0)) + self._decimal(
                    payload, "profit"
                )
                body = self._mapping(payload, "body")
                rub_cost = body.get("rub_cost")
                if self._text(payload, "dealType") == DealType.SALE and rub_cost is not None:
                    cost_key = "deal.RUB.rub_cost"
                    totals[cost_key] = totals.get(cost_key, Decimal(0)) + self._decimal(
                        body, "rub_cost"
                    )
                continue
            currency = (
                self._currency(
                    payload,
                    default=(
                        "RUB"
                        if record.kind is ImportEntityKind.INTERNAL_BALANCE
                        else ""
                    ),
                )
                if record.kind
                in {
                    ImportEntityKind.CASH_BALANCE,
                    ImportEntityKind.CLIENT_BALANCE,
                    ImportEntityKind.INTERNAL_BALANCE,
                    ImportEntityKind.POSITION_OPENING,
                    ImportEntityKind.POSITION_MOVE,
                    ImportEntityKind.WALLET_FACT,
                }
                else "USDT" if record.kind is ImportEntityKind.PROFIT_ACCRUAL else "RUB"
            )
            field = "qty" if record.kind in {
                ImportEntityKind.POSITION_OPENING,
                ImportEntityKind.POSITION_MOVE,
                ImportEntityKind.WALLET_FACT,
                ImportEntityKind.PROFIT_ACCRUAL,
            } else "amount"
            value_field = "qty" if field == "qty" else "amount"
            key = f"{record.kind.value}.{currency}.{field}"
            totals[key] = totals.get(key, Decimal(0)) + self._decimal(payload, value_field)
            if record.kind is ImportEntityKind.POSITION_OPENING:
                rub_key = f"{record.kind.value}.{currency}.rub"
                totals[rub_key] = totals.get(rub_key, Decimal(0)) + self._decimal(
                    payload, "rubCost"
                )
        return totals

    @staticmethod
    def _currency(payload, *, default: str = "") -> str:
        raw = str(payload.get("currency") or default).strip().upper()
        normalized = _CURRENCY_ALIASES.get(raw, raw)
        if normalized not in {"RUB", "EUR", "USDT", "USD_BL", "USD_WH"}:
            raise DomainValidationError(f"Unsupported import currency: {raw!r}")
        return normalized

    @staticmethod
    def _decimal(payload, field: str) -> Decimal:
        try:
            value = Decimal(str(payload[field]))
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise DomainValidationError(f"Invalid import decimal: {field}") from None
        if not value.is_finite():
            raise DomainValidationError(f"Invalid import decimal: {field}")
        return value

    @staticmethod
    def _integer(payload, field: str) -> int:
        try:
            return int(payload[field])
        except (KeyError, TypeError, ValueError):
            raise DomainValidationError(f"Invalid import integer: {field}") from None

    @staticmethod
    def _text(payload, field: str, *, default: str | None = None) -> str:
        value = str(payload.get(field) or default or "").strip()
        if not value:
            raise DomainValidationError(f"Import field is required: {field}")
        return value

    @staticmethod
    def _mapping(payload, field: str) -> Mapping[str, object]:
        value = payload.get(field)
        if not isinstance(value, Mapping):
            raise DomainValidationError(f"Invalid import object: {field}")
        return value

    @staticmethod
    def _date(payload, field: str) -> date:
        try:
            return date.fromisoformat(str(payload[field]))
        except (KeyError, ValueError):
            raise DomainValidationError(f"Invalid import date: {field}") from None

    @staticmethod
    def _datetime(value: Any) -> datetime:
        try:
            result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except ValueError:
            raise DomainValidationError("Invalid import timestamp") from None
        if result.tzinfo is None:
            raise DomainValidationError("Import timestamp must be timezone-aware")
        return result
