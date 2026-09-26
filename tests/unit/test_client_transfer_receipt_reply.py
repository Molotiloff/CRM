from types import SimpleNamespace

from handlers.wallets import WalletsHandler


def _message(*, caption: str, sender_id: int = 10, has_photo: bool = True):
    return SimpleNamespace(
        bot=SimpleNamespace(id=10),
        reply_to_message=SimpleNamespace(
            from_user=SimpleNamespace(id=sender_id),
            photo=[object()] if has_photo else None,
            caption=caption,
        ),
    )


def test_reply_identifies_own_transfer_receipt() -> None:
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597")) == 597


def test_reply_ignores_other_messages_and_forged_caption() -> None:
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597", sender_id=11)) is None
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597", has_photo=False)) is None
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597 extra")) is None
