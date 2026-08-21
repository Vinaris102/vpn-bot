"""
Обёртка над YooKassa SDK.
СБП и карта поддерживаются автоматически через 'confirmation.type: redirect' —
ЮKassa сама показывает пользователю страницу выбора способа оплаты
(карта / СБП по QR или диплинку в банк).
"""
import uuid

from yookassa import Configuration, Payment as YKPayment

from app.config import settings

Configuration.account_id = settings.yookassa_shop_id
Configuration.secret_key = settings.yookassa_secret_key


def create_payment(amount_rub: int, months: int, user_tg_id: int) -> dict:
    idempotence_key = str(uuid.uuid4())
    payment = YKPayment.create(
        {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": settings.yookassa_return_url,
            },
            "capture": True,
            "description": f"VPN подписка на {months} мес. (tg_id={user_tg_id})",
            "metadata": {"tg_id": str(user_tg_id), "months": str(months)},
        },
        idempotence_key,
    )
    return {
        "id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url,
        "status": payment.status,
    }


def get_payment_status(payment_id: str) -> str:
    payment = YKPayment.find_one(payment_id)
    return payment.status
