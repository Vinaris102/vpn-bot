from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Telegram
    bot_token: str
    admin_ids: str = ""

    # Marzban
    marzban_url: str
    marzban_username: str
    marzban_password: str
    marzban_inbound_tag: str = "VLESS TCP REALITY"

    # YooKassa (пока не используется — оставлено на будущее, см. app/payment_client.py)
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_return_url: str = ""

    # Ручная оплата картой (текущий способ)
    card_number: str = "2202 2061 1399 4631"
    card_holder: str = "Винарис К."

    # Backend
    database_url: str = "sqlite+aiosqlite:///./vpnbot.db"
    webhook_base_url: str

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.split(",") if x.strip()]


settings = Settings()

# Тарифы. code — уникальный идентификатор (используется в callback_data кнопок),
# months — на сколько месяцев продлевается подписка, devices — сколько устройств
# можно подключить ОДНОВРЕМЕННО. Технически лимит устройств не forces — Marzban/Xray
# по умолчанию не ограничивает число одновременных подключений с одной ссылкой,
# поэтому "семейный" тариф — это маркетинговая упаковка (дороже за тот же доступ,
# явно разрешаем и советуем шарить ссылку с семьёй), а не отдельная техническая настройка.
PLANS = [
    {"code": "1m", "months": 1, "price": 150, "label": "1 месяц", "devices": 1},
    {"code": "3m", "months": 3, "price": 390, "label": "3 месяца (экономия 60₽)", "devices": 1},
    {"code": "6m", "months": 6, "price": 690, "label": "6 месяцев (экономия 210₽)", "devices": 1},
    {"code": "12m", "months": 12, "price": 1190, "label": "12 месяцев (экономия 610₽)", "devices": 1},
]


def get_plan(code: str) -> dict | None:
    return next((p for p in PLANS if p["code"] == code), None)


# Пробный период (бесплатно, один раз на пользователя)
TRIAL_DAYS = 3

# Реферальная программа: сколько дней VPN получает пригласивший,
# когда приглашённый друг оплачивает первую подписку
REFERRAL_BONUS_DAYS = 14

# За сколько дней ДО истечения подписки напоминать клиенту о продлении
# (напоминание уходит один раз в этом окне на каждый период подписки)
EXPIRY_REMINDER_DAYS = 3
