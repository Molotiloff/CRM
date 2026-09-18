from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from services.accounting.dashboard_comparison_service import (
    DashboardComparisonReport,
    DashboardDifference,
    DifferenceKind,
    StoredDashboardComparisonReport,
)

from .base import ConnectionBoundRepo


class ShadowComparisonsRepo(ConnectionBoundRepo):
    async def save(
        self,
        *,
        business_date: date,
        primary_source: str,
        report: DashboardComparisonReport,
    ) -> int:
        diagnostics = [
            {
                "path": item.path,
                "sheet": str(item.sheet_value) if item.sheet_value is not None else None,
                "database": str(item.db_value) if item.db_value is not None else None,
                "absoluteDelta": (
                    str(item.absolute_delta) if item.absolute_delta is not None else None
                ),
                "relativeDelta": (
                    str(item.relative_delta) if item.relative_delta is not None else None
                ),
                "classification": item.kind.value,
            }
            for item in report.differences
        ]
        async with self._connection() as connection:
            report_id = await connection.fetchval(
                """
                INSERT INTO dashboard_shadow_reports(
                    business_date, primary_source, status, compared_fields,
                    mismatch_count, absolute_tolerance, relative_tolerance,
                    diagnostics, sheets_data_as_of, db_data_as_of,
                    report_fingerprint
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10,
                    MD5(
                        JSONB_BUILD_OBJECT(
                            'status', $3::text,
                            'comparedFields', $4::integer,
                            'mismatchCount', $5::integer,
                            'absoluteTolerance', $6::numeric,
                            'relativeTolerance', $7::numeric,
                            'diagnostics', $8::jsonb
                        )::TEXT
                    )
                )
                ON CONFLICT (business_date, primary_source, report_fingerprint)
                DO NOTHING
                RETURNING id
                """,
                business_date,
                primary_source,
                report.status,
                report.compared_fields,
                report.mismatch_count,
                report.absolute_tolerance,
                report.relative_tolerance,
                json.dumps(diagnostics),
                report.sheets_data_as_of,
                report.db_data_as_of,
            )
            if report_id is None:
                report_id = await connection.fetchval(
                    """
                    SELECT id
                    FROM dashboard_shadow_reports
                    WHERE business_date = $1
                      AND primary_source = $2
                      AND report_fingerprint = MD5(
                          JSONB_BUILD_OBJECT(
                              'status', $3::text,
                              'comparedFields', $4::integer,
                              'mismatchCount', $5::integer,
                              'absoluteTolerance', $6::numeric,
                              'relativeTolerance', $7::numeric,
                              'diagnostics', $8::jsonb
                          )::TEXT
                      )
                    """,
                    business_date,
                    primary_source,
                    report.status,
                    report.compared_fields,
                    report.mismatch_count,
                    report.absolute_tolerance,
                    report.relative_tolerance,
                    json.dumps(diagnostics),
                )
            if report_id is None:
                raise RuntimeError("Dashboard shadow report was not persisted")
        return int(report_id)

    async def get(self, report_id: int) -> StoredDashboardComparisonReport | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, business_date, primary_source, status, compared_fields,
                       mismatch_count, absolute_tolerance, relative_tolerance,
                       diagnostics, sheets_data_as_of, db_data_as_of, created_at
                FROM dashboard_shadow_reports
                WHERE id = $1
                """,
                report_id,
            )
        if row is None:
            return None
        diagnostics = row["diagnostics"]
        if isinstance(diagnostics, str):
            diagnostics = json.loads(diagnostics)
        return StoredDashboardComparisonReport(
            id=int(row["id"]),
            business_date=row["business_date"],
            primary_source=str(row["primary_source"]),
            status=str(row["status"]),
            compared_fields=int(row["compared_fields"]),
            mismatch_count=int(row["mismatch_count"]),
            absolute_tolerance=Decimal(str(row["absolute_tolerance"])),
            relative_tolerance=Decimal(str(row["relative_tolerance"])),
            differences=tuple(_difference(item) for item in diagnostics),
            sheets_data_as_of=row["sheets_data_as_of"],
            db_data_as_of=row["db_data_as_of"],
            created_at=row["created_at"],
        )


def _difference(item: dict[str, object]) -> DashboardDifference:
    return DashboardDifference(
        path=str(item["path"]),
        sheet_value=_optional_decimal(item.get("sheet")),
        db_value=_optional_decimal(item.get("database")),
        absolute_delta=_optional_decimal(item.get("absoluteDelta")),
        relative_delta=_optional_decimal(item.get("relativeDelta")),
        kind=DifferenceKind(str(item["classification"])),
    )


def _optional_decimal(value: object | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
