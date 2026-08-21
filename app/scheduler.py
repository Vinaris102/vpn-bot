"""
Отдельный процесс, который раз в час:
1. Отключает пользователей с истёкшей подпиской.
2. Присылает напоминание о скором окончании подписки (за EXPIRY_REMINDER_DAYS дней)
   с кнопкой «Продлить» — снижает отток клиентов, которые просто забыли продлить.

Запуск: python -m app.scheduler
"""
import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from app.config import EXPIRY_REMINDER_DAYS
from app.database import init_db, async_session, User
from app.marzban_client import marzban
from app.notify import notify_user

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_expired():
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.is_active == True)  # noqa: E712
        )
        users = result.scalars().all()

        for user in users:
            if user.subscription_end and user.subscription_end < datetime.utcnow():
                try:
                    await marzban.disable_user(user.marzban_username)
                except Exception:
                    logger.exception(f"Не удалось отключить пользователя {user.marzban_username} в Marzban")
                    continue  # не помечаем is_active=False, если реально не отключили — попробуем на следующем цикле
                user.is_active = False
                try:
                    await notify_user(
                        user.tg_id,
                        "⚠️ Ваша подписка истекла и доступ отключён.\n"
                        "Используйте /start, чтобы продлить.",
                    )
                except Exception:
                    logger.exception(f"Не удалось уведомить об истечении tg_id={user.tg_id}")
        await session.commit()


async def send_expiry_reminders():
    """Напоминание за EXPIRY_REMINDER_DAYS дней до конца подписки, один раз
    на каждый период подписки (отслеживается через expiry_reminder_sent_for —
    если пользователь продлит, subscription_end изменится, и напоминание
    сможет прийти снова перед следующим окончанием)."""
    now = datetime.utcnow()
    window_end = now + timedelta(days=EXPIRY_REMINDER_DAYS)

    async with async_session() as session:
        result = await session.execute(
            select(User).where(
                User.is_active == True,  # noqa: E712
                User.subscription_end != None,  # noqa: E711
                User.subscription_end > now,
                User.subscription_end <= window_end,
            )
        )
        users = result.scalars().all()

        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💳 Продлить", callback_data="menu:buy")]]
        )

        for user in users:
            if user.expiry_reminder_sent_for == user.subscription_end:
                continue  # уже отправляли напоминание именно на эту дату окончания

            days_left = (user.subscription_end - now).days
            end_str = user.subscription_end.strftime("%d.%m.%Y")

            try:
                await notify_user(
                    user.tg_id,
                    f"⏳ Ваша подписка на VPN истекает {'через ' + str(days_left) + ' дн.' if days_left > 0 else 'сегодня'} "
                    f"({end_str}).\n\nПродлите заранее, чтобы не потерять доступ:",
                    reply_markup=kb,
                )
                user.expiry_reminder_sent_for = user.subscription_end
            except Exception:
                logger.exception(f"Не удалось отправить напоминание об истечении tg_id={user.tg_id}")

        await session.commit()


async def main():
    await init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_expired, "interval", hours=1)
    scheduler.add_job(send_expiry_reminders, "interval", hours=1)
    scheduler.start()
    logger.info("Scheduler started")
    await asyncio.Event().wait()  # держим процесс живым


if __name__ == "__main__":
    asyncio.run(main())
