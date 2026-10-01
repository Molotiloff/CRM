from __future__ import annotations

import asyncio

import uvicorn
from aiogram import Bot

from app.container import ApplicationContainer
from config import Config
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.pool import close_pool, create_pool
from observability import configure_logging
from telegram_adapters import AiogramMessenger

from .app import create_api_app


async def run_api() -> None:
    config = Config.from_env()
    await AlembicMigrator(config.database_url).upgrade_to_head()
    pool = await create_pool(config.database_url)
    bot = Bot(token=config.bot_token)
    try:
        container = ApplicationContainer.build(config, pool, messenger=AiogramMessenger(bot))
        await container.accounting.cash_chat_registry.sync()
        server = uvicorn.Server(
            uvicorn.Config(
                create_api_app(config, container=container),
                host=config.api_host,
                port=config.api_port,
                log_config=None,
            )
        )
        await server.serve()
    finally:
        await bot.session.close()
        await close_pool(pool)


def main() -> None:
    configure_logging()
    asyncio.run(run_api())


if __name__ == "__main__":
    main()
