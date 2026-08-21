from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    marzban_username: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    subscription_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=False)
    trial_used: Mapped[bool] = mapped_column(default=False)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    referral_bonus_given: Mapped[bool] = mapped_column(default=False)  # бонус за ЭТОГО (приглашённого) пользователя уже выплачен рефереру
    referral_teaser_shown: Mapped[bool] = mapped_column(default=False)  # тизер "пригласи друга" уже показывали
    expiry_reminder_sent_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # для какого subscription_end уже отправлено напоминание


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    yookassa_payment_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    months: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)  # в рублях
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/succeeded/canceled/rejected
    method: Mapped[str] = mapped_column(String, default="manual_card")  # manual_card / yookassa
    proof_text: Mapped[str | None] = mapped_column(String, nullable=True)  # последние 4 цифры и т.п.
    proof_file_id: Mapped[str | None] = mapped_column(String, nullable=True)  # telegram file_id скриншота
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # tg_id админа, кто подтвердил
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
