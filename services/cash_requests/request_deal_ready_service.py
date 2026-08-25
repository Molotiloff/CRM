from services.cash_requests.constants import CB_DEAL_READY
from services.cash_requests.deal_status_service import CashDealStatusService
from services.cash_requests.deal_status_workflow import READY_POLICY
from services.cash_requests.workflow_models import CashRequestResult, CashRequestStatusCommand

RequestDealReadyParams = CashRequestStatusCommand
RequestDealReadyResult = CashRequestResult


class RequestDealReadyService(CashDealStatusService):
    callback_prefix = CB_DEAL_READY
    policy = READY_POLICY
