"""Schema and session handling. SQLite locally, Postgres (Neon) when VOYAGE_DATABASE_URL is set."""
from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _db_url() -> str:
    url = os.environ.get("VOYAGE_DATABASE_URL", "").strip()
    if not url:
        os.makedirs("data", exist_ok=True)
        return "sqlite+aiosqlite:///data/voyage.db"
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url.replace("sslmode=require", "ssl=require")


engine = create_async_engine(_db_url(), pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Agency(Base):
    __tablename__ = "agencies"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    gstin: Mapped[str] = mapped_column(String(20))
    wallet_balance: Mapped[int] = mapped_column(Integer, default=0)  # INR, whole rupees


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[str] = mapped_column(ForeignKey("agencies.id"))
    email: Mapped[str] = mapped_column(String(160), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), default="")
    role: Mapped[str] = mapped_column(String(20))  # sub_agent | agency_admin


class Hotel(Base):
    __tablename__ = "hotels"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    city: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    stars: Mapped[int] = mapped_column(Integer)
    sell_price: Mapped[int] = mapped_column(Integer)  # per night, what agencies pay
    supplier_cost: Mapped[int] = mapped_column(Integer)  # per night, commercially confidential
    supplier: Mapped[str] = mapped_column(String(60))


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # BK-10001 ...
    agency_id: Mapped[str] = mapped_column(ForeignKey("agencies.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    hotel_id: Mapped[str] = mapped_column(ForeignKey("hotels.id"))
    checkin: Mapped[str] = mapped_column(String(10))
    nights: Mapped[int] = mapped_column(Integer)
    rooms: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # HELD | CONFIRMED | CANCELLED
    travellers: Mapped[list] = mapped_column(JSON)  # [{name, passport, nationality}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WalletTxn(Base):
    __tablename__ = "wallet_txns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agency_id: Mapped[str] = mapped_column(ForeignKey("agencies.id"))
    booking_id: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))  # DEBIT | REFUND
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
