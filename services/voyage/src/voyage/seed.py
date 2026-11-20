"""Synthetic seed data: two agencies, four test users, hotels and bookings. No real people or documents."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .db import Agency, Booking, Counter, Hotel, Session, User, WalletTxn, create_all, now
from .security import hash_password

# Test accounts. These are synthetic credentials for the sandbox only; HAWK's
# gateway vault reads the same values from deploy/gateway.targets.json.
TEST_USERS = [
    # (agency, email, password, display name, role)
    ("AG-1001", "admin@skyline.voyage.test", "Skyline-Admin-2026!", "Asha Menon", "agency_admin"),
    ("AG-1001", "sub@skyline.voyage.test", "Skyline-Sub-2026!", "Rohan Iyer", "sub_agent"),
    ("AG-2002", "admin@coral.voyage.test", "Coral-Admin-2026!", "Meera Pillai", "agency_admin"),
    ("AG-2002", "sub@coral.voyage.test", "Coral-Sub-2026!", "Kabir Shah", "sub_agent"),
]

AGENCIES = [
    ("AG-1001", "Skyline Travels Pvt Ltd", "27AABCS1234F1Z5", 500_000),
    ("AG-2002", "Coral Holidays LLP", "32AAFFC5678K1Z2", 350_000),
]

HOTELS = [
    ("HT-GOA-01", "Goa", "Seabreeze Resort", 4, 7_800, 6_100, "SupplierNet"),
    ("HT-GOA-02", "Goa", "Palm Grove Inn", 3, 4_200, 3_350, "BedHub"),
    ("HT-DXB-01", "Dubai", "Marina Crest", 5, 18_500, 14_900, "GulfBeds"),
    ("HT-DXB-02", "Dubai", "Old Souk Suites", 4, 9_900, 7_700, "SupplierNet"),
    ("HT-BAL-01", "Bali", "Ubud Terraces", 4, 8_600, 6_500, "IslandRooms"),
    ("HT-SIN-01", "Singapore", "Harbourfront One", 5, 21_000, 17_200, "BedHub"),
]

SEED_BOOKINGS = [
    # (agency, user email, hotel, checkin offset days, nights, rooms, travellers)
    ("AG-1001", "sub@skyline.voyage.test", "HT-GOA-01", 20, 3, 1,
     [{"name": "Test Traveller A1", "passport": "T1000001", "nationality": "IN"}]),
    ("AG-1001", "admin@skyline.voyage.test", "HT-DXB-01", 34, 4, 2,
     [{"name": "Test Traveller A2", "passport": "T1000002", "nationality": "IN"},
      {"name": "Test Traveller A3", "passport": "T1000003", "nationality": "IN"}]),
    ("AG-2002", "sub@coral.voyage.test", "HT-BAL-01", 15, 5, 1,
     [{"name": "Test Traveller B1", "passport": "T2000001", "nationality": "IN"}]),
    ("AG-2002", "admin@coral.voyage.test", "HT-SIN-01", 41, 2, 1,
     [{"name": "Test Traveller B2", "passport": "T2000002", "nationality": "AE"}]),
]


async def next_booking_id(session) -> str:
    row = await session.get(Counter, "booking", with_for_update=True)
    row.value += 1
    return f"BK-{row.value}"


async def seed() -> None:
    await create_all()
    async with Session() as s:
        if (await s.execute(select(Agency))).first():
            return
        for aid, name, gstin, bal in AGENCIES:
            s.add(Agency(id=aid, name=name, gstin=gstin, wallet_balance=bal))
        users = {}
        for i, (aid, email, pw, display, role) in enumerate(TEST_USERS, start=1):
            u = User(id=i, agency_id=aid, email=email, password_hash=hash_password(pw),
                     display_name=display, phone=f"+91-90000-0000{i}", role=role)
            users[email] = u
            s.add(u)
        for hid, city, name, stars, sell, cost, supplier in HOTELS:
            s.add(Hotel(id=hid, city=city, name=name, stars=stars, sell_price=sell,
                        supplier_cost=cost, supplier=supplier))
        s.add(Counter(name="booking", value=10000))
        s.add(Counter(name="release", value=1))
        await s.flush()
        hotels = {h[0]: h for h in HOTELS}
        for aid, email, hid, off, nights, rooms, travellers in SEED_BOOKINGS:
            bid = await next_booking_id(s)
            amount = hotels[hid][4] * nights * rooms
            checkin = (now() + timedelta(days=off)).date().isoformat()
            s.add(Booking(id=bid, agency_id=aid, user_id=users[email].id, hotel_id=hid, checkin=checkin,
                          nights=nights, rooms=rooms, amount=amount, status="CONFIRMED", travellers=travellers))
            s.add(WalletTxn(agency_id=aid, booking_id=bid, kind="DEBIT", amount=amount))
        await s.commit()
