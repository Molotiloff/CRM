from services.cash_requests.constants import CB_DEAL_CANCEL
from services.cash_requests.deal_status_service import CashDealStatusService
from services.cash_requests.deal_status_workflow import CANCEL_POLICY
from services.cash_requests.workflow_models import CashRequestResult, CashRequestStatusCommand

RequestDealCancelParams = CashRequestStatusCommand
RequestDealCancelResult = CashRequestResult


class RequestDealCancelService(CashDealStatusService):
    callback_prefix = CB_DEAL_CANCEL
    policy = CANCEL_POLICY
