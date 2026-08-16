from services.cash_requests.constants import CB_DEAL_DONE
from services.cash_requests.deal_status_service import CashDealStatusService
from services.cash_requests.deal_status_workflow import DONE_POLICY
from services.cash_requests.workflow_models import CashRequestResult, CashRequestStatusCommand

RequestDealDoneParams = CashRequestStatusCommand
RequestDealDoneResult = CashRequestResult


class RequestDealDoneService(CashDealStatusService):
    callback_prefix = CB_DEAL_DONE
    policy = DONE_POLICY
