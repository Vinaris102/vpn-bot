"""
Отдельный процесс: FastAPI-сервер, который слушает вебхуки от ЮKassa.
ЮKassa шлёт POST на {WEBHOOK_BASE_URL}/yookassa/webhook при смене статуса платежа.
Запуск: uvicorn app.webhook_server:app --host 0.0.0.0 --port 8080
Не забудьте поставить nginx с HTTPS перед этим портом — ЮKassa требует HTTPS.

БЕЗОПАСНОСТЬ: тело вебхука — это данные, присланные внешним HTTP-запросом, и
их НЕЛЬЗЯ считать достоверными сами по себе (кто угодно может отправить сюда
поддельный POST). Поэтому статус платежа мы не берём из тела запроса, а всегда
перепроверяем через YooKassa API своим секретным ключом (get_payment_status) —
это единственный источник правды о том, реально ли платёж оплачен.
"""
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from sqlalchemy import select

from app.database import init_db, async_session, User, Payment
from app.marzban_client import marzban
from app.notify import notify_user
from app.payment_client import get_payment_status

app = FastAPI()


@app.on_event("startup")
async def startup():
    await init_db()


@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    body = await request.json()

    obj = body.get("object", {})
    payment_id = obj.get("id")

    if not payment_id:
        raise HTTPException(status_code=400, detail="no payment id")

    # Не доверяем статусу из тела запроса — запрашиваем его напрямую у YooKassa.
    # Тело вебхука используется только как триггер "проверь этот payment_id",
    # а не как источник истины о том, что произошло.
    try:
        real_status = get_payment_status(payment_id)
    except Exception:
        raise HTTPException(status_code=502, detail="failed to verify payment with YooKassa")

    async with async_session() as session:
        result = await session.execute(
            select(Payment).where(Payment.yookassa_payment_id == payment_id)
        )
        payment = result.scalar_one_or_none()
        if not payment:
            raise HTTPException(status_code=404, detail="payment not found")

        if real_status == "succeeded" and payment.status != "succeeded":
            payment.status = "succeeded"

            user_result = await session.execute(select(User).where(User.id == payment.user_id))
            user = user_result.scalar_one()

            if user.is_active:
                await marzban.extend_user(user.marzban_username, payment.months * 30)
            else:
                await marzban.create_user(user.marzban_username, payment.months * 30)
                user.is_active = True

            base = user.subscription_end if user.subscription_end and user.subscription_end > datetime.utcnow() else datetime.utcnow()
            user.subscription_end = base + timedelta(days=30 * payment.months)

            await session.commit()

            link = await marzban.get_subscription_link(user.marzban_username)
            await notify_user(
                user.tg_id,
                f"✅ Оплата на {payment.months} мес. прошла успешно!\n\n"
                f"Ваша ссылка на подписку:\n{link}\n\n"
                f"Действует до: {user.subscription_end.strftime('%d.%m.%Y')}",
            )

        elif real_status in ("canceled", "cancelled"):
            payment.status = "canceled"
            await session.commit()

    return {"ok": True}
