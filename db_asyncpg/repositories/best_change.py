from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from domain import DomainStateError, DomainValidationError
from services.best_change.models import (
    BestChangeAccount,
    BestChangeCalculation,
    BestChangeCorrectionResult,
    BestChangeMonthCloseResult,
    BestChangeMonthReport,
    BestChangeMonthReversalResult,
    BestChangePaymentKind,
    BestChangePaymentResult,
    BestChangePaymentReversalResult,
    BestChangePostingResult,
    CloseBestChangeMonth,
    CorrectBestChangeDeal,
    RecordBestChangeDeal,
    RecordBestChangePayment,
    ReverseBestChangeMonth,
    ReverseBestChangePayment,
)

from .base import ConnectionBoundRepo


class BestChangeRepo(ConnectionBoundRepo):
    async def prepare_correction(self, *, command: CorrectBestChangeDeal) -> date:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('best-change:' || $1, 0))",
                str(command.chat_id),
            )
            existing = await connection.fetchrow(
                """
                SELECT correction.original_deal_id, correction.source_ref,
                       replacement.deal_at
                FROM best_change_deal_corrections AS correction
                JOIN deals AS replacement
                  ON replacement.id = correction.replacement_deal_id
                WHERE correction.idempotency_key = $1
                """,
                command.idempotency_key,
            )
            if existing is not None:
                if (
                    int(existing["original_deal_id"]) != command.deal_id
                    or existing["source_ref"] != command.source_ref
                ):
                    raise DomainStateError(
                        "BestChange message was already used for another correction"
                    )
                return existing["deal_at"]
            original = await connection.fetchrow(
                """
                SELECT id, deal_at
                FROM deals
                WHERE id = $1 AND deal_type = 'best_change' AND status = 'done'
                FOR UPDATE
                """,
                command.deal_id,
            )
            if original is None:
                raise DomainValidationError(
                    "Completed BestChange deal was not found"
                )
            active_closure_id = await connection.fetchval(
                """
                SELECT id
                FROM best_change_month_closures
                WHERE chat_id = $1
                  AND period_month = date_trunc('month', $2::date)::date
                  AND status = 'closed' AND reversal_of_id IS NULL
                """,
                command.chat_id,
                original["deal_at"],
            )
            if active_closure_id is not None:
                raise DomainStateError(
                    "Reverse the BestChange month closure before correcting its deal"
                )
            return original["deal_at"]

    async def correct_deal(
        self,
        *,
        command: CorrectBestChangeDeal,
        replacement: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> BestChangeCorrectionResult:
        async with self._connection() as connection:
            existing = await connection.fetchrow(
                """
                SELECT correction.original_deal_id,
                       correction.replacement_deal_id,
                       correction.source_ref,
                       replacement.city, replacement.deal_at,
                       replacement.body, replacement.profit
                FROM best_change_deal_corrections AS correction
                JOIN deals AS replacement
                  ON replacement.id = correction.replacement_deal_id
                WHERE correction.idempotency_key = $1
                """,
                command.idempotency_key,
            )
            if existing is not None:
                if (
                    int(existing["original_deal_id"]) != command.deal_id
                    or existing["source_ref"] != command.source_ref
                ):
                    raise DomainStateError(
                        "BestChange message was already used for another correction"
                    )
                self._validate_replay(existing, replacement, calculation)
                return BestChangeCorrectionResult(
                    original_deal_id=command.deal_id,
                    replacement=await self._result(
                        connection,
                        deal_id=int(existing["replacement_deal_id"]),
                        command=replacement,
                        calculation=calculation,
                        repeated=True,
                    ),
                    repeated=True,
                )
            actor_user_id = await self._active_actor_id(
                connection,
                command.actor_tg_user_id,
                operation="correction",
            )
            original = await connection.fetchrow(
                """
                SELECT id, deal_at
                FROM deals
                WHERE id = $1 AND deal_type = 'best_change' AND status = 'done'
                FOR UPDATE
                """,
                command.deal_id,
            )
            if original is None or original["deal_at"] != replacement.deal_at:
                raise DomainStateError("BestChange correction target has changed")
            already_corrected = await connection.fetchval(
                """
                SELECT id FROM best_change_deal_corrections
                WHERE original_deal_id = $1
                """,
                command.deal_id,
            )
            if already_corrected is not None:
                raise DomainStateError("BestChange deal is already corrected")
            active_closure_id = await connection.fetchval(
                """
                SELECT id
                FROM best_change_month_closures
                WHERE chat_id = $1
                  AND period_month = date_trunc('month', $2::date)::date
                  AND status = 'closed' AND reversal_of_id IS NULL
                """,
                command.chat_id,
                replacement.deal_at,
            )
            if active_closure_id is not None:
                raise DomainStateError(
                    "Reverse the BestChange month closure before correcting its deal"
                )
            original_moves = await connection.fetch(
                """
                SELECT id, account_code, currency_code, amount
                FROM best_change_account_moves
                WHERE deal_id = $1 AND reversal_of_id IS NULL
                ORDER BY id
                """,
                command.deal_id,
            )
            for move in original_moves:
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=BestChangeAccount(move["account_code"]),
                    currency=move["currency_code"],
                    amount=-Decimal(str(move["amount"])),
                    deal_id=command.deal_id,
                    actor_user_id=actor_user_id,
                    comment=command.reason,
                    idempotency_key=(
                        f"{command.idempotency_key}:move:{int(move['id'])}"
                    ),
                    reversal_of_id=int(move["id"]),
                )
            posted = await self.post_deal(
                command=replacement,
                calculation=calculation,
            )
            if posted.repeated:
                raise DomainStateError(
                    "BestChange correction message was already used for a deal"
                )
            await connection.execute(
                """
                INSERT INTO best_change_deal_corrections(
                    original_deal_id, replacement_deal_id, actor_user_id,
                    source_ref, idempotency_key, reason
                ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                command.deal_id,
                posted.deal_id,
                actor_user_id,
                command.source_ref,
                command.idempotency_key,
                command.reason,
            )
            await connection.execute(
                "UPDATE deals SET status = 'canceled' WHERE id = $1",
                command.deal_id,
            )
            await connection.execute(
                """
                INSERT INTO deal_status_events(
                    deal_id, old_status, new_status, actor_user_id, payload
                ) VALUES ($1, 'done', 'canceled', $2, $3::jsonb)
                """,
                command.deal_id,
                actor_user_id,
                json.dumps(
                    {
                        "source": "best_change_correction",
                        "replacementDealId": posted.deal_id,
                        "reason": command.reason,
                    }
                ),
            )
            return BestChangeCorrectionResult(
                original_deal_id=command.deal_id,
                replacement=posted,
                repeated=False,
            )

    async def month_report(
        self,
        *,
        chat_id: int,
        period_month: date,
    ) -> BestChangeMonthReport:
        async with self._connection() as connection:
            return await self._month_report(
                connection,
                chat_id=chat_id,
                period_month=period_month,
            )

    async def close_month(
        self,
        *,
        command: CloseBestChangeMonth,
    ) -> BestChangeMonthCloseResult:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('best-change:' || $1, 0))",
                str(command.chat_id),
            )
            actor_user_id = await self._active_actor_id(
                connection,
                command.actor_tg_user_id,
                operation="close",
            )
            existing_id = await connection.fetchval(
                """
                SELECT id FROM best_change_month_closures
                WHERE chat_id = $1 AND period_month = $2
                  AND status = 'closed' AND reversal_of_id IS NULL
                """,
                command.chat_id,
                command.period_month,
            )
            if existing_id is not None:
                return BestChangeMonthCloseResult(
                    report=await self._month_report(
                        connection,
                        chat_id=command.chat_id,
                        period_month=command.period_month,
                    ),
                    repeated=True,
                )
            report = await self._month_report(
                connection,
                chat_id=command.chat_id,
                period_month=command.period_month,
            )
            if report.deal_count == 0:
                raise DomainValidationError("BestChange month has no completed deals")
            closure_id = await connection.fetchval(
                """
                INSERT INTO best_change_month_closures(
                    chat_id, period_month,
                    purchase_tym_rub, sale_tym_rub,
                    purchase_chlb_rub, sale_chlb_rub,
                    profit_pool_rub, partner_share_rub, skyex_profit_rub,
                    platform_fee_accrued_usdt, platform_fee_paid_usdt,
                    platform_fee_delta_usdt, deal_ids, closed_by,
                    idempotency_key, comment
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, 0, $10, $11::jsonb, $12, $13, $14
                ) RETURNING id
                """,
                command.chat_id,
                command.period_month,
                report.purchase_tym_rub,
                report.sale_tym_rub,
                report.purchase_chlb_rub,
                report.sale_chlb_rub,
                report.profit_pool_rub,
                report.partner_share_rub,
                report.skyex_profit_rub,
                report.platform_fee_accrued_usdt,
                json.dumps(list(report.deal_ids)),
                int(actor_user_id),
                command.idempotency_key,
                command.comment,
            )
            for account, amount in (
                (BestChangeAccount.PURCHASE_TYM, report.purchase_tym_rub),
                (BestChangeAccount.SALE_TYM, report.sale_tym_rub),
                (BestChangeAccount.PURCHASE_CHLB, report.purchase_chlb_rub),
                (BestChangeAccount.SALE_CHLB, report.sale_chlb_rub),
            ):
                if amount == 0:
                    continue
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=account,
                    currency="RUB",
                    amount=-amount,
                    deal_id=None,
                    closure_id=int(closure_id),
                    actor_user_id=int(actor_user_id),
                    comment=command.comment or f"Закрытие BestChange {command.period_month:%m.%Y}",
                    idempotency_key=(
                        f"{command.idempotency_key}:account:{account.value}"
                    ),
                )
            return BestChangeMonthCloseResult(
                report=await self._month_report(
                    connection,
                    chat_id=command.chat_id,
                    period_month=command.period_month,
                ),
                repeated=False,
            )

    async def record_payment(
        self,
        *,
        command: RecordBestChangePayment,
    ) -> BestChangePaymentResult:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('best-change:' || $1, 0))",
                str(command.chat_id),
            )
            actor_user_id = await self._active_actor_id(
                connection,
                command.actor_tg_user_id,
                operation="payment",
            )
            existing = await connection.fetchrow(
                """
                SELECT payment.id, payment.payment_kind, payment.currency_code,
                       payment.amount, payment.payment_reference,
                       closure.chat_id, closure.period_month
                FROM best_change_payments AS payment
                JOIN best_change_month_closures AS closure
                  ON closure.id = payment.closure_id
                WHERE payment.idempotency_key = $1
                """,
                command.idempotency_key,
            )
            if existing is not None:
                self._validate_payment_replay(existing, command)
                return await self._payment_result(
                    connection,
                    payment_id=int(existing["id"]),
                    command=command,
                    repeated=True,
                )
            closure = await connection.fetchrow(
                """
                SELECT id, partner_share_rub, platform_fee_accrued_usdt
                FROM best_change_month_closures
                WHERE chat_id = $1 AND period_month = $2
                  AND status = 'closed' AND reversal_of_id IS NULL
                FOR UPDATE
                """,
                command.chat_id,
                command.period_month,
            )
            if closure is None:
                raise DomainValidationError(
                    "BestChange month must be closed before recording a payment"
                )
            paid = Decimal(
                str(
                    await connection.fetchval(
                        """
                        SELECT COALESCE(SUM(amount), 0)
                        FROM best_change_payments
                        WHERE closure_id = $1 AND payment_kind = $2
                        """,
                        int(closure["id"]),
                        command.kind.value,
                    )
                )
            )
            accrued = Decimal(
                str(
                    closure[
                        "partner_share_rub"
                        if command.kind is BestChangePaymentKind.PARTNER
                        else "platform_fee_accrued_usdt"
                    ]
                )
            )
            payable = max(accrued, Decimal(0))
            remaining = payable - paid
            if command.amount > remaining:
                raise DomainValidationError(
                    f"BestChange payment exceeds the remaining amount: "
                    f"{remaining} {command.currency}"
                )
            if command.kind is BestChangePaymentKind.COINDROP:
                balance = await self._balance(
                    connection,
                    command.chat_id,
                    BestChangeAccount.PLATFORM_FEE,
                )
                if command.amount > balance:
                    raise DomainStateError(
                        f"CoinDrop balance is insufficient: {balance} USDT"
                    )
            payment_id = await connection.fetchval(
                """
                INSERT INTO best_change_payments(
                    closure_id, payment_kind, currency_code, amount,
                    payment_reference, actor_user_id, source_ref,
                    idempotency_key, comment
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                int(closure["id"]),
                command.kind.value,
                command.currency,
                command.amount,
                command.payment_reference,
                int(actor_user_id),
                command.source_ref,
                command.idempotency_key,
                command.comment,
            )
            if command.kind is BestChangePaymentKind.COINDROP:
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=BestChangeAccount.PLATFORM_FEE,
                    currency="USDT",
                    amount=-command.amount,
                    deal_id=None,
                    closure_id=int(closure["id"]),
                    actor_user_id=int(actor_user_id),
                    comment=command.comment or f"Выплата CoinDrop {command.payment_reference}",
                    idempotency_key=f"{command.idempotency_key}:move",
                )
                new_paid = paid + command.amount
                await connection.execute(
                    """
                    UPDATE best_change_month_closures
                    SET platform_fee_paid_usdt = $2,
                        platform_fee_delta_usdt = platform_fee_accrued_usdt - $2,
                        payment_reference = $3
                    WHERE id = $1
                    """,
                    int(closure["id"]),
                    new_paid,
                    command.payment_reference,
                )
            return await self._payment_result(
                connection,
                payment_id=int(payment_id),
                command=command,
                repeated=False,
            )

    async def reverse_payment(
        self,
        *,
        command: ReverseBestChangePayment,
    ) -> BestChangePaymentReversalResult:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('best-change:' || $1, 0))",
                str(command.chat_id),
            )
            actor_user_id = await self._active_actor_id(
                connection,
                command.actor_tg_user_id,
                operation="reversal",
            )
            existing = await connection.fetchrow(
                """
                SELECT reversal.id AS reversal_id,
                       original.id AS original_id,
                       closure.chat_id, closure.period_month
                FROM best_change_payments AS reversal
                JOIN best_change_payments AS original
                  ON original.id = reversal.reversal_of_id
                JOIN best_change_month_closures AS closure
                  ON closure.id = original.closure_id
                WHERE reversal.idempotency_key = $1
                """,
                command.idempotency_key,
            )
            if existing is not None:
                if (
                    int(existing["original_id"]) != command.payment_id
                    or int(existing["chat_id"]) != command.chat_id
                ):
                    raise DomainStateError(
                        "BestChange message was already used for another reversal"
                    )
                return BestChangePaymentReversalResult(
                    original_payment_id=int(existing["original_id"]),
                    reversal_payment_id=int(existing["reversal_id"]),
                    report=await self._month_report(
                        connection,
                        chat_id=command.chat_id,
                        period_month=existing["period_month"],
                    ),
                    repeated=True,
                )
            original = await connection.fetchrow(
                """
                SELECT payment.id, payment.closure_id, payment.payment_kind,
                       payment.currency_code, payment.amount,
                       payment.payment_reference, closure.period_month
                FROM best_change_payments AS payment
                JOIN best_change_month_closures AS closure
                  ON closure.id = payment.closure_id
                WHERE payment.id = $1 AND payment.amount > 0
                  AND closure.chat_id = $2
                FOR UPDATE OF closure
                """,
                command.payment_id,
                command.chat_id,
            )
            if original is None:
                raise DomainValidationError("BestChange payment was not found")
            already_reversed = await connection.fetchval(
                "SELECT id FROM best_change_payments WHERE reversal_of_id = $1",
                command.payment_id,
            )
            if already_reversed is not None:
                raise DomainStateError("BestChange payment is already reversed")
            reversal_id = await connection.fetchval(
                """
                INSERT INTO best_change_payments(
                    closure_id, payment_kind, currency_code, amount,
                    payment_reference, actor_user_id, source_ref,
                    idempotency_key, reversal_of_id, comment
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                int(original["closure_id"]),
                original["payment_kind"],
                original["currency_code"],
                -Decimal(str(original["amount"])),
                original["payment_reference"],
                actor_user_id,
                command.source_ref,
                command.idempotency_key,
                command.payment_id,
                command.reason,
            )
            if original["payment_kind"] == BestChangePaymentKind.COINDROP.value:
                amount = Decimal(str(original["amount"]))
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=BestChangeAccount.PLATFORM_FEE,
                    currency="USDT",
                    amount=amount,
                    deal_id=None,
                    closure_id=int(original["closure_id"]),
                    actor_user_id=actor_user_id,
                    comment=command.reason,
                    idempotency_key=f"{command.idempotency_key}:move",
                )
                await self._refresh_platform_payment_summary(
                    connection,
                    int(original["closure_id"]),
                )
            return BestChangePaymentReversalResult(
                original_payment_id=command.payment_id,
                reversal_payment_id=int(reversal_id),
                report=await self._month_report(
                    connection,
                    chat_id=command.chat_id,
                    period_month=original["period_month"],
                ),
                repeated=False,
            )

    async def reverse_month(
        self,
        *,
        command: ReverseBestChangeMonth,
    ) -> BestChangeMonthReversalResult:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('best-change:' || $1, 0))",
                str(command.chat_id),
            )
            actor_user_id = await self._active_actor_id(
                connection,
                command.actor_tg_user_id,
                operation="reversal",
            )
            existing = await connection.fetchrow(
                """
                SELECT reversal.id AS reversal_id,
                       original.id AS original_id,
                       original.chat_id, original.period_month
                FROM best_change_month_closures AS reversal
                JOIN best_change_month_closures AS original
                  ON original.id = reversal.reversal_of_id
                WHERE reversal.idempotency_key = $1
                """,
                command.idempotency_key,
            )
            if existing is not None:
                if (
                    int(existing["chat_id"]) != command.chat_id
                    or existing["period_month"] != command.period_month
                ):
                    raise DomainStateError(
                        "BestChange message was already used for another reversal"
                    )
                return BestChangeMonthReversalResult(
                    period_month=existing["period_month"],
                    original_closure_id=int(existing["original_id"]),
                    reversal_closure_id=int(existing["reversal_id"]),
                    repeated=True,
                )
            original = await connection.fetchrow(
                """
                SELECT *
                FROM best_change_month_closures
                WHERE chat_id = $1 AND period_month = $2
                  AND status = 'closed' AND reversal_of_id IS NULL
                FOR UPDATE
                """,
                command.chat_id,
                command.period_month,
            )
            if original is None:
                raise DomainValidationError(
                    "BestChange month has no active closure to reverse"
                )
            unsettled_payments = await connection.fetch(
                """
                SELECT payment_kind, SUM(amount) AS amount
                FROM best_change_payments
                WHERE closure_id = $1
                GROUP BY payment_kind
                HAVING SUM(amount) <> 0
                """,
                int(original["id"]),
            )
            if unsettled_payments:
                raise DomainStateError(
                    "Reverse all BestChange payments for the month before reversing its closure"
                )
            reversal_id = await connection.fetchval(
                """
                INSERT INTO best_change_month_closures(
                    chat_id, period_month,
                    purchase_tym_rub, sale_tym_rub,
                    purchase_chlb_rub, sale_chlb_rub,
                    profit_pool_rub, partner_share_rub, skyex_profit_rub,
                    platform_fee_accrued_usdt, platform_fee_paid_usdt,
                    platform_fee_delta_usdt, deal_ids, status, closed_by,
                    idempotency_key, reversal_of_id, comment
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, 0, $10, $11, 'reversed', $12, $13, $14, $15
                ) RETURNING id
                """,
                command.chat_id,
                command.period_month,
                -Decimal(str(original["purchase_tym_rub"])),
                -Decimal(str(original["sale_tym_rub"])),
                -Decimal(str(original["purchase_chlb_rub"])),
                -Decimal(str(original["sale_chlb_rub"])),
                -Decimal(str(original["profit_pool_rub"])),
                -Decimal(str(original["partner_share_rub"])),
                -Decimal(str(original["skyex_profit_rub"])),
                -Decimal(str(original["platform_fee_accrued_usdt"])),
                original["deal_ids"],
                actor_user_id,
                command.idempotency_key,
                int(original["id"]),
                command.reason,
            )
            for account, field in (
                (BestChangeAccount.PURCHASE_TYM, "purchase_tym_rub"),
                (BestChangeAccount.SALE_TYM, "sale_tym_rub"),
                (BestChangeAccount.PURCHASE_CHLB, "purchase_chlb_rub"),
                (BestChangeAccount.SALE_CHLB, "sale_chlb_rub"),
            ):
                amount = Decimal(str(original[field]))
                if amount == 0:
                    continue
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=account,
                    currency="RUB",
                    amount=amount,
                    deal_id=None,
                    closure_id=int(reversal_id),
                    actor_user_id=actor_user_id,
                    comment=command.reason,
                    idempotency_key=(
                        f"{command.idempotency_key}:account:{account.value}"
                    ),
                )
            await connection.execute(
                "UPDATE best_change_month_closures SET status = 'reversed' WHERE id = $1",
                int(original["id"]),
            )
            return BestChangeMonthReversalResult(
                period_month=command.period_month,
                original_closure_id=int(original["id"]),
                reversal_closure_id=int(reversal_id),
                repeated=False,
            )

    async def balances(self, *, chat_id: int) -> dict[BestChangeAccount, Decimal]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                """
                SELECT DISTINCT ON (account_code) account_code, balance_after
                FROM best_change_account_moves
                WHERE chat_id = $1
                ORDER BY account_code, id DESC
                """,
                chat_id,
            )
        current = {
            BestChangeAccount(row["account_code"]): Decimal(str(row["balance_after"]))
            for row in rows
        }
        return {account: current.get(account, Decimal(0)) for account in BestChangeAccount}

    async def post_deal(
        self,
        *,
        command: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> BestChangePostingResult:
        async with self._connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('best-change:' || $1, 0))",
                str(command.chat_id),
            )
            actor_user_id = await self._active_actor_id(
                connection,
                command.actor_tg_user_id,
                operation="deal",
            )
            existing = await connection.fetchrow(
                """
                SELECT id, city, deal_at, body, profit
                FROM deals
                WHERE source = 'tg_bot' AND source_kind = 'best_change' AND source_ref = $1
                """,
                command.source_ref,
            )
            if existing is not None:
                self._validate_replay(existing, command, calculation)
                return await self._result(
                    connection,
                    deal_id=int(existing["id"]),
                    command=command,
                    calculation=calculation,
                    repeated=True,
                )
            active_closure_id = await connection.fetchval(
                """
                SELECT id
                FROM best_change_month_closures
                WHERE chat_id = $1
                  AND period_month = date_trunc('month', $2::date)::date
                  AND status = 'closed' AND reversal_of_id IS NULL
                """,
                command.chat_id,
                command.deal_at,
            )
            if active_closure_id is not None:
                raise DomainStateError(
                    "Reverse the BestChange month closure before adding its deal"
                )
            deal_id = await connection.fetchval(
                """
                INSERT INTO deals(
                    deal_type, city, status, created_by, source, comment, body,
                    profit, deal_at, source_kind, source_ref
                ) VALUES (
                    'best_change', $1, 'done', $2, 'tg_bot', $3, $4::jsonb,
                    $5, $6, 'best_change', $7
                )
                RETURNING id
                """,
                command.city,
                int(actor_user_id),
                command.comment,
                json.dumps(calculation.body()),
                calculation.skyex_profit_rub,
                command.deal_at,
                command.source_ref,
            )
            await connection.execute(
                """
                INSERT INTO deal_status_events(
                    deal_id, old_status, new_status, actor_user_id, payload
                ) VALUES ($1, NULL, 'done', $2, $3::jsonb)
                """,
                deal_id,
                int(actor_user_id),
                json.dumps({"source": "best_change", "sourceRef": command.source_ref}),
            )
            if calculation.profit_pool_rub != 0:
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=calculation.profit_account,
                    currency="RUB",
                    amount=calculation.profit_pool_rub,
                    deal_id=int(deal_id),
                    actor_user_id=int(actor_user_id),
                    comment=command.comment,
                    idempotency_key=f"best-change:{command.source_ref}:profit",
                )
            if calculation.platform_fee_usdt != 0:
                await self._append_move(
                    connection,
                    chat_id=command.chat_id,
                    account=BestChangeAccount.PLATFORM_FEE,
                    currency="USDT",
                    amount=calculation.platform_fee_usdt,
                    deal_id=int(deal_id),
                    actor_user_id=int(actor_user_id),
                    comment=command.comment,
                    idempotency_key=f"best-change:{command.source_ref}:fee",
                )
            return await self._result(
                connection,
                deal_id=int(deal_id),
                command=command,
                calculation=calculation,
                repeated=False,
            )

    @staticmethod
    async def _append_move(
        connection,
        *,
        chat_id: int,
        account: BestChangeAccount,
        currency: str,
        amount: Decimal,
        deal_id: int | None,
        actor_user_id: int,
        comment: str | None,
        idempotency_key: str,
        closure_id: int | None = None,
        reversal_of_id: int | None = None,
    ) -> None:
        current = await connection.fetchval(
            """
            SELECT balance_after
            FROM best_change_account_moves
            WHERE chat_id = $1 AND account_code = $2
            ORDER BY id DESC LIMIT 1
            """,
            chat_id,
            account.value,
        )
        balance_after = Decimal(str(current or 0)) + amount
        await connection.execute(
            """
            INSERT INTO best_change_account_moves(
                chat_id, account_code, currency_code, amount, balance_after,
                deal_id, closure_id, actor_user_id, comment, idempotency_key,
                reversal_of_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            chat_id,
            account.value,
            currency,
            amount,
            balance_after,
            deal_id,
            closure_id,
            actor_user_id,
            comment,
            idempotency_key,
            reversal_of_id,
        )

    @classmethod
    async def _month_report(
        cls,
        connection,
        *,
        chat_id: int,
        period_month: date,
    ) -> BestChangeMonthReport:
        row = await connection.fetchrow(
            """
            WITH period_deals AS (
                SELECT id, city, body,
                       COALESCE(NULLIF(body->>'profit_pool_rub', '')::numeric, 0)
                           AS profit_pool_rub,
                       COALESCE(NULLIF(body->>'partner_share_rub', '')::numeric, 0)
                           AS partner_share_rub,
                       COALESCE(NULLIF(body->>'skyex_profit_rub', '')::numeric, 0)
                           AS skyex_profit_rub,
                       COALESCE(NULLIF(body->>'platform_fee_usdt', '')::numeric, 0)
                           AS platform_fee_usdt,
                       body->>'operation_kind' AS operation_kind
                FROM deals
                WHERE deal_type = 'best_change' AND status = 'done'
                  AND deal_at >= $1
                  AND deal_at < ($1 + INTERVAL '1 month')::date
            )
            SELECT
                COALESCE(SUM(profit_pool_rub) FILTER (
                    WHERE operation_kind = 'purchase' AND LOWER(BTRIM(city)) = 'тюм'
                ), 0) AS purchase_tym_rub,
                COALESCE(SUM(profit_pool_rub) FILTER (
                    WHERE operation_kind = 'sale' AND LOWER(BTRIM(city)) = 'тюм'
                ), 0) AS sale_tym_rub,
                COALESCE(SUM(profit_pool_rub) FILTER (
                    WHERE operation_kind = 'purchase' AND LOWER(BTRIM(city)) = 'члб'
                ), 0) AS purchase_chlb_rub,
                COALESCE(SUM(profit_pool_rub) FILTER (
                    WHERE operation_kind = 'sale' AND LOWER(BTRIM(city)) = 'члб'
                ), 0) AS sale_chlb_rub,
                COALESCE(SUM(profit_pool_rub), 0) AS profit_pool_rub,
                COALESCE(SUM(partner_share_rub), 0) AS partner_share_rub,
                COALESCE(SUM(skyex_profit_rub), 0) AS skyex_profit_rub,
                COALESCE(SUM(platform_fee_usdt), 0) AS platform_fee_accrued_usdt,
                COALESCE(ARRAY_AGG(id ORDER BY id), ARRAY[]::bigint[]) AS deal_ids
            FROM period_deals
            """,
            period_month,
        )
        closure = await connection.fetchrow(
            """
            SELECT closure.id, closure.closed_at,
                   closure.purchase_tym_rub, closure.sale_tym_rub,
                   closure.purchase_chlb_rub, closure.sale_chlb_rub,
                   closure.profit_pool_rub, closure.partner_share_rub,
                   closure.skyex_profit_rub,
                   closure.platform_fee_accrued_usdt,
                   COALESCE((
                       SELECT SUM(payment.amount)
                       FROM best_change_payments AS payment
                       WHERE payment.closure_id = closure.id
                         AND payment.payment_kind = 'partner'
                   ), 0) AS partner_paid_rub,
                   COALESCE((
                       SELECT SUM(payment.amount)
                       FROM best_change_payments AS payment
                       WHERE payment.closure_id = closure.id
                         AND payment.payment_kind = 'coindrop'
                   ), closure.platform_fee_paid_usdt) AS platform_fee_paid_usdt,
                   closure.deal_ids::text AS deal_ids_json,
                   actor.tg_user_id AS closed_by_tg_user_id
            FROM best_change_month_closures AS closure
            JOIN users AS actor ON actor.id = closure.closed_by
            WHERE closure.chat_id = $1 AND closure.period_month = $2
              AND closure.status = 'closed' AND closure.reversal_of_id IS NULL
            """,
            chat_id,
            period_month,
        )
        values = closure or row
        deal_ids = (
            tuple(int(value) for value in json.loads(closure["deal_ids_json"]))
            if closure
            else tuple(int(value) for value in row["deal_ids"])
        )
        return BestChangeMonthReport(
            period_month=period_month,
            purchase_tym_rub=Decimal(str(values["purchase_tym_rub"])),
            sale_tym_rub=Decimal(str(values["sale_tym_rub"])),
            purchase_chlb_rub=Decimal(str(values["purchase_chlb_rub"])),
            sale_chlb_rub=Decimal(str(values["sale_chlb_rub"])),
            profit_pool_rub=Decimal(str(values["profit_pool_rub"])),
            partner_share_rub=Decimal(str(values["partner_share_rub"])),
            partner_paid_rub=(
                Decimal(str(closure["partner_paid_rub"])) if closure else Decimal(0)
            ),
            partner_delta_rub=(
                Decimal(str(values["partner_share_rub"]))
                - (Decimal(str(closure["partner_paid_rub"])) if closure else Decimal(0))
            ),
            skyex_profit_rub=Decimal(str(values["skyex_profit_rub"])),
            platform_fee_accrued_usdt=Decimal(
                str(values["platform_fee_accrued_usdt"])
            ),
            platform_fee_paid_usdt=(
                Decimal(str(closure["platform_fee_paid_usdt"]))
                if closure
                else Decimal(0)
            ),
            platform_fee_delta_usdt=(
                Decimal(str(values["platform_fee_accrued_usdt"]))
                - Decimal(str(closure["platform_fee_paid_usdt"]))
                if closure
                else Decimal(str(row["platform_fee_accrued_usdt"]))
            ),
            platform_fee_outstanding_usdt=await cls._balance(
                connection,
                chat_id,
                BestChangeAccount.PLATFORM_FEE,
            ),
            deal_ids=deal_ids,
            closure_id=int(closure["id"]) if closure else None,
            closed_at=closure["closed_at"] if closure else None,
            closed_by_tg_user_id=(
                int(closure["closed_by_tg_user_id"]) if closure else None
            ),
        )

    @classmethod
    async def _payment_result(
        cls,
        connection,
        *,
        payment_id: int,
        command: RecordBestChangePayment,
        repeated: bool,
    ) -> BestChangePaymentResult:
        return BestChangePaymentResult(
            payment_id=payment_id,
            kind=command.kind,
            amount=command.amount,
            currency=command.currency,
            payment_reference=command.payment_reference,
            report=await cls._month_report(
                connection,
                chat_id=command.chat_id,
                period_month=command.period_month,
            ),
            repeated=repeated,
        )

    @staticmethod
    async def _active_actor_id(
        connection,
        tg_user_id: int,
        *,
        operation: str,
    ) -> int:
        actor_user_id = await connection.fetchval(
            "SELECT id FROM users WHERE tg_user_id = $1 AND is_active",
            tg_user_id,
        )
        if actor_user_id is None:
            raise DomainValidationError(
                f"BestChange {operation} actor is not an active CRM user"
            )
        return int(actor_user_id)

    @staticmethod
    async def _refresh_platform_payment_summary(connection, closure_id: int) -> None:
        paid = await connection.fetchval(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM best_change_payments
            WHERE closure_id = $1 AND payment_kind = 'coindrop'
            """,
            closure_id,
        )
        await connection.execute(
            """
            UPDATE best_change_month_closures
            SET platform_fee_paid_usdt = $2,
                platform_fee_delta_usdt = platform_fee_accrued_usdt - $2
            WHERE id = $1
            """,
            closure_id,
            paid,
        )

    @classmethod
    async def _result(
        cls,
        connection,
        *,
        deal_id: int,
        command: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
        repeated: bool,
    ) -> BestChangePostingResult:
        return BestChangePostingResult(
            deal_id=deal_id,
            calculation=calculation,
            profit_balance_rub=await cls._balance(
                connection,
                command.chat_id,
                calculation.profit_account,
            ),
            platform_fee_balance_usdt=await cls._balance(
                connection,
                command.chat_id,
                BestChangeAccount.PLATFORM_FEE,
            ),
            repeated=repeated,
        )

    @staticmethod
    async def _balance(connection, chat_id: int, account: BestChangeAccount) -> Decimal:
        value = await connection.fetchval(
            """
            SELECT balance_after
            FROM best_change_account_moves
            WHERE chat_id = $1 AND account_code = $2
            ORDER BY id DESC LIMIT 1
            """,
            chat_id,
            account.value,
        )
        return Decimal(str(value or 0))

    @staticmethod
    def _validate_replay(
        row,
        command: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> None:
        body = row["body"]
        if isinstance(body, str):
            body = json.loads(body)
        if (
            str(row["city"]).strip().lower() != command.city
            or row["deal_at"] != command.deal_at
            or Decimal(str(row["profit"])) != calculation.skyex_profit_rub
            or body != calculation.body()
        ):
            raise DomainStateError("BestChange message was already used for another deal")

    @staticmethod
    def _validate_payment_replay(row, command: RecordBestChangePayment) -> None:
        if (
            int(row["chat_id"]) != command.chat_id
            or row["period_month"] != command.period_month
            or row["payment_kind"] != command.kind.value
            or row["currency_code"] != command.currency
            or Decimal(str(row["amount"])) != command.amount
            or row["payment_reference"] != command.payment_reference
        ):
            raise DomainStateError(
                "BestChange message was already used for another payment"
            )
