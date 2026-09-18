from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self

from domain import DomainValidationError

from .calculator import BestChangeCalculator
from .models import (
    BestChangeAccount,
    BestChangeCalculation,
    BestChangeCorrectionResult,
    BestChangeMonthCloseResult,
    BestChangeMonthReport,
    BestChangeMonthReversalResult,
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


class BestChangeRepositoryPort(Protocol):
    async def post_deal(
        self,
        *,
        command: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> BestChangePostingResult: ...

    async def balances(self, *, chat_id: int) -> dict[BestChangeAccount, Decimal]: ...

    async def month_report(
        self,
        *,
        chat_id: int,
        period_month: date,
    ) -> BestChangeMonthReport: ...

    async def close_month(
        self,
        *,
        command: CloseBestChangeMonth,
    ) -> BestChangeMonthCloseResult: ...

    async def record_payment(
        self,
        *,
        command: RecordBestChangePayment,
    ) -> BestChangePaymentResult: ...

    async def reverse_payment(
        self,
        *,
        command: ReverseBestChangePayment,
    ) -> BestChangePaymentReversalResult: ...

    async def reverse_month(
        self,
        *,
        command: ReverseBestChangeMonth,
    ) -> BestChangeMonthReversalResult: ...

    async def prepare_correction(
        self,
        *,
        command: CorrectBestChangeDeal,
    ) -> date: ...

    async def correct_deal(
        self,
        *,
        command: CorrectBestChangeDeal,
        replacement: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> BestChangeCorrectionResult: ...


class BestChangeUnitOfWorkPort(Protocol):
    best_change: BestChangeRepositoryPort

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


BestChangeUnitOfWorkFactory = Callable[[], BestChangeUnitOfWorkPort]


class BestChangeService:
    def __init__(
        self,
        unit_of_work_factory: BestChangeUnitOfWorkFactory,
        *,
        chat_id: int,
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        if chat_id == 0:
            raise ValueError("BestChange chat id must not be zero")
        self._unit_of_work_factory = unit_of_work_factory
        self._chat_id = chat_id
        self._today = today_factory

    async def record(self, command: RecordBestChangeDeal) -> BestChangePostingResult:
        self._validate_chat(command.chat_id, "command")
        if command.deal_at > self._today():
            raise DomainValidationError("BestChange deal date cannot be in the future")
        calculation = BestChangeCalculator.calculate(command)
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.post_deal(
                command=command,
                calculation=calculation,
            )
            await unit_of_work.commit()
            return result

    async def balances(self) -> dict[BestChangeAccount, Decimal]:
        async with self._unit_of_work_factory() as unit_of_work:
            balances = await unit_of_work.best_change.balances(chat_id=self._chat_id)
            await unit_of_work.commit()
            return balances

    async def month_report(self, period_month: date) -> BestChangeMonthReport:
        _validate_period_month(period_month)
        async with self._unit_of_work_factory() as unit_of_work:
            report = await unit_of_work.best_change.month_report(
                chat_id=self._chat_id,
                period_month=period_month,
            )
            await unit_of_work.commit()
            return report

    async def close_month(
        self,
        command: CloseBestChangeMonth,
    ) -> BestChangeMonthCloseResult:
        self._validate_chat(command.chat_id, "close")
        _validate_period_month(command.period_month)
        today = self._today()
        current_month = today.replace(day=1)
        if command.period_month >= current_month:
            raise DomainValidationError("Only a completed BestChange month can be closed")
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.close_month(command=command)
            await unit_of_work.commit()
            return result

    async def record_payment(
        self,
        command: RecordBestChangePayment,
    ) -> BestChangePaymentResult:
        self._validate_chat(command.chat_id, "payment")
        _validate_period_month(command.period_month)
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.record_payment(command=command)
            await unit_of_work.commit()
            return result

    async def reverse_payment(
        self,
        command: ReverseBestChangePayment,
    ) -> BestChangePaymentReversalResult:
        self._validate_chat(command.chat_id, "reversal")
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.reverse_payment(command=command)
            await unit_of_work.commit()
            return result

    async def reverse_month(
        self,
        command: ReverseBestChangeMonth,
    ) -> BestChangeMonthReversalResult:
        self._validate_chat(command.chat_id, "reversal")
        _validate_period_month(command.period_month)
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.reverse_month(command=command)
            await unit_of_work.commit()
            return result

    async def correct_deal(
        self,
        command: CorrectBestChangeDeal,
    ) -> BestChangeCorrectionResult:
        self._validate_chat(command.chat_id, "correction")
        async with self._unit_of_work_factory() as unit_of_work:
            deal_at = await unit_of_work.best_change.prepare_correction(command=command)
            replacement = command.replacement(deal_at=deal_at)
            calculation = BestChangeCalculator.calculate(replacement)
            result = await unit_of_work.best_change.correct_deal(
                command=command,
                replacement=replacement,
                calculation=calculation,
            )
            await unit_of_work.commit()
            return result

    def _validate_chat(self, chat_id: int, operation: str) -> None:
        if chat_id != self._chat_id:
            raise DomainValidationError(
                f"BestChange {operation} is allowed only in its configured chat"
            )


def _validate_period_month(period_month: date) -> None:
    if period_month.day != 1:
        raise DomainValidationError("BestChange period must be the first day of a month")
