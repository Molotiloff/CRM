import asyncio
import logging

import asyncpg
import uvicorn
from aiogram import Bot, Dispatcher

from api.app import create_api_app
from app.container import ApplicationContainer
from app.lifecycle import AsyncServerLifecycleAdapter
from app.runtime import BotRuntimeServices
from app.setup_handlers import setup_handlers
from config import Config
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.pool import close_pool, create_pool
from middlewares.dedup import DedupMiddleware
from observability import configure_logging
from telegram_adapters import AiogramMessenger


class BotApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bot = Bot(token=config.bot_token)
        self.container: ApplicationContainer | None = None

        request_chat_id = self.config.request_chat_id
        city_cash_chats = self.config.cash_chat_map

        ignore_chat_ids = set()
        if request_chat_id:
            ignore_chat_ids.add(int(request_chat_id))
        ignore_chat_ids.update(int(x) for x in city_cash_chats.values())
        self.ignore_chat_ids = ignore_chat_ids

        self.dp = Dispatcher()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

        self.dp.message.middleware(DedupMiddleware())
        self.dp.callback_query.middleware(DedupMiddleware())

        self.services: BotRuntimeServices | None = None

    async def _on_startup(self) -> None:
        if self.services is None:
            raise RuntimeError("Bot services are not initialized")
        await self.services.start(bot=self.bot, config=self.config)

    async def _on_shutdown(self) -> None:
        if self.services is None:
            return
        await self.services.stop()

    def _bootstrap(self, pool: asyncpg.Pool) -> None:
        self.container = ApplicationContainer.build(
            self.config,
            pool,
            messenger=AiogramMessenger(self.bot),
        )
        self.services = setup_handlers(
            dp=self.dp,
            bot=self.bot,
            container=self.container,
            ignore_chat_ids=self.ignore_chat_ids,
        )

    async def run(self) -> None:
        logging.info("Applying database migrations…")
        await AlembicMigrator(self.config.database_url).upgrade_to_head()
        logging.info("Connecting to Postgres…")
        pool = await create_pool(self.config.database_url)
        api_lifecycle = None
        try:
            self._bootstrap(pool)
            if self.services is None:
                raise RuntimeError("Bot services are not initialized")
            logging.info(
                "Bot is starting… (request_chat_id=%s, city_cash_chats=%s, "
                "ignore_chat_ids=%s, city_cash_chat_ids=%s, rate_orders_chat_id=%s, "
                "aml_enabled=%s, rapira_enabled=%s)",
                self.config.request_chat_id,
                self.config.cash_chat_map,
                self.ignore_chat_ids,
                self.config.city_cash_chat_ids,
                self.config.rate_orders_chat_id,
                bool(self.config.getblock),
                self.services.market_ws_service is not None,
            )
            api_server = self._create_api_server()
            if api_server is not None:
                api_lifecycle = AsyncServerLifecycleAdapter(
                    api_server,
                    task_name="crm-api",
                )
                await api_lifecycle.start()
                logging.info(
                    "CRM API is starting… (host=%s, port=%s)",
                    self.config.api_host,
                    self.config.api_port,
                )
            await self.dp.start_polling(self.bot)
        finally:
            try:
                if api_lifecycle is not None:
                    await api_lifecycle.stop()
            finally:
                await close_pool(pool)

    def _create_api_server(self) -> uvicorn.Server | None:
        if not self.config.api_enabled:
            return None
        if self.container is None:
            raise RuntimeError("Application container is not initialized")

        api_app = create_api_app(
            self.config,
            container=self.container,
        )
        uvicorn_config = uvicorn.Config(
            api_app,
            host=self.config.api_host,
            port=self.config.api_port,
            log_config=None,
            access_log=False,
        )
        return uvicorn.Server(uvicorn_config)


def run_app() -> None:
    configure_logging()
    config = Config.from_env()
    app = BotApp(config)
    asyncio.run(app.run())
