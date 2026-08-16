from __future__ import annotations

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.deal_status_workflow import (
    CashDealStatusPolicy,
    CashDealStatusWorkflow,
)
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator, ScheduleBoardSync
from services.cash_requests.workflow_models import CashRequestResult, CashRequestStatusCommand
from services.messaging import MessengerPort, ReplierPort


class CashDealStatusService:
    callback_prefix: str
    policy: CashDealStatusPolicy

    def __init__(
        self,
        *,
        repo: ManagerRepositoryPort,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
        workflow: CashDealStatusWorkflow | None = None,
        card_parser: CashCardParser | None = None,
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self._parser = card_parser or CashCardParser()
        self._workflow = workflow or CashDealStatusWorkflow(
            router_service=router_service,
            schedule_coordinator=CashScheduleCoordinator(
                router_service=router_service,
                schedule_service=schedule_service,
            ),
        )

    async def execute_core(
        self,
        params: CashRequestStatusCommand,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
        sync_schedule_board: ScheduleBoardSync | None = None,
    ) -> CashRequestResult:
        req_id = self._parser.request_id(
            params.callback_data,
            prefix=self.callback_prefix,
        )
        if not req_id:
            error = (
                "Не удалось определить заявку."
                if (params.callback_data or "").strip().startswith(self.callback_prefix)
                else "Некорректные данные."
            )
            await replier.alert(error)
            return CashRequestResult(ok=False, error=error)
        return await self._workflow.execute(
            params,
            req_id=req_id,
            policy=self.policy,
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule_board,
        )
