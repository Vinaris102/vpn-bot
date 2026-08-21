"""
Команды администратора. Доступны только tg_id из settings.admin_id_list (см. .env: ADMIN_IDS).

Команды:
  /admin              — общая статистика
  /users [страница]   — список активных подписок
  /find <tg_id>       — карточка конкретного пользователя
  /payments [N]       — последние N платежей (по умолчанию 10)
  /grant <tg_id> <мес> — вручную выдать/продлить подписку (например, при ручном переводе)
  /revoke <tg_id>     — вручную отключить доступ
  /relink <tg_id>     — пересоздать ссылку подписки (если у пользователя не открывается)

Плюс инлайн-кнопки "Подтвердить/Отклонить" на заявках об оплате картой
(см. app/bot.py — там пользователь присылает скриншот/цифры карты).
"""
from datetime import datetime, timedelta
import logging

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select, func, desc

from app.config import settings
from app.database import async_session, User, Payment
from app.subscriptions import activate_subscription
from app.marzban_client import marzban
from app.delivery import deliver_subscription_link
from app.referral import maybe_reward_referrer, mark_referral_teaser_shown, send_referral_teaser_message

logger = logging.getLogger(__name__)

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_id_list


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return  # молча игнорируем, чтобы не палить наличие команды

    async with async_session() as session:
        total_users = (await session.execute(select(func.count(User.id)))).scalar_one()
        active_users = (
            await session.execute(select(func.count(User.id)).where(User.is_active == True))  # noqa: E712
        ).scalar_one()
        total_revenue = (
            await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "succeeded")
            )
        ).scalar_one()
        payments_today = (
            await session.execute(
                select(func.count(Payment.id)).where(
                    Payment.status == "succeeded",
                    Payment.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0),
                )
            )
        ).scalar_one()

    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Активных подписок: {active_users}\n"
        f"Оплат сегодня: {payments_today}\n"
        f"Общая выручка: {total_revenue}₽\n\n"
        "Команды: /users /payments /find /grant /revoke",
        parse_mode="HTML",
    )


@router.message(Command("users"))
async def cmd_users(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    page_size = 15
    offset = (page - 1) * page_size

    async with async_session() as session:
        result = await session.execute(
            select(User)
            .where(User.is_active == True)  # noqa: E712
            .order_by(desc(User.subscription_end))
            .offset(offset)
            .limit(page_size)
        )
        users = result.scalars().all()

    if not users:
        await message.answer("Активных пользователей нет (или страница пустая).")
        return

    lines = [f"Страница {page}, активных подписок показано: {len(users)}\n"]
    for u in users:
        uname = f"@{u.username}" if u.username else str(u.tg_id)
        end = u.subscription_end.strftime("%d.%m.%Y") if u.subscription_end else "—"
        lines.append(f"• {uname} (id {u.tg_id}) — до {end}")

    lines.append(f"\nСледующая страница: /users {page + 1}")
    await message.answer("\n".join(lines))


@router.message(Command("find"))
async def cmd_find(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /find <tg_id>")
        return

    tg_id = int(parts[1])
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Пользователь не найден.")
            return

        payments_result = await session.execute(
            select(Payment).where(Payment.user_id == user.id).order_by(desc(Payment.created_at)).limit(5)
        )
        payments = payments_result.scalars().all()

    end = user.subscription_end.strftime("%d.%m.%Y") if user.subscription_end else "—"
    lines = [
        f"👤 {f'@{user.username}' if user.username else '(без username)'} (id {user.tg_id})",
        f"Активна: {'да' if user.is_active else 'нет'}",
        f"До: {end}",
        f"Marzban username: {user.marzban_username}",
        "\nПоследние платежи:",
    ]
    for p in payments:
        lines.append(f"• {p.created_at.strftime('%d.%m %H:%M')} — {p.amount}₽ / {p.months} мес. — {p.status}")

    await message.answer("\n".join(lines) if payments else "\n".join(lines) + "нет")


@router.message(Command("payments"))
async def cmd_payments(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10

    async with async_session() as session:
        result = await session.execute(
            select(Payment, User)
            .join(User, Payment.user_id == User.id)
            .order_by(desc(Payment.created_at))
            .limit(limit)
        )
        rows = result.all()

    if not rows:
        await message.answer("Платежей пока нет.")
        return

    lines = ["💳 Последние платежи:\n"]
    for payment, user in rows:
        uname = f"@{user.username}" if user.username else str(user.tg_id)
        lines.append(
            f"• {payment.created_at.strftime('%d.%m %H:%M')} — {uname} — "
            f"{payment.amount}₽ / {payment.months} мес. — {payment.status}"
        )

    await message.answer("\n".join(lines))


@router.message(Command("grant"))
async def cmd_grant(message: Message, bot: Bot):
    """Ручная выдача подписки, например при оплате переводом мимо автоматики."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Использование: /grant <tg_id> <месяцы>")
        return

    tg_id, months = int(parts[1]), int(parts[2])

    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Пользователь не найден — он должен хотя бы раз запустить /start у бота.")
            return

        link = await activate_subscription(user, months, session)
        await session.commit()

    await message.answer(f"Готово. Подписка до {user.subscription_end.strftime('%d.%m.%Y')}")
    ok = await deliver_subscription_link(
        bot, tg_id, link, "✅ Вам вручную выдана подписка!", user.subscription_end.strftime("%d.%m.%Y")
    )
    if not ok:
        await message.answer(
            f"⚠️ Не удалось отправить сообщение пользователю (возможно, он не открывал бота "
            f"или заблокировал его). Ссылка на всякий случай: {link}"
        )


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /revoke <tg_id>")
        return

    tg_id = int(parts[1])
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await message.answer("Пользователь не найден или уже неактивен.")
            return

        await marzban.disable_user(user.marzban_username)
        user.is_active = False
        await session.commit()

    await message.answer("Доступ отключён.")
    try:
        await bot.send_message(tg_id, "⚠️ Ваш доступ к VPN был отключён администратором.")
    except Exception:
        pass


@router.message(Command("relink"))
async def cmd_relink(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /relink <tg_id>")
        return

    tg_id = int(parts[1])
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await message.answer("Пользователь не найден или подписка неактивна.")
            return
        marzban_username = user.marzban_username

    try:
        new_link = await marzban.regenerate_subscription_link(marzban_username)
    except Exception:
        await message.answer("Ошибка при обращении к Marzban. Проверьте логи/доступность панели.")
        return

    await message.answer(f"Готово. Новая ссылка:\n{new_link}")
    ok = await deliver_subscription_link(
        bot,
        tg_id,
        new_link,
        "🔄 Администратор пересоздал вашу ссылку на подписку (старая перестала работать).",
        include_instructions=False,
    )
    if not ok:
        await message.answer("⚠️ Не удалось отправить ссылку пользователю в личку — сообщите ему вручную.")


@router.callback_query(lambda c: c.data and c.data.startswith("confirm_pay:"))
async def confirm_payment(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    # Подтверждаем нажатие СРАЗУ — дальше идут запросы к Marzban, которые могут
    # занять больше 15 сек (лимит Telegram на ответ на callback), из-за чего
    # повторный callback.answer() в конце падал с "query is too old".
    try:
        await callback.answer()
    except Exception:
        pass

    payment_id = int(callback.data.split(":")[1])

    async with async_session() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment:
            await callback.message.answer("Заявка не найдена.")
            return
        if payment.status != "pending":
            await callback.message.answer("Уже обработана.")
            return

        user_result = await session.execute(select(User).where(User.id == payment.user_id))
        user = user_result.scalar_one()

        try:
            link = await activate_subscription(user, payment.months, session)
        except Exception:
            logger.exception(f"Ошибка активации подписки для payment_id={payment_id}")
            await session.rollback()
            await callback.message.answer("⚠️ Ошибка Marzban — подписка НЕ выдана, см. логи бота.")
            return

        payment.status = "succeeded"
        payment.reviewed_by = callback.from_user.id

        try:
            await maybe_reward_referrer(user, payment, session, bot)
        except Exception:
            logger.exception(f"Ошибка начисления реферального бонуса для payment_id={payment_id}")
            # не блокируем подтверждение основной оплаты из-за сбоя в реферальной логике

        show_teaser = await mark_referral_teaser_shown(user)

        await session.commit()

        end_str = user.subscription_end.strftime("%d.%m.%Y")
        tg_id = user.tg_id

    # Обновляем сообщение у админа (убираем кнопки, чтобы не нажали дважды)
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=callback.message.caption + "\n\n✅ Подтверждено",
            )
        else:
            await callback.message.edit_text(callback.message.text + "\n\n✅ Подтверждено")
    except Exception:
        pass

    ok = await deliver_subscription_link(bot, tg_id, link, "✅ Оплата подтверждена!", end_str)
    if not ok:
        await callback.message.answer(
            f"⚠️ Подписка активирована, но сообщение пользователю не доставлено "
            f"(мог не открывать бота или заблокировал его). Ссылка: {link}"
        )

    if show_teaser:
        await send_referral_teaser_message(bot, tg_id)


@router.callback_query(lambda c: c.data and c.data.startswith("reject_pay:"))
async def reject_payment(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    try:
        await callback.answer()
    except Exception:
        pass

    payment_id = int(callback.data.split(":")[1])

    async with async_session() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment:
            await callback.message.answer("Заявка не найдена.")
            return
        if payment.status != "pending":
            await callback.message.answer("Уже обработана.")
            return

        payment.status = "rejected"
        payment.reviewed_by = callback.from_user.id
        await session.commit()

        user_result = await session.execute(select(User).where(User.id == payment.user_id))
        user = user_result.scalar_one()
        tg_id = user.tg_id

    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=callback.message.caption + "\n\n❌ Отклонено",
            )
        else:
            await callback.message.edit_text(callback.message.text + "\n\n❌ Отклонено")
    except Exception:
        pass

    try:
        await bot.send_message(
            tg_id,
            "❌ Оплата не подтверждена. Если это ошибка — свяжитесь с поддержкой "
            "или попробуйте оформить заявку заново через /start.",
        )
    except Exception:
        pass
