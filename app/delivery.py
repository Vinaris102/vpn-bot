"""
Единая точка отправки ссылки на подписку пользователю — из любого места бота
(подтверждение оплаты, /grant, /newlink, /relink, пробный период и т.д.).

Раньше отправка была разбросана по коду с "except Exception: pass" —
из-за этого ошибки (например, невалидная ссылка от Marzban) проходили
незамеченными, и клиент просто ничего не получал. Здесь ошибки логируются,
а не проглатываются молча.
"""
import io
import logging

import qrcode
from aiogram import Bot
from aiogram.types import BufferedInputFile

logger = logging.getLogger(__name__)

INSTRUCTIONS_TEXT = (
    "📱 <b>Как подключиться</b>\n\n"
    "<b>iOS:</b>\n"
    "1. Установите приложение <b>Streisand</b> из App Store\n"
    "2. Откройте его → нажмите «+» → «Импорт по ссылке» (Import from URL / Add from URL)\n"
    "3. Вставьте вашу ссылку на подписку (или отсканируйте QR-код выше через камеру)\n"
    "4. Нажмите на добавленный профиль, чтобы подключиться\n\n"
    "<b>Android:</b>\n"
    "1. Установите приложение <b>v2rayNG</b> из Google Play (или APK на GitHub, если Play недоступен)\n"
    "2. Откройте его → значок «+» в правом верхнем углу → «Импорт из буфера обмена» "
    "(предварительно скопируйте ссылку) или «Сканировать QR-код»\n"
    "3. После импорта нажмите на профиль и на кнопку ▶️, чтобы подключиться"
)


def _make_qr_bytes(link: str) -> bytes:
    img = qrcode.make(link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


async def deliver_subscription_link(
    bot: Bot,
    tg_id: int,
    link: str,
    intro: str,
    until_str: str | None = None,
    include_instructions: bool = True,
) -> bool:
    """Присылает пользователю QR-код + ссылку, и (по умолчанию) инструкцию по подключению.
    include_instructions=False — для пересоздания ссылки (/newlink, /relink, кнопка
    «Пересоздать ссылку» в профиле), когда клиент уже знает, как добавлять конфиг,
    и повторная инструкция только засоряет чат.
    Возвращает True при успехе, False при ошибке (ошибка логируется, не глотается)."""
    caption = intro
    if until_str:
        caption += f"\n\nДействует до: <b>{until_str}</b>"
    caption += f"\n\nСсылка на подписку (можно вставить вручную):\n{link}"

    try:
        qr_bytes = _make_qr_bytes(link)
        photo = BufferedInputFile(qr_bytes, filename="vpn_qr.png")
        await bot.send_photo(tg_id, photo=photo, caption=caption, parse_mode="HTML")
        if include_instructions:
            await bot.send_message(tg_id, INSTRUCTIONS_TEXT, parse_mode="HTML")
        return True
    except Exception:
        logger.exception(f"Не удалось доставить подписку пользователю tg_id={tg_id}")
        return False
