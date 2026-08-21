"""
Реферальная программа: пригласивший получает REFERRAL_BONUS_DAYS дней VPN,
но только после того, как приглашённый друг реально оплатит первую подписку
(чтобы нельзя было накрутить бонусы фейковыми регистрациями).
"""
import logging

from aiogram import Bot
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import REFERRAL_BONUS_DAYS
from app.database import User, Payment
from app.subscriptions import activate_subscription_days

logger = logging.getLogger(__name__)

_bot_username_cache: str | None = None


async def resolve_bot_username(bot: Bot) -> str:
    """Кэширует username бота на процесс, чтобы не дёргать get_me() на каждое сообщение."""
    global _bot_username_cache
    if _bot_username_cache is None:
        me = await bot.get_me()
        _bot_username_cache = me.username
    return _bot_username_cache


def build_referral_link(bot_username: str, tg_id: int) -> str:
    return f"https://t.me/{bot_username}?start={tg_id}"


def referral_teaser(bot_username: str, tg_id: int, bonus_days: int) -> str:
    """Короткая подсказка о реферальной программе — отправляется отдельным
    сообщением сразу после первой выдачи доступа (пробный период, оплата),
    но не при пересоздании ссылки — там она будет лишней и навязчивой."""
    link = build_referral_link(bot_username, tg_id)
    return (
        f"🤝 Кстати, приведи друга — получи +{bonus_days} дней VPN бесплатно, "
        f"когда он оформит первую подписку!\n\nТвоя ссылка:\n{link}"
    )


async def mark_referral_teaser_shown(user: User) -> bool:
    """Помечает, что тизер нужно показать (флаг ставится в той же транзакции,
    что и активация подписки). Возвращает True, если тизер ещё не показывали —
    тогда вызывающий код должен отправить сообщение через send_referral_teaser_message
    ПОСЛЕ основной доставки ссылки (чтобы порядок сообщений был логичным)."""
    if user.referral_teaser_shown:
        return False
    user.referral_teaser_shown = True
    return True


async def send_referral_teaser_message(bot: Bot, tg_id: int) -> None:
    try:
        username = await resolve_bot_username(bot)
        await bot.send_message(tg_id, referral_teaser(username, tg_id, REFERRAL_BONUS_DAYS))
    except Exception:
        logger.exception(f"Не удалось отправить реферальный тизер tg_id={tg_id}")


async def maybe_send_referral_teaser(user: User, session: AsyncSession, bot: Bot) -> None:
    """Обёртка для мест, где порядок сообщений не важен (например, /grant) —
    помечает и сразу отправляет одним вызовом."""
    if await mark_referral_teaser_shown(user):
        await send_referral_teaser_message(bot, user.tg_id)


async def maybe_reward_referrer(user: User, payment: Payment, session: AsyncSession, bot: Bot) -> None:
    """Вызывается сразу после того, как оплата `payment` пользователя `user`
    помечена succeeded (в той же сессии, до commit). Если это первая успешная
    оплата этого пользователя и он был приглашён — начисляет бонус рефереру.
    Ничего не коммитит — коммит делает вызывающий код."""
    if not user.referred_by_id or user.referral_bonus_given:
        return

    prior_result = await session.execute(
        select(func.count(Payment.id)).where(
            Payment.user_id == user.id,
            Payment.status == "succeeded",
            Payment.id != payment.id,
        )
    )
    prior_successful_payments = prior_result.scalar_one()
    if prior_successful_payments > 0:
        return  # это не первая оплата — бонус уже должен был выдаться раньше

    referrer_result = await session.execute(select(User).where(User.id == user.referred_by_id))
    referrer = referrer_result.scalar_one_or_none()
    if not referrer:
        return

    try:
        await activate_subscription_days(referrer, REFERRAL_BONUS_DAYS, session)
    except Exception:
        logger.exception(f"Не удалось начислить реферальный бонус referrer_id={referrer.id}")
        return

    user.referral_bonus_given = True

    try:
        await bot.send_message(
            referrer.tg_id,
            f"🤝 Ваш друг оформил первую подписку!\n"
            f"Вам начислено <b>+{REFERRAL_BONUS_DAYS} дней</b> VPN.\n\n"
            f"Новая дата окончания: {referrer.subscription_end.strftime('%d.%m.%Y')}",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(f"Не удалось уведомить referrer tg_id={referrer.tg_id} о начислении бонуса")
