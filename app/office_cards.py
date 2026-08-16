from __future__ import annotations

from pathlib import Path

from handlers.office_cards import OfficeCard

OFFICE_CARDS: dict[str, OfficeCard] = {
    "екб": OfficeCard(
        command="екб",
        photo_file_id="AgACAgIAAyEGAATT2G0dAAObaYl-f3rUjrGP94zHaQhcZEckLgEAAtwQaxvhBFBICMf2mvVQ6e4BAAMCAAN5AAM6BA",
        image_path=Path("images/ekb_office.jpeg"),
        caption=(
            "📍 <b>Адрес офиса</b>\n"
            "г. Екатеринбург, ул. <b>Малышева, 51</b>\n"
            "БЦ «<b>Высоцкий</b>», 7 этаж, офис 7/06\n"
            "<b>Пропуск</b> — на ресепшене по документу\n\n"
            "🚗 <b>Бесплатный паркинг</b>\n"
            "Въезд с <b>ул. Красноармейская</b>\n"
            "Гостевой пропуск оформляется на <b>офис 7/06</b>,\n"
            "сдаётся при выезде\n\n"
            "🔔 <b>Для входа в офис назовите номер заявки в домофон</b>"
        ),
    ),
    "члб": OfficeCard(
        command="члб",
        photo_file_id="AgACAgIAAxkDAAKYrWmMQKUDobJM7d5iXuiELE3S-QR6AAJsFWsbK2hgSNNw9YSoO8ibAQADAgADeQADOgQ",
        image_path=Path("images/chlb_office.jpg"),
        caption=(
            "📍 <b>Адрес офиса:</b>\n"
            "г. Челябинск, ул. <b>Молодогвардейцев, 31к1</b>\n"
            "БЦ «<b>Grand Vera</b>», 2 этаж, офис 6206\n\n"
            "🚗 <b>Парковка:</b>\n"
            "Въезд с пр. Победы, на территорию под шлагбаум\n"
            "Парковочное место №16\n"
            "Пожалуйста, заранее сообщите номер и марку автомобиля, чтобы мы оформили въезд.\n\n"
            "<a href=\"https://telegra.ph/Prohodka-CHelyabinsk-02-03\">Подробная инструкция</a>"
        ),
    ),
    "тюм": OfficeCard(
            command="тюм",
            photo_file_id="AgACAgIAAxkDAALZH2naOUCgl2XvCLVcTl_YjKWtWL90AAKXE2sbts3QSnOXOJXlPPi0AQADAgADeQADOwQ",
            image_path=Path("images/tum_office.jpeg"),
            caption=(
                "📍 <b>Адрес офиса:</b>\n"
                "г. Тюмень, ул. <b>Чернышевского, 1Б</b>\n"
                "БЦ <b>«Резидент»</b>, 7 этаж, офис 709\n\n"
                "🚗 <b>Парковка:</b>\n"
                "Подземный паркинг БЦ.\n"
                "Пожалуйста, заранее сообщите номер и марку автомобиля, чтобы мы оформили въезд.\n\n"
            ),
        ),
}
