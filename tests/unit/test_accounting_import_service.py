from __future__ import annotations

from decimal import Decimal

import pytest

from domain import DomainValidationError
from services.accounting.import_models import AccountingImportManifest
from services.accounting.import_service import AccountingImportService


@pytest.mark.asyncio
async def test_import_control_totals_use_financial_precision() -> None:
    service = AccountingImportService(
        lambda: (_ for _ in ()).throw(AssertionError("dry-run opened a UoW")),
        position_service=None,  # type: ignore[arg-type]
    )
    accepted = _manifest("10.009")
    result = await service.run(accepted, dry_run=True)
    assert result.control_totals == {"expense.RUB.amount": Decimal("10")}

    with pytest.raises(DomainValidationError, match="control total mismatch"):
        await service.run(_manifest("10.02"), dry_run=True)


@pytest.mark.asyncio
async def test_import_accepts_historical_expense_refund() -> None:
    service = AccountingImportService(
        lambda: (_ for _ in ()).throw(AssertionError("dry-run opened a UoW")),
        position_service=None,  # type: ignore[arg-type]
    )
    manifest = _manifest("-8660", amount="-8660")

    result = await service.run(manifest, dry_run=True)

    assert result.control_totals == {"expense.RUB.amount": Decimal("-8660")}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Баланс\nВаня Support", "Баланса Мэтью"])
async def test_import_rejects_employee_balance_as_internal_account(name: str) -> None:
    service = AccountingImportService(
        lambda: (_ for _ in ()).throw(AssertionError("dry-run opened a UoW")),
        position_service=None,  # type: ignore[arg-type]
    )
    manifest = AccountingImportManifest.from_dict(
        {
            "sourceName": "employee-balance-policy-test",
            "cutoverAt": "2026-09-07T20:40:10+05:00",
            "positionStrategy": {},
            "records": [
                {
                    "key": "internal-rub-b-row-29",
                    "kind": "internal_balance",
                    "payload": {
                        "name": name,
                        "accountKind": "tech",
                        "currency": "RUB",
                        "amount": "-231548",
                    },
                }
            ],
        }
    )

    with pytest.raises(
        DomainValidationError,
        match="Employee RUB balance must be imported through client_accounts",
    ):
        await service.run(manifest, dry_run=True)


@pytest.mark.asyncio
async def test_import_keeps_reviewed_legacy_manifest_reproducible() -> None:
    service = AccountingImportService(
        lambda: (_ for _ in ()).throw(AssertionError("dry-run opened a UoW")),
        position_service=None,  # type: ignore[arg-type]
    )
    manifest = AccountingImportManifest.from_dict(
        {
            "sourceName": "skyex-main-2026-09-07-20-40-10",
            "cutoverAt": "2026-09-07T20:40:10+05:00",
            "positionStrategy": {},
            "records": [
                {
                    "key": "internal-rub-b-row-33",
                    "kind": "internal_balance",
                    "payload": {
                        "name": "Баланса Мэтью",
                        "accountKind": "tech",
                        "currency": "RUB",
                        "amount": "22566",
                    },
                }
            ],
        }
    )

    result = await service.run(manifest, dry_run=True)

    assert result.status == "validated"


@pytest.mark.asyncio
async def test_import_deal_controls_profit_and_sale_rub_cost() -> None:
    service = AccountingImportService(
        lambda: (_ for _ in ()).throw(AssertionError("dry-run opened a UoW")),
        position_service=None,  # type: ignore[arg-type]
    )
    manifest = AccountingImportManifest.from_dict(
        {
            "sourceName": "deal-control-test",
            "cutoverAt": "2026-08-26T00:00:00+05:00",
            "positionStrategy": {},
            "records": [
                {
                    "key": "sale-row-2",
                    "kind": "deal",
                    "payload": {
                        "dealType": "sale",
                        "city": "екб",
                        "date": "2026-08-01",
                        "profit": "25.50",
                        "body": {"rub_cost": "1000", "sale_amount": "1025.50"},
                    },
                },
                {
                    "key": "purchase-row-2",
                    "kind": "deal",
                    "payload": {
                        "dealType": "purchase",
                        "city": "екб",
                        "date": "2026-08-01",
                        "profit": "0",
                        "body": {"rub_cost": "900"},
                    },
                },
            ],
            "expectedTotals": {
                "deal.RUB.profit": "25.50",
                "deal.RUB.rub_cost": "1000",
            },
        }
    )

    result = await service.run(manifest, dry_run=True)

    assert result.control_totals == {
        "deal.RUB.profit": Decimal("25.50"),
        "deal.RUB.rub_cost": Decimal("1000"),
    }


@pytest.mark.asyncio
async def test_import_profit_deal_key_must_precede_accrual() -> None:
    service = AccountingImportService(
        lambda: (_ for _ in ()).throw(AssertionError("dry-run opened a UoW")),
        position_service=None,  # type: ignore[arg-type]
    )
    manifest = AccountingImportManifest.from_dict(
        {
            "sourceName": "deal-reference-test",
            "cutoverAt": "2026-08-26T00:00:00+05:00",
            "positionStrategy": {},
            "records": [
                {
                    "key": "profit-accrual",
                    "kind": "profit_accrual",
                    "payload": {"dealKey": "profit-deal", "qty": "1"},
                },
                {
                    "key": "profit-deal",
                    "kind": "deal",
                    "payload": {
                        "dealType": "profit",
                        "city": "другое",
                        "date": "2026-08-01",
                        "profit": "80",
                        "body": {},
                    },
                },
            ],
        }
    )

    with pytest.raises(DomainValidationError, match="must precede"):
        await service.run(manifest, dry_run=True)


def _manifest(expected: str, *, amount: str = "10") -> AccountingImportManifest:
    return AccountingImportManifest.from_dict(
        {
            "sourceName": "control-total-test",
            "cutoverAt": "2026-08-26T00:00:00+05:00",
            "positionStrategy": {},
            "records": [
                {
                    "key": "expense",
                    "kind": "expense",
                    "payload": {
                        "expenseKind": "variable",
                        "category": "Test",
                        "amount": amount,
                        "date": "2026-08-26",
                    },
                }
            ],
            "expectedTotals": {"expense.RUB.amount": expected},
        }
    )
