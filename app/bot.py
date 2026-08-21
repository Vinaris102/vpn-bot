"""
Основной процесс бота (long polling).
Запуск: python -m app.bot

Главное меню: Попробовать бесплатно / Купить подписку / Мой профиль / Помощь.
Оплата — ручная через карту с подтверждением админом (см. app/admin.py).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from sqlalchemy import select, func

from app.config import settings, PLANS, get_plan, TRIAL_DAYS, REFERRAL_BONUS_DAYS
from app.database import init_db, async_session, User, Payment
from app.admin import router as admin_router
from app.marzban_client import marzban
from app.subscriptions import activate_subscription_days
from app.delivery import deliver_subscription_link, INSTRUCTIONS_TEXT
from app.formatting import format_bytes
from app.referral import build_referral_link, mark_referral_teaser_shown, send_referral_teaser_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=settings.bot_token)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(admin_router)

bot_username: str | None = None  # заполняется в main() при старте, нужен для реферальных ссылок


class PaymentProof(StatesGroup):
    waiting_proof = State()


HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    + INSTRUCTIONS_TEXT
    + "\n\n<b>Не приходит ссылка?</b> Используйте «Мой профиль» — там всегда можно "
    "получить актуальную ссылку заново.\n\n"
    "<b>Остались вопросы?</b> Напишите администратору."
)

BOT_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню"),
    BotCommand(command="trial", description="🎁 Попробовать бесплатно"),
    BotCommand(command="buy", description="💳 Купить подписку"),
    BotCommand(command="profile", description="👤 Мой профиль"),
    BotCommand(command="invite", description="🤝 Пригласить друга"),
    BotCommand(command="help", description="❓ Помощь"),
]

# Тексты кнопок постоянной клавиатуры внизу экрана (та самая иконка ☰ справа от поля ввода)
BTN_TRIAL = f"🎁 Попробовать {TRIAL_DAYS} дня бесплатно"
BTN_BUY = "💳 Купить подписку"
BTN_PROFILE = "👤 Мой профиль"
BTN_HELP = "❓ Помощь"
BTN_REFERRAL = "🤝 Пригласить друга"


def persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TRIAL)],
            [KeyboardButton(text=BTN_BUY), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_REFERRAL)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------- Клавиатуры ----------

def plans_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{plan['label']} — {plan['price']}₽", callback_data=f"buy:{plan['code']}")]
        for plan in PLANS
    ]
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="menu:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="menu:close")]])


# ---------- Вспомогательное ----------

async def get_or_create_user(tg_id: int, username: str | None, referrer_tg_id: int | None = None) -> User:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if user:
            return user

        referred_by_id = None
        if referrer_tg_id and referrer_tg_id != tg_id:
            ref_result = await session.execute(select(User).where(User.tg_id == referrer_tg_id))
            referrer = ref_result.scalar_one_or_none()
            if referrer:
                referred_by_id = referrer.id

        user = User(
            tg_id=tg_id,
            username=username,
            marzban_username=f"tg_{tg_id}",
            referred_by_id=referred_by_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


# ---------- Команды ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    referrer_tg_id = None
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip().isdigit():
        referrer_tg_id = int(parts[1].strip())

    user = await get_or_create_user(message.from_user.id, message.from_user.username, referrer_tg_id)

    first_name = message.from_user.first_name or "друг"
    greeting = f"👋 Привет, {first_name}!\n\n"
    if not user.trial_used:
        greeting += f"🎁 Вам доступен бесплатный пробный период на {TRIAL_DAYS} дня.\n\n"
    greeting += "Выберите действие:"

    # Постоянная клавиатура снизу экрана уже содержит все действия — отдельное
    # inline-меню при /start не дублируем, чтобы не захламлять чат.
    await message.answer(greeting, reply_markup=persistent_keyboard())


@dp.message(Command("status"))
async def cmd_status(message: Message):
    await send_profile(message, message.from_user.id)


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    await send_profile(message, message.from_user.id)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=back_keyboard(), parse_mode="HTML")


@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    await message.answer("Выберите срок подписки:", reply_markup=plans_keyboard())


@dp.message(Command("trial"))
async def cmd_trial(message: Message):
    await run_trial(message, message.from_user.id, message.from_user.username)


@dp.message(Command("invite"))
async def cmd_invite(message: Message):
    await send_referral_info(message, message.from_user.id, message.from_user.username)


async def send_referral_info(message: Message, tg_id: int, username: str | None):
    user = await get_or_create_user(tg_id, username)
    link = build_referral_link(bot_username, tg_id)

    async with async_session() as session:
        total_result = await session.execute(
            select(func.count(User.id)).where(User.referred_by_id == user.id)
        )
        total_invited = total_result.scalar_one()

        paid_result = await session.execute(
            select(func.count(User.id)).where(
                User.referred_by_id == user.id, User.referral_bonus_given == True  # noqa: E712
            )
        )
        paid_invited = paid_result.scalar_one()

    text = (
        "🤝 <b>Пригласите друга</b>\n\n"
        f"Отправьте другу вашу ссылку:\n{link}\n\n"
        f"Когда друг оплатит первую подписку — вам начислится "
        f"<b>+{REFERRAL_BONUS_DAYS} дней</b> VPN бесплатно!\n\n"
        f"Приглашено друзей: {total_invited}\n"
        f"Из них оплатили подписку (бонус начислен): {paid_invited}"
    )
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")


# ---------- Постоянная клавиатура: те же действия, что и команды/inline-кнопки.
# Зарегистрированы ДО обработчиков состояния оплаты (PaymentProof), чтобы кнопки меню
# всегда имели приоритет, даже если пользователь завис на шаге отправки скриншота. ----------

@dp.message(F.text == BTN_TRIAL)
async def kb_trial(message: Message):
    await run_trial(message, message.from_user.id, message.from_user.username)


@dp.message(F.text == BTN_BUY)
async def kb_buy(message: Message):
    await message.answer("Выберите срок подписки:", reply_markup=plans_keyboard())


@dp.message(F.text == BTN_PROFILE)
async def kb_profile(message: Message):
    await send_profile(message, message.from_user.id)


@dp.message(F.text == BTN_REFERRAL)
async def kb_referral(message: Message):
    await send_referral_info(message, message.from_user.id, message.from_user.username)


@dp.message(F.text == BTN_HELP)
async def kb_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=back_keyboard(), parse_mode="HTML")


# ---------- Главное меню: обработчики ----------

@dp.callback_query(F.data == "menu:close")
async def menu_close(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        # Не удалось удалить (например, сообщение слишком старое) — просто убираем кнопки
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@dp.callback_query(F.data == "menu:buy")
async def menu_buy(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.edit_text(
            "Выберите срок подписки:", reply_markup=plans_keyboard()
        )
    except Exception:
        await callback.message.answer("Выберите срок подписки:", reply_markup=plans_keyboard())


@dp.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.edit_text(HELP_TEXT, reply_markup=back_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(HELP_TEXT, reply_markup=back_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery):
    await callback.answer()
    await send_profile(callback.message, callback.from_user.id, via_callback=True)


@dp.callback_query(F.data == "menu:referral")
async def menu_referral(callback: CallbackQuery):
    await callback.answer()
    await send_referral_info(callback.message, callback.from_user.id, callback.from_user.username)


async def send_profile(message: Message, tg_id: int, via_callback: bool = False):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()

    if not user or not user.is_active:
        text = "У вас пока нет активной подписки."
        kb = back_keyboard()
        if via_callback:
            try:
                await message.edit_text(text, reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=kb)
        return

    info = await marzban.get_user_info(user.marzban_username)
    if info is None:
        text = (
            "⚠️ Не удалось получить данные подписки (пользователь не найден на сервере). "
            "Свяжитесь с администратором."
        )
        await message.answer(text, reply_markup=back_keyboard())
        return

    used = format_bytes(info.get("used_traffic", 0))
    limit = info.get("data_limit") or 0
    limit_str = "безлимит" if not limit else format_bytes(limit)
    end = user.subscription_end.strftime("%d.%m.%Y") if user.subscription_end else "—"

    try:
        link = await marzban.get_subscription_link(user.marzban_username)
        link_line = f"\n🔗 Ссылка на подключение:\n{link}"
    except Exception:
        logger.exception(f"Не удалось получить ссылку для профиля tg_id={tg_id}")
        link_line = "\n⚠️ Не удалось получить ссылку — попробуйте позже."

    text = (
        "👤 <b>Мой профиль</b>\n\n"
        f"Статус: ✅ активна\n"
        f"Действует до: <b>{end}</b>\n"
        f"Использовано трафика: {used} / {limit_str}"
        + link_line
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пересоздать ссылку", callback_data="profile:newlink")],
            [InlineKeyboardButton(text="✖️ Закрыть", callback_data="menu:close")],
        ]
    )
    if via_callback:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "profile:newlink")
async def profile_newlink(callback: CallbackQuery):
    await callback.answer("Пересоздаю ссылку...")

    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == callback.from_user.id))
        user = result.scalar_one_or_none()

    if not user or not user.is_active:
        await callback.message.answer("У вас нет активной подписки.")
        return

    try:
        new_link = await marzban.regenerate_subscription_link(user.marzban_username)
    except Exception:
        logger.exception(f"Ошибка пересоздания ссылки для tg_id={callback.from_user.id}")
        await callback.message.answer(
            "Не получилось пересоздать ссылку — попробуйте ещё раз чуть позже."
        )
        return

    ok = await deliver_subscription_link(
        bot, callback.from_user.id, new_link, "🔄 Новая ссылка готова!", include_instructions=False
    )
    if not ok:
        await callback.message.answer(f"Ссылка: {new_link}")


# ---------- Пробный период ----------

async def run_trial(target: Message, tg_id: int, username: str | None):
    """Общая логика активации пробного периода — вызывается и из команды /trial,
    и из кнопки в меню. target — любое сообщение, у которого есть .answer()."""
    user = await get_or_create_user(tg_id, username)

    if user.trial_used:
        await target.answer("Пробный период уже был использован.")
        return

    await target.answer("⏳ Активирую пробный доступ...")

    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user.id))
        fresh_user = result.scalar_one()
        try:
            link = await activate_subscription_days(fresh_user, TRIAL_DAYS, session)
        except Exception:
            logger.exception(f"Ошибка активации пробного периода для tg_id={tg_id}")
            await session.rollback()
            await target.answer(
                "⚠️ Не получилось активировать пробный доступ — техническая ошибка на сервере. "
                "Попробуйте позже."
            )
            return

        fresh_user.trial_used = True
        show_teaser = await mark_referral_teaser_shown(fresh_user)
        await session.commit()
        end_str = fresh_user.subscription_end.strftime("%d.%m.%Y")

    ok = await deliver_subscription_link(
        bot, tg_id, link, f"🎁 Пробный доступ на {TRIAL_DAYS} дня активирован!", end_str
    )
    if not ok:
        await target.answer(f"Ссылка: {link}")

    if show_teaser:
        await send_referral_teaser_message(bot, tg_id)


@dp.callback_query(F.data == "menu:trial")
async def process_trial(callback: CallbackQuery):
    await callback.answer()
    await run_trial(callback.message, callback.from_user.id, callback.from_user.username)


# ---------- Покупка подписки ----------

@dp.callback_query(F.data.startswith("buy:"))
async def process_buy(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    plan = get_plan(code)
    if not plan:
        await callback.answer("Тариф не найден, попробуйте снова из меню", show_alert=True)
        return

    months = plan["months"]
    price = plan["price"]

    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)

    async with async_session() as session:
        # Защита от спама: если у пользователя уже есть незакрытая заявка на этот
        # же тариф, переиспользуем её вместо создания новой — иначе можно нажимать
        # «Купить» много раз подряд и заваливать админов одинаковыми уведомлениями.
        existing_result = await session.execute(
            select(Payment).where(
                Payment.user_id == user.id,
                Payment.status == "pending",
                Payment.months == months,
                Payment.amount == price,
            )
        )
        payment = existing_result.scalars().first()

        if not payment:
            payment = Payment(
                user_id=user.id,
                months=months,
                amount=price,
                status="pending",
                method="manual_card",
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)

    await state.update_data(payment_id=payment.id)
    await state.set_state(PaymentProof.waiting_proof)

    devices_note = (
        f"\n👨‍👩‍👧‍👦 Этот тариф — до {plan['devices']} устройств одновременно, "
        f"можно смело делиться ссылкой с семьёй.\n"
        if plan["devices"] > 1
        else ""
    )

    await callback.message.answer(
        f"💳 <b>Оплата картой</b>\n\n"
        f"Тариф: <b>{plan['label']}</b>\n"
        f"{devices_note}\n"
        f"Переведите <b>{price}₽</b> на карту:\n"
        f"🏦 {settings.card_number}\n"
        f"👤 {settings.card_holder}\n\n"
        f"📎 После перевода пришлите сюда <b>скриншот перевода</b> "
        f"или напишите <b>последние 4 цифры карты</b>, с которой платили — "
        f"этого достаточно, чтобы подтвердить оплату вручную.",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(PaymentProof.waiting_proof, F.photo)
async def proof_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    payment_id = data.get("payment_id")
    if not payment_id:
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    await _save_proof_and_notify_admins(message, payment_id, file_id=file_id)
    await state.clear()


@dp.message(PaymentProof.waiting_proof, F.text)
async def proof_text(message: Message, state: FSMContext):
    data = await state.get_data()
    payment_id = data.get("payment_id")
    if not payment_id:
        await state.clear()
        return

    # Ограничиваем длину — это должны быть последние 4 цифры карты, а не
    # произвольный текст; заодно защита от спама огромными сообщениями админам.
    proof = message.text.strip()[:100]

    await _save_proof_and_notify_admins(message, payment_id, text=proof)
    await state.clear()


async def _save_proof_and_notify_admins(
    message: Message, payment_id: int, file_id: str | None = None, text: str | None = None
):
    async with async_session() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        if not payment or payment.status != "pending":
            await message.answer("Эта заявка уже обработана или не найдена.")
            return

        payment.proof_file_id = file_id
        payment.proof_text = text
        await session.commit()

        user_result = await session.execute(select(User).where(User.id == payment.user_id))
        user = user_result.scalar_one()

    await message.answer(
        "✅ Принято! Заявка отправлена на проверку администратору.\n"
        "Как только оплата будет подтверждена — вы получите ссылку на подключение."
    )

    uname = f"@{user.username}" if user.username else str(user.tg_id)
    caption = (
        f"🆕 Новая заявка на оплату #{payment.id}\n"
        f"Пользователь: {uname} (id {user.tg_id})\n"
        f"Сумма: {payment.amount}₽ / {payment.months} мес.\n"
        + (f"Последние цифры карты: {text}" if text else "Приложен скриншот")
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_pay:{payment.id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_pay:{payment.id}"),
            ]
        ]
    )

    for admin_id in settings.admin_id_list:
        try:
            if file_id:
                await bot.send_photo(admin_id, photo=file_id, caption=caption, reply_markup=kb)
            else:
                await bot.send_message(admin_id, caption, reply_markup=kb)
        except Exception:
            logger.exception(f"Не удалось отправить заявку админу {admin_id}")


async def main():
    global bot_username
    await init_db()
    me = await bot.get_me()
    bot_username = me.username
    await bot.set_my_commands(BOT_COMMANDS)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
