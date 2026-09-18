from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from domain import DomainValidationError

_CURRENCY_ALIASES = {"USD": "USD_BL", "USDW": "USD_WH"}


class ImportEntityKind(StrEnum):
    DEAL = "deal"
    CASH_REGISTRY = "cash_registry"
    CASH_BALANCE = "cash_balance"
    CLIENT_BALANCE = "client_balance"
    INTERNAL_BALANCE = "internal_balance"
    CAPITAL = "capital"
    EXPENSE = "expense"
    POSITION_OPENING = "position_opening"
    POSITION_MOVE = "position_move"
    WALLET_FACT = "wallet_fact"
    PROFIT_ACCRUAL = "profit_accrual"


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountingImportRecord:
    key: str
    kind: ImportEntityKind
    payload: Mapping[str, Any]

    @property
    def checksum(self) -> str:
        return _checksum({"key": self.key, "kind": self.kind.value, "payload": self.payload})


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountingImportManifest:
    source_name: str
    cutover_at: datetime
    position_strategy: Mapping[str, str]
    records: tuple[AccountingImportRecord, ...]
    expected_totals: Mapping[str, Decimal]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AccountingImportManifest:
        try:
            cutover_at = datetime.fromisoformat(str(value["cutoverAt"]))
            records = tuple(
                AccountingImportRecord(
                    key=str(item["key"]).strip(),
                    kind=ImportEntityKind(str(item["kind"])),
                    payload=dict(item.get("payload") or {}),
                )
                for item in value.get("records", [])
            )
            expected = {
                str(key): Decimal(str(amount))
                for key, amount in dict(value.get("expectedTotals") or {}).items()
            }
        except (KeyError, TypeError, ValueError):
            raise DomainValidationError("Invalid accounting import manifest") from None
        return cls(
            source_name=str(value.get("sourceName") or "").strip(),
            cutover_at=cutover_at,
            position_strategy={
                _CURRENCY_ALIASES.get(
                    str(key).strip().upper(), str(key).strip().upper()
                ): str(strategy).strip().lower()
                for key, strategy in dict(value.get("positionStrategy") or {}).items()
            },
            records=records,
            expected_totals=expected,
        )

    @property
    def checksum(self) -> str:
        return _checksum(
            {
                "sourceName": self.source_name,
                "cutoverAt": self.cutover_at.isoformat(),
                "positionStrategy": self.position_strategy,
                "records": [
                    {"key": row.key, "kind": row.kind.value, "payload": row.payload}
                    for row in self.records
                ],
                "expectedTotals": self.expected_totals,
            }
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportRunState:
    id: int
    status: str
    checkpoint: int
    record_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportedRecord:
    payload_checksum: str
    target_table: str
    target_id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountingImportResult:
    dry_run: bool
    status: str
    manifest_checksum: str
    record_count: int
    applied_count: int
    repeated_count: int
    checkpoint: int
    control_totals: Mapping[str, Decimal]
    run_id: int | None = None


def _checksum(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
