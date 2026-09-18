"""Validate or apply a normalized accounting JSON manifest.

The default mode is read-only. Pass --apply explicitly after reviewing checksum
and control totals. Source-specific XLSX extraction stays outside the accounting
service and only has to produce this stable manifest contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_asyncpg.pool import close_pool, create_pool  # noqa: E402
from db_asyncpg.uow import AsyncpgUnitOfWork  # noqa: E402
from services.accounting.firm_position_service import (  # noqa: E402
    FirmPositionAccountingService,
)
from services.accounting.import_models import AccountingImportManifest  # noqa: E402
from services.accounting.import_service import AccountingImportService  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write validated records")
    parser.add_argument("--expect-checksum", help="Abort if the manifest checksum differs")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest = AccountingImportManifest.from_dict(value)
    if args.expect_checksum and args.expect_checksum != manifest.checksum:
        raise SystemExit("Manifest checksum differs from --expect-checksum")
    if args.apply and not args.expect_checksum:
        raise SystemExit("--apply requires the reviewed --expect-checksum value")
    if not args.apply:
        def unavailable_unit_of_work():
            raise RuntimeError("Dry-run must not open a database unit of work")

        result = await AccountingImportService(
            unavailable_unit_of_work,
            position_service=FirmPositionAccountingService(unavailable_unit_of_work),
        ).run(manifest, dry_run=True)
        _print_result(result)
        return

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    pool = await create_pool(database_url)
    try:
        def unit_of_work_factory():
            return AsyncpgUnitOfWork(pool)

        result = await AccountingImportService(
            unit_of_work_factory,
            position_service=FirmPositionAccountingService(unit_of_work_factory),
        ).run(manifest)
        _print_result(result)
    finally:
        await close_pool(pool)


def _print_result(result) -> None:
    print(
        json.dumps(
            {
                "dryRun": result.dry_run,
                "status": result.status,
                "manifestChecksum": result.manifest_checksum,
                "recordCount": result.record_count,
                "appliedCount": result.applied_count,
                "repeatedCount": result.repeated_count,
                "checkpoint": result.checkpoint,
                "controlTotals": {
                    key: str(amount) for key, amount in result.control_totals.items()
                },
                "runId": result.run_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
