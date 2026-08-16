from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from db_asyncpg.ports.workflows import ExchangeCommandRepositoryPort
from observability import NULL_METRICS, MetricsRecorder
from services.act_counter import ActCounterService
from services.crm.telegram_deal_registrar import TelegramDealRegistrarPort
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.cancel_exchange_request import CancelExchangeRequest
from services.exchange.card_parser import extract_created_by, extract_request_id
from services.exchange.create_exchange_request import CreateExchangeParams, CreateExchangeRequest
from services.exchange.edit_exchange_request import EditExchangeParams, EditExchangeRequest
from services.exchange.keyboard_port import ExchangeKeyboardPort
from services.exchange.notification_builder import ExchangeNotificationBuilder
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.text_builder import ExchangeTextBuilder
from services.exchange.transaction_service import ExchangeTransactionService
from services.exchange.wallet_presenter import ExchangeWalletPresenter
from services.expression_calculator import CalcError, evaluate
from services.messaging import MessengerPort, ReplierPort
from services.number_formatting import format_rate
from services.unit_of_work import UnitOfWorkFactory


@dataclass(slots=True, frozen=True)
class ParsedAcceptShortCommand:
    recv_code: str
    recv_amount_expr: str
    pay_code: str
    pay_amount_expr: str
    user_note: str | None
    recv_amount: Decimal
    pay_amount: Decimal
    recv_prec: int
    pay_prec: int
    rate_str: str


@dataclass(slots=True, frozen=True)
class ExchangeReplyContext:
    message_id: int
    text: str
    authored_by_bot: bool


@dataclass(slots=True, frozen=True)
class AcceptShortCommand:
    chat_id: int
    chat_name: str
    message_id: int
    text: str
    actor_name: str
    reply: ExchangeReplyContext | None = None


class AcceptShortService:
    RECV_MAP = {
        "пд": "USD",
        "пе": "EUR",
        "пт": "USDT",
        "пр": "RUB",
        "пб": "USDW",
        "прмск": "РУБМСК",
        "прспб": "РУБСПБ",
        "прпер": "РУБПЕР",
        "пп": "EUR500",
    }
    PAY_MAP = {
        "од": "USD",
        "ое": "EUR",
        "от": "USDT",
        "ор": "RUB",
        "об": "USDW",
        "ормск": "РУБМСК",
        "орспб": "РУБСПБ",
        "орпер": "РУБПЕР",
        "оп": "EUR500",
    }
    RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР"}
    _COMMAND_RE = re.compile(
        r"^/(пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\s+(.+?)\s+"
        r"(од|ое|от|ор|об|ормск|орспб|орпер|оп)\s+(\S+)(?:\s+(.+))?$",
        flags=re.IGNORECASE | re.UNICODE,
    )

    def __init__(
        self,
        repo: ExchangeCommandRepositoryPort,
        request_chat_id: int | None = None,
        act_counter_service: ActCounterService | None = None,
        deal_registrar: TelegramDealRegistrarPort | None = None,
        unit_of_work_factory: UnitOfWorkFactory | None = None,
        balance_service: ExchangeBalanceService | None = None,
        calculator: ExchangeCalculator | None = None,
        text_builder: ExchangeTextBuilder | None = None,
        transaction_service: ExchangeTransactionService | None = None,
        source_links: ExchangeSourceLinkService | None = None,
        notification_builder: ExchangeNotificationBuilder | None = None,
        wallet_presenter: ExchangeWalletPresenter | None = None,
        keyboards: ExchangeKeyboardPort | None = None,
        metrics: MetricsRecorder = NULL_METRICS,
    ) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id
        if unit_of_work_factory is None:
            raise ValueError("unit_of_work_factory is required")
        if keyboards is None:
            raise ValueError("keyboards is required")
        balance_service = balance_service or ExchangeBalanceService(unit_of_work_factory)
        calculator = calculator or ExchangeCalculator()
        text_builder = text_builder or ExchangeTextBuilder()
        transaction_service = transaction_service or ExchangeTransactionService(
            unit_of_work_factory=unit_of_work_factory,
            balance_service=balance_service,
        )
        if source_links is None:
            raise ValueError("source_links is required")
        common = {
            "repo": repo,
            "request_chat_id": request_chat_id,
            "balance_service": balance_service,
            "calculator": calculator,
            "text_builder": text_builder,
            "unit_of_work_factory": unit_of_work_factory,
            "act_counter_service": act_counter_service,
            "deal_registrar": deal_registrar,
            "transaction_service": transaction_service,
            "source_links": source_links,
            "notification_builder": notification_builder or ExchangeNotificationBuilder(),
            "wallet_presenter": wallet_presenter or ExchangeWalletPresenter(),
            "keyboards": keyboards,
            "metrics": metrics,
        }
        self.create_exchange_request = CreateExchangeRequest(**common)
        self.edit_exchange_request = EditExchangeRequest(**common)
        self.cancel_exchange_request = CancelExchangeRequest(**common)

    def is_request_chat_origin(self, chat_id: int) -> bool:
        return bool(self.request_chat_id and int(chat_id) == int(self.request_chat_id))

    @classmethod
    def help_text(cls) -> str:
        return (
            "Формат:\n"
            "  /пд|/пе|/пт|/пр|/пб <сумма/expr> <од|ое|от|ор|об> <сумма/expr> [комментарий]\n\n"
            "Примеры:\n"
            "• /пд 1000 ое 1000/0.92 Клиент Петров\n"
            "• /пе (2500+500) ор 300000 «наличные»\n"
            "• /пт 700 од 700*1.08 срочно\n"
            "• /пр 100000 от 100000/94 договор №42"
        )

    async def parse_command(
        self,
        raw: str,
        *,
        chat_id: int,
        chat_name: str,
    ) -> ParsedAcceptShortCommand | None:
        match = self._COMMAND_RE.match(raw)
        if not match:
            return None

        recv_key = match.group(1).lower()
        recv_amount_expr = match.group(2).strip()
        pay_key = match.group(3).lower()
        pay_amount_expr = match.group(4).strip()
        user_note = (match.group(5) or "").strip() or None

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            raise ValueError(
                "Не распознал валюты. Используйте: /пд /пе /пт /пр /пб /прмск /прспб и "
                "од/ое/от/ор/об/ормск/орспб."
            )

        try:
            recv_raw = evaluate(recv_amount_expr)
            pay_raw = evaluate(pay_amount_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                raise ValueError("Суммы должны быть > 0")
            _ = recv_raw / pay_raw
        except ValueError:
            raise
        except (CalcError, InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError(f"Ошибка в выражениях: {exc}") from exc

        client_id = await self.repo.ensure_client(
            chat_id=chat_id,
            name=chat_name,
        )
        accounts = await self.repo.snapshot_wallet(client_id)

        acc_recv = next(
            (row for row in accounts if str(row["currency_code"]).upper() == recv_code), None
        )
        acc_pay = next(
            (row for row in accounts if str(row["currency_code"]).upper() == pay_code), None
        )
        if not acc_recv or not acc_pay:
            missing = recv_code if not acc_recv else pay_code
            raise ValueError(
                f"Счёт {missing} не найден. Добавьте валюту: /добавь {missing} [точность]"
            )

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])

        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_amount = recv_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amount = pay_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
        if recv_amount == 0 or pay_amount == 0:
            raise ValueError("Сумма слишком мала для точности выбранных валют.")

        try:
            service_cls = self.__class__
            if recv_code in service_cls.RUB_CODES or pay_code in service_cls.RUB_CODES:
                if recv_code in service_cls.RUB_CODES:
                    rub_raw = recv_raw
                    other_raw = pay_raw
                else:
                    rub_raw = pay_raw
                    other_raw = recv_raw
                rate = rub_raw / other_raw
            else:
                rate = pay_raw / recv_raw
            if not rate.is_finite() or rate <= 0:
                raise ValueError("Курс невалидный.")
            rate_str = format_rate(rate.quantize(Decimal("1e-8")))
        except ValueError:
            raise
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError("Ошибка расчёта курса.") from exc

        return ParsedAcceptShortCommand(
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,
            user_note=user_note,
            recv_amount=recv_amount,
            pay_amount=pay_amount,
            recv_prec=recv_prec,
            pay_prec=pay_prec,
            rate_str=rate_str,
        )

    async def execute_command(
        self,
        command: AcceptShortCommand,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> None:
        parsed = await self.parse_command(
            command.text,
            chat_id=command.chat_id,
            chat_name=command.chat_name,
        )
        if not parsed:
            await replier.reply(self.help_text())
            return

        is_request_chat_origin = self.is_request_chat_origin(command.chat_id)
        recv_is_deposit = is_request_chat_origin
        pay_is_withdraw = is_request_chat_origin

        handled = await self._try_edit_request(
            command,
            parsed,
            recv_is_deposit,
            pay_is_withdraw,
            messenger=messenger,
            replier=replier,
        )
        if handled:
            return

        await self.create_exchange_request.execute_core(
            CreateExchangeParams(
                chat_id=command.chat_id,
                chat_name=command.chat_name,
                source_message_id=command.message_id,
                recv_code=parsed.recv_code,
                recv_amount_expr=parsed.recv_amount_expr,
                pay_code=parsed.pay_code,
                pay_amount_expr=parsed.pay_amount_expr,
                creator_name=command.actor_name,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                note=parsed.user_note,
                reply_to_message_id=command.message_id,
            ),
            messenger=messenger,
            replier=replier,
        )

    async def _try_edit_request(
        self,
        command: AcceptShortCommand,
        parsed: ParsedAcceptShortCommand,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> bool:
        reply = command.reply
        if reply is None or not reply.text:
            return False

        if reply.authored_by_bot:
            edit_req_id = extract_request_id(reply.text)
            if not edit_req_id:
                await replier.reply(
                    "Это сообщение бота не похоже на карточку заявки.\n"
                    "Чтобы изменить и пересчитать баланс, ответьте на сообщение БОТА с заявкой."
                )
                return True
            target_bot_msg_id = reply.message_id
        else:
            if extract_request_id(reply.text):
                await replier.reply(
                    "Похоже, вы ответили на пересланную/чужую карточку.\n"
                    "Ответьте на оригинальное сообщение БОТА с заявкой."
                )
                return True
            await replier.reply(
                "Чтобы изменить заявку, ответьте на сообщение БОТА с карточкой заявки."
            )
            return True

        creator_name = extract_created_by(reply.text) or command.actor_name
        await self.edit_exchange_request.execute_core(
            EditExchangeParams(
                chat_id=command.chat_id,
                chat_name=command.chat_name,
                edit_req_id=str(edit_req_id),
                target_bot_msg_id=target_bot_msg_id,
                old_card_text=reply.text,
                cmd_msg_id=command.message_id,
                recv_code=parsed.recv_code,
                pay_code=parsed.pay_code,
                recv_amount=parsed.recv_amount,
                pay_amount=parsed.pay_amount,
                recv_prec=parsed.recv_prec,
                pay_prec=parsed.pay_prec,
                rate_str=parsed.rate_str,
                user_note=parsed.user_note,
                creator_name=creator_name,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
            ),
            messenger=messenger,
            replier=replier,
        )
        return True
