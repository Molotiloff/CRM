from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.container import ApplicationContainer
from config import Config

from . import ws
from .exception_handlers import register_exception_handlers
from .routers import auth, balances, clients, dashboard, deals, fulfillments, health


def create_api_app(
    config: Config,
    *,
    container: ApplicationContainer,
) -> FastAPI:
    crm_repositories = container.crm_repositories
    crm_services = container.crm

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await crm_repositories.users.seed_from_managers(config.admin_ids)
        yield

    app = FastAPI(title="SkyEX CRM API", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.container = container
    app.state.user_repository = crm_repositories.users
    app.state.client_queries = container.api_queries.clients
    app.state.balance_queries = container.api_queries.balances
    app.state.dashboard_queries = container.api_queries.dashboard
    app.state.deal_repository = crm_repositories.deals
    app.state.deal_event_bus = crm_services.event_bus
    app.state.deal_service = crm_services.deals
    app.state.deal_source_mutation_service = crm_services.source_mutation
    app.state.deal_settlement_service = container.accounting.deal_settlements
    app.state.fulfillment_queue_service = container.accounting.fulfillment_queue
    app.state.metrics = container.metrics
    register_exception_handlers(app)

    if config.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.api_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(clients.router)
    app.include_router(balances.router)
    app.include_router(dashboard.router)
    app.include_router(deals.router)
    app.include_router(fulfillments.router)
    app.include_router(ws.router)
    return app
