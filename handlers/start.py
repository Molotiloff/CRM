# handlers/start.py
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from keyboards import MainKeyboard
from services.admin_client import ClientBootstrapService
from telegram_adapters.message_context import get_chat_name


class StartHandler:
    def __init__(self, bootstrap_service: ClientBootstrapService) -> None:
        self.bootstrap_service = bootstrap_service
        self.router = Router()
        self._register()

    async def _on_start(self, message: Message) -> None:
        # регистрируем/обновляем клиента (чат) в БД
        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        await self.bootstrap_service.ensure_client_wallet(chat_id=chat_id, chat_name=chat_name)

        text = (
            "👋 Привет! Я бот команды <b>SKYEX</b>.\n\n"
            "Я фиксирую детали сделки и веду её учёт — это гарантирует точность и прозрачность операций.\n\n"
            "📜 <b>Условия работы:</b>\n\n"
            "1️⃣ ❗️ <b>Фикс</b> — это подтверждение курса сделки.\n"
            "После фикса курс закрепляется и не меняется.\n\n"
            "В случае отмены или срыва фикса может применяться неустойка, "
            "если курс изменился и мы понесли убытки.\n\n"
            "2️⃣ Перед отправкой USDT обязательно уточняйте актуальный кошелёк "
            "командой <code>/кош</code> или у менеджера\n\n"
            "3️⃣ 🕒 <b>График работы офиса:</b>\n"
            "• Пн–Пт: 11:00 – 20:00\n"
            "• Сб: 13:00 – 17:00\n"
            "• Вне графика — по согласованию\n\n"
            "⚙️ <b>Команды:</b>\n"
            "<code>/дай</code> — показывает актуальный баланс\n"
            "<code>/кош</code> — актуальный кошелёк\n"
            "<code>/екб</code> / <code>/члб</code> — информация по проходкам в городах"
        )
        # Не показываем клавиатуру автоматически
        await message.answer(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

    async def _show_help(self, message: Message) -> None:
        text = (
            "👋 Привет! Я бот команды <b>SKYEX</b>.\n\n"
            "Я фиксирую детали сделки и веду её учёт — это гарантирует точность и прозрачность операций.\n\n"
            "📜 <b>Условия работы:</b>\n\n"
            "1️⃣ ❗️ <b>Фикс</b> — это подтверждение курса сделки.\n"
            "После фикса курс закрепляется и не меняется.\n\n"
            "В случае отмены или срыва фикса может применяться неустойка, "
            "если курс изменился и мы понесли убытки.\n\n"
            "2️⃣ Перед отправкой USDT обязательно уточняйте актуальный кошелёк "
            "командой <code>/кош</code> или у менеджера\n\n"
            "3️⃣ 🕒 <b>График работы офиса:</b>\n"
            "• Пн–Пт: 11:00 – 20:00\n"
            "• Сб: 13:00 – 17:00\n"
            "• Вне графика — по согласованию\n\n"
            "⚙️ <b>Команды:</b>\n"
            "<code>/дай</code> — показывает актуальный баланс\n"
            "<code>/кош</code> — актуальный кошелёк\n"
            "<code>/екб</code> / <code>/члб</code> — информация по проходкам в городах"
        )
        # Тоже без автоматического показа клавиатуры
        await message.answer(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

    async def _show_help_commands(self, message: Message) -> None:
        text_help = (
            "📖 Доступные команды\n\n"
            "🏦 Кошелёк:\n"
            "• <code>/кошелек</code> — показать все счета.\n"
            "• <code>/дай</code> — показать только ненулевые счета.\n"
            "• <code>/добавь ВАЛЮТА [точность]</code> — добавить валюту (напр. <code>/добавь CNY 2</code>).\n"
            "• <code>/удали ВАЛЮТА</code> — удалить валюту.\n\n"
            "💵 Изменение баланса (по счёту):\n"
            "• <code>/USD 250</code> — добавить 250 USD\n"
            "• <code>/RUB -100</code> — списать 100 RUB (овердрафт разрешён)\n"
            "• <code>/USDT (2+3*4)</code> — изменить на результат выражения\n\n"
            "🔁 Обмен (короткие команды):\n"
            "• Принимаем (списываем у клиента): <code>/пд</code> (USD), <code>/пе</code> (EUR), "
            "<code>/пт</code> (USDT), <code>/пр</code> (RUB)\n"
            "• Отдаём (зачисляем клиенту): <code>од</code> (USD), <code>ое</code> (EUR), "
            "<code>от</code> (USDT), <code>ор</code> (RUB)\n"
            "Примеры:\n"
            "• <code>/пд 1000 ор 1000*84 Клиент Петров</code>\n"
            "• <code>/пр 100000 от 100000/97 срочно</code>\n\n"
            "🧾 Заявки наличными:\n"
            "• Депозит: <code>/депр</code> (RUB), <code>/депт</code> (USDT), "
            "<code>/депд</code> (USD), <code>/депе</code> (EUR), <code>/депб</code> (USDW)\n"
            "• Выдача: <code>/выдр</code> (RUB), <code>/выдт</code> (USDT), "
            "<code>/выдд</code> (USD), <code>/выде</code> (EUR), <code>/выдб</code> (USDW)\n\n"
            "📊 Отчёты:\n"
            "• <code>/бк</code> — балансы клиентов (ненулевые)\n"
            "• <code>/бк &lt;ВАЛЮТА&gt; &lt;+|-&gt;</code> — фильтр\n\n"
            "👥 Клиенты и города:\n"
            "• <code>/клиенты</code> — список\n"
            "• <code>/город &lt;chat_id&gt; &lt;город&gt;</code> — присвоить город (админы)\n\n"
            "🔐 Роли и доступ:\n"
            "• <code>/mgr &lt;user_id&gt;</code> — добавить менеджера (user_id см. <code>/whoami</code>)\n\n"
            "💼 Кошелёк USDT:\n"
            "• <code>/кош</code> — адрес USDT (TRC20)\n\n"
            "🖼 Прочее:\n"
            "• <code>/проходка</code> — пропуск/паркинг\n\n"
            "Показать кнопки: <code>/кнопки</code> · Скрыть: <code>/скрыть</code>"
        )
        await message.answer(text_help, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

    async def _show_keyboard(self, message: Message) -> None:
        """Включить клавиатуру по запросу пользователя."""
        await message.answer(
            "Клавиатура включена. Выберите команду ниже:",
            reply_markup=MainKeyboard.main(),
        )

    async def _hide_keyboard(self, message: Message) -> None:
        """Спрятать клавиатуру по запросу пользователя."""
        await message.answer("Клавиатура скрыта. Чтобы вернуть — /кнопки.", reply_markup=ReplyKeyboardRemove())

    async def _cb_menu_help(self, cq: CallbackQuery) -> None:
        if cq.message:
            await self._show_help(cq.message)
        await cq.answer()

    async def _cmd_help(self, message: Message) -> None:
        await self._show_help(message)

    async def _cmd_help_commands(self, message: Message) -> None:
        await self._show_help_commands(message)

    def _register(self) -> None:
        self.router.message.register(self._on_start, CommandStart())
        self.router.message.register(self._cmd_help, Command("помоги"))
        self.router.message.register(self._cmd_help_commands, Command("help"))
        self.router.message.register(self._show_keyboard, Command("кнопки"))
        self.router.message.register(self._hide_keyboard, Command("скрыть"))
