"""
Разовый скрипт миграции для тех, у кого уже есть vpnbot.db со старой схемой
(до добавления ручной оплаты картой). Добавляет недостающие колонки в payments,
не трогая существующие данные.

Запуск (из корня проекта, с активированным venv):
    python -m app.migrate_add_manual_payment_fields
"""
import asyncio

from sqlalchemy import text

from app.database import engine


NEW_COLUMNS = {
    "method": "VARCHAR DEFAULT 'manual_card'",
    "proof_text": "VARCHAR",
    "proof_file_id": "VARCHAR",
    "reviewed_by": "INTEGER",
}

NEW_USER_COLUMNS = {
    "trial_used": "BOOLEAN DEFAULT 0",
    "referred_by_id": "INTEGER",
    "referral_bonus_given": "BOOLEAN DEFAULT 0",
    "referral_teaser_shown": "BOOLEAN DEFAULT 0",
    "expiry_reminder_sent_for": "DATETIME",
}


async def migrate():
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(payments)"))
        existing_columns = {row[1] for row in result.fetchall()}

        # yookassa_payment_id теперь должен разрешать NULL — в SQLite проще всего
        # это не потребует изменений, т.к. SQLite слабо типизирован и не проверяет
        # NOT NULL/UNIQUE так же строго при отсутствии явного constraint на старых таблицах.

        for column, ddl_type in NEW_COLUMNS.items():
            if column not in existing_columns:
                print(f"Добавляю колонку payments.{column} ...")
                await conn.execute(text(f"ALTER TABLE payments ADD COLUMN {column} {ddl_type}"))
            else:
                print(f"Колонка payments.{column} уже существует, пропускаю")

        result2 = await conn.execute(text("PRAGMA table_info(users)"))
        existing_user_columns = {row[1] for row in result2.fetchall()}

        for column, ddl_type in NEW_USER_COLUMNS.items():
            if column not in existing_user_columns:
                print(f"Добавляю колонку users.{column} ...")
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {column} {ddl_type}"))
            else:
                print(f"Колонка users.{column} уже существует, пропускаю")

    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(migrate())
