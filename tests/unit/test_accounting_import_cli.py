from __future__ import annotations

import argparse
import json

import pytest

from scripts.import_accounting_snapshot import _run


@pytest.mark.asyncio
async def test_import_cli_dry_run_does_not_require_database(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sourceName": "cli-dry-run",
                "cutoverAt": "2026-08-26T00:00:00+05:00",
                "positionStrategy": {},
                "records": [
                    {
                        "key": "expense",
                        "kind": "expense",
                        "payload": {
                            "expenseKind": "variable",
                            "category": "Test",
                            "amount": "10",
                            "date": "2026-08-26",
                        },
                    }
                ],
                "expectedTotals": {"expense.RUB.amount": "10"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    await _run(
        argparse.Namespace(
            manifest=manifest_path,
            apply=False,
            expect_checksum=None,
        )
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "validated"
    assert result["dryRun"] is True
    assert result["controlTotals"] == {"expense.RUB.amount": "10"}


@pytest.mark.asyncio
async def test_import_cli_apply_requires_reviewed_checksum(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sourceName": "cli-apply",
                "cutoverAt": "2026-08-26T00:00:00+05:00",
                "positionStrategy": {},
                "records": [],
                "expectedTotals": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="--apply requires"):
        await _run(
            argparse.Namespace(
                manifest=manifest_path,
                apply=True,
                expect_checksum=None,
            )
        )
