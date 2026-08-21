"""
Общая функция, которую вызывают и админ-подтверждение ручной оплаты,
и /grant, и пробный период — чтобы не дублировать логику продления.
"""
import logging
from datetime import datetime, timedelta

from app.database import User
from app.marzban_client import marzban

logger = logging.getLogger(__name__)


async def activate_subscription_days(user: User, days: int, session) -> str:
    """Создаёт/продлевает пользователя в Marzban на `days` дней от текущей даты
    окончания (если она ещё не наступила) или от сейчас. Обновляет subscription_end.
    Возвращает ссылку на подписку. Не коммитит сессию — это делает вызывающий код.

    Флаг user.is_active в нашей БД может разойтись с реальностью в Marzban
    (например, пользователя удалили вручную из панели во время тестов).
    Поэтому перед продлением всегда проверяем, что пользователь правда
    существует в Marzban — если нет, создаём заново вместо попытки продлить."""
    really_exists = await marzban.user_exists(user.marzban_username)

    if user.is_active and not really_exists:
        logger.warning(
            f"Рассинхрон: {user.marzban_username} помечен активным в БД, "
            f"но отсутствует в Marzban. Создаю заново."
        )

    if user.is_active and really_exists:
        await marzban.extend_user(user.marzban_username, days)
    else:
        await marzban.create_user(user.marzban_username, days)
        user.is_active = True

    base = (
        user.subscription_end
        if user.subscription_end and user.subscription_end > datetime.utcnow()
        else datetime.utcnow()
    )
    user.subscription_end = base + timedelta(days=days)

    return await marzban.get_subscription_link(user.marzban_username)


async def activate_subscription(user: User, months: int, session) -> str:
    """Обёртка для платных тарифов, заданных в месяцах (1 месяц = 30 дней)."""
    return await activate_subscription_days(user, months * 30, session)
