"""
Webhook-сервер (FastAPI) и планировщик (scheduler) — отдельные от бота процессы,
поэтому для отправки сообщения пользователю каждый создаёт свой временный
экземпляр Bot и шлёт через него.
"""
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.config import settings


async def notify_user(tg_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    bot = Bot(token=settings.bot_token)
    try:
        await bot.send_message(tg_id, text, reply_markup=reply_markup, parse_mode="HTML")
    finally:
        await bot.session.close()
