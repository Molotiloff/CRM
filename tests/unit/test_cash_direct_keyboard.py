from keyboards.request import deal_kb
from services.cash_requests.constants import CB_DEAL_CANCEL, CB_DEAL_READY


def test_new_cash_keyboard_has_cancel_but_no_ready_button():
    buttons = [button for row in deal_kb("Б-123456").inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == [f"{CB_DEAL_CANCEL}Б-123456"]
    assert all(not (button.callback_data or "").startswith(CB_DEAL_READY) for button in buttons)
