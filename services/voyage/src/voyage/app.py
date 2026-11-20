"""Voyage API. Flaws marked `PLANTED` are intentional (see voyage.planted); everything else is meant to be correct."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update

from . import portal
from .db import Agency, Base, Booking, Counter, Hotel, Session, User, WalletTxn, engine
from .security import check_password, issue_token, read_token
from .seed import next_booking_id, seed

OPS_TOKEN = os.environ.get("VOYAGE_OPS_TOKEN", "dev-only-ops")
WEBHOOK_URL = os.environ.get("VOYAGE_DEPLOY_WEBHOOK", "")
WEBHOOK_SECRET = os.environ.get("HAWK_WEBHOOK_SECRET", "dev-only-webhook")
ACCESS_LOG = os.environ.get("VOYAGE_ACCESS_LOG", "data/voyage-access.jsonl")
RELEASE_VERSIONS = {1: "2026.11.1", 2: "2026.11.2"}
STATE = {"strain_ms": 0}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await seed()
    yield


app = FastAPI(title="Voyage B2B Travel API", version="3.4.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)  # no published spec: HAWK must discover


# ---------------------------------------------------------------- access log
@app.middleware("http")
async def access_log(request: Request, call_next):
    t0 = time.perf_counter()
    if STATE["strain_ms"]:
        await asyncio.sleep(STATE["strain_ms"] / 1000)
    response = await call_next(request)
    route = request.scope.get("route")
    agency = None
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            agency = read_token(auth[7:]).get("agency")
        except Exception:
            agency = None
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "source": "voyage",
        "method": request.method,
        "path": request.url.path,
        "query": request.url.query,
        "route": getattr(route, "path", None),
        "status": response.status_code,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "ua": request.headers.get("user-agent", ""),
        "agency": agency,
    }
    line = json.dumps(entry)
    print(line, file=sys.stdout, flush=True)  # Cloud Logging -> BigQuery sink in the cloud
    if ACCESS_LOG:
        os.makedirs(os.path.dirname(ACCESS_LOG) or ".", exist_ok=True)
        with open(ACCESS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return response


# ---------------------------------------------------------------- helpers
async def current_user(authorization: str = Header(default="")) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = read_token(authorization[7:])
    except Exception:
        raise HTTPException(401, "invalid token")
    async with Session() as s:
        user = await s.get(User, int(claims["sub"]))
    if not user:
        raise HTTPException(401, "unknown user")
    return user  # role and agency are read from the database, not trusted from the token


async def fixed(flaw: str) -> bool:
    """Ops can switch a planted flaw to its fixed behaviour, to demo HAWK's retest closing the ticket."""
    async with Session() as s:
        row = await s.get(Counter, f"fix:{flaw}")
        return bool(row and row.value)


async def release() -> int:
    async with Session() as s:
        return (await s.get(Counter, "release")).value


def booking_view(b: Booking, hotel: Hotel, full: bool) -> dict:
    v = {"booking_id": b.id, "agency_id": b.agency_id, "hotel": {"id": hotel.id, "name": hotel.name, "city": hotel.city},
         "checkin": b.checkin, "nights": b.nights, "rooms": b.rooms, "amount": b.amount, "status": b.status}
    v["travellers"] = b.travellers if full else [{"name": t["name"]} for t in b.travellers]
    return v


def parse_offer(offer_id: str) -> tuple[str, str, int, int]:
    # OF~HT-GOA-01~2026-12-10~3~1
    try:
        _, hotel_id, checkin, nights, rooms = offer_id.split("~")
        return hotel_id, checkin, int(nights), int(rooms)
    except ValueError:
        raise HTTPException(400, "malformed offer_id")


# ---------------------------------------------------------------- auth
class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/v3/auth/login")
async def login(body: LoginIn):
    async with Session() as s:
        user = (await s.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if not user or not check_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    return {"token": issue_token(user.id, user.agency_id, user.role),
            "user": {"id": user.id, "display_name": user.display_name, "role": user.role, "agency_id": user.agency_id}}


# ---------------------------------------------------------------- search and booking
@app.get("/api/v3/hotels/search")
async def search(city: str, checkin: str, nights: int = 1, rooms: int = 1, user: User = Depends(current_user)):
    if not (1 <= nights <= 30 and 1 <= rooms <= 9):
        raise HTTPException(422, "nights 1-30, rooms 1-9")
    async with Session() as s:
        hotels = (await s.execute(select(Hotel).where(Hotel.city == city))).scalars().all()
    return {"offers": [{"offer_id": f"OF~{h.id}~{checkin}~{nights}~{rooms}", "hotel_id": h.id, "name": h.name,
                        "stars": h.stars, "city": h.city, "checkin": checkin, "nights": nights, "rooms": rooms,
                        "price": h.sell_price * nights * rooms, "currency": "INR"} for h in hotels]}


class Traveller(BaseModel):
    name: str
    passport: str
    nationality: str = "IN"


class HoldIn(BaseModel):
    offer_id: str
    travellers: list[Traveller] = Field(min_length=1, max_length=9)
    price: int | None = None  # echoed by the portal from search results


@app.post("/api/v3/bookings/hold")
async def hold(body: HoldIn, user: User = Depends(current_user)):
    hotel_id, checkin, nights, rooms = parse_offer(body.offer_id)
    if not (1 <= nights <= 30 and 1 <= rooms <= 9):
        raise HTTPException(422, "nights 1-30, rooms 1-9")
    async with Session() as s:
        hotel = await s.get(Hotel, hotel_id)
        if not hotel:
            raise HTTPException(404, "offer expired")
        expected = hotel.sell_price * nights * rooms
        amount = body.price if body.price is not None else expected  # PLANTED V-FARE-TAMPER: trusts the client
        if await fixed("V-FARE-TAMPER"):
            amount = expected
        bid = await next_booking_id(s)
        s.add(Booking(id=bid, agency_id=user.agency_id, user_id=user.id, hotel_id=hotel_id, checkin=checkin,
                      nights=nights, rooms=rooms, amount=amount, status="HELD",
                      travellers=[t.model_dump() for t in body.travellers]))
        await s.commit()
    return {"booking_id": bid, "status": "HELD", "amount": amount, "currency": "INR"}


async def own_booking(s, booking_id: str, user: User) -> Booking:
    b = await s.get(Booking, booking_id)
    if not b or b.agency_id != user.agency_id:
        raise HTTPException(404, "booking not found")
    return b


@app.post("/api/v3/bookings/{booking_id}/pay")
async def pay(booking_id: str, user: User = Depends(current_user)):
    async with Session() as s:
        b = await own_booking(s, booking_id, user)
        if b.status != "HELD":
            raise HTTPException(409, f"booking is {b.status}")
        agency = await s.get(Agency, user.agency_id)
        if agency.wallet_balance < b.amount:
            raise HTTPException(402, "insufficient wallet balance")
        agency.wallet_balance -= b.amount
        b.status = "CONFIRMED"
        s.add(WalletTxn(agency_id=agency.id, booking_id=b.id, kind="DEBIT", amount=b.amount))
        await s.commit()
        return {"booking_id": b.id, "status": b.status, "wallet_balance": agency.wallet_balance}


@app.get("/api/v3/bookings")
async def list_bookings(user: User = Depends(current_user)):
    async with Session() as s:
        rows = (await s.execute(select(Booking, Hotel).join(Hotel, Booking.hotel_id == Hotel.id)
                                .where(Booking.agency_id == user.agency_id).order_by(Booking.id))).all()
    return {"bookings": [booking_view(b, h, full=False) for b, h in rows]}


@app.get("/api/v3/bookings/{booking_id}")
async def get_booking(booking_id: str, user: User = Depends(current_user)):
    async with Session() as s:
        b = await s.get(Booking, booking_id)  # PLANTED V-IDOR-BOOKING: no agency check
        if b and b.agency_id != user.agency_id and await fixed("V-IDOR-BOOKING"):
            b = None
        if not b:
            raise HTTPException(404, "booking not found")
        hotel = await s.get(Hotel, b.hotel_id)
    return booking_view(b, hotel, full=True)


@app.post("/api/v3/bookings/{booking_id}/cancel")
async def cancel(booking_id: str, user: User = Depends(current_user)):
    async with Session() as s:
        b = await own_booking(s, booking_id, user)
        if b.status != "CONFIRMED":
            raise HTTPException(409, f"booking is {b.status}")
        amount = b.amount
    await asyncio.sleep(0.25)  # supplier cancellation call
    # PLANTED V-REFUND-RACE: the status check above is not atomic with the refund below.
    async with Session() as s:
        if await fixed("V-REFUND-RACE"):  # the fix: flip the status atomically, refund only if we flipped it
            res = await s.execute(update(Booking).where(Booking.id == booking_id, Booking.status == "CONFIRMED")
                                  .values(status="CANCELLED"))
            if res.rowcount != 1:
                raise HTTPException(409, "booking is already CANCELLED")
            await s.execute(update(Agency).where(Agency.id == user.agency_id)
                            .values(wallet_balance=Agency.wallet_balance + amount))
            s.add(WalletTxn(agency_id=user.agency_id, booking_id=booking_id, kind="REFUND", amount=amount))
            await s.commit()
            balance = (await s.get(Agency, user.agency_id, populate_existing=True)).wallet_balance
            return {"booking_id": booking_id, "status": "CANCELLED", "refunded": amount, "wallet_balance": balance}
        await s.execute(update(Agency).where(Agency.id == user.agency_id)
                        .values(wallet_balance=Agency.wallet_balance + amount))  # atomic credit...
        await s.execute(update(Booking).where(Booking.id == booking_id).values(status="CANCELLED"))
        s.add(WalletTxn(agency_id=user.agency_id, booking_id=booking_id, kind="REFUND", amount=amount))
        await s.commit()
        balance = (await s.get(Agency, user.agency_id, populate_existing=True)).wallet_balance
        return {"booking_id": booking_id, "status": "CANCELLED", "refunded": amount, "wallet_balance": balance}


@app.get("/api/v3/bookings/{booking_id}/invoice")
async def invoice(booking_id: str, user: User = Depends(current_user)):
    if await release() < 2:
        raise HTTPException(404, "Not Found")
    async with Session() as s:
        b = await s.get(Booking, booking_id)  # PLANTED V-IDOR-INVOICE (release 2): no agency check
        if b and b.agency_id != user.agency_id and await fixed("V-IDOR-INVOICE"):
            b = None
        if not b:
            raise HTTPException(404, "booking not found")
        agency = await s.get(Agency, b.agency_id)
        hotel = await s.get(Hotel, b.hotel_id)
    tax = round(b.amount * 0.12)
    return {"invoice_no": f"INV-{b.id}", "booking_id": b.id, "billed_to": {"agency": agency.name, "gstin": agency.gstin},
            "hotel": hotel.name, "amount": b.amount, "gst": tax, "total": b.amount + tax,
            "travellers": [t["name"] for t in b.travellers]}


# ---------------------------------------------------------------- wallet, profile, agency
@app.get("/api/v3/wallet")
async def wallet(user: User = Depends(current_user)):
    async with Session() as s:
        agency = await s.get(Agency, user.agency_id)
        txns = (await s.execute(select(WalletTxn).where(WalletTxn.agency_id == user.agency_id)
                                .order_by(WalletTxn.id.desc()).limit(20))).scalars().all()
    return {"agency_id": agency.id, "balance": agency.wallet_balance, "currency": "INR",
            "recent": [{"booking_id": t.booking_id, "kind": t.kind, "amount": t.amount} for t in txns]}


def user_view(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "phone": u.phone,
            "role": u.role, "agency_id": u.agency_id}


@app.get("/api/v3/users/me")
async def me(user: User = Depends(current_user)):
    return user_view(user)


@app.patch("/api/v3/users/me")
async def update_me(request: Request, user: User = Depends(current_user)):
    patch = await request.json()
    if not isinstance(patch, dict):
        raise HTTPException(422, "expected an object")
    allow_only = {"display_name", "phone"} if await fixed("V-MASS-ASSIGN-ROLE") else None
    async with Session() as s:
        u = await s.get(User, user.id)
        for key, value in patch.items():
            if allow_only is not None and key not in allow_only:
                continue
            # PLANTED V-MASS-ASSIGN-ROLE: a deny-list instead of an allow-list leaves `role` writable.
            if key in {"id", "password_hash", "agency_id", "email"} or not hasattr(u, key):
                continue
            setattr(u, key, value)
        await s.commit()
        return user_view(u)


@app.get("/api/v3/agency/users")
async def agency_users(user: User = Depends(current_user)):
    if user.role != "agency_admin":
        raise HTTPException(403, "agency admin only")
    async with Session() as s:
        rows = (await s.execute(select(User).where(User.agency_id == user.agency_id))).scalars().all()
    return {"users": [user_view(u) for u in rows]}


# ---------------------------------------------------------------- old and undocumented surfaces
@app.get("/api/v1/bookings/{booking_id}")
async def legacy_v1_booking(booking_id: str):
    # PLANTED V-ZOMBIE-V1: the 2023 mobile app's lookup. Nothing calls it any more, nobody turned it off.
    if await fixed("V-ZOMBIE-V1"):
        return JSONResponse({"error": "GONE"}, status_code=410)
    async with Session() as s:
        b = await s.get(Booking, booking_id)
        if not b:
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        hotel = await s.get(Hotel, b.hotel_id)
    return {"BookingId": b.id, "AgencyCode": b.agency_id, "HotelName": hotel.name, "Amount": b.amount,
            "Status": b.status, "Pax": b.travellers}


@app.get("/api/v2/bookings/{booking_id}")
async def legacy_v2_booking(booking_id: str):
    return JSONResponse({"error": "GONE", "message": "v2 retired, use /api/v3"}, status_code=410)


@app.get("/api/v3/partner/rates")
async def partner_rates(city: str, user: User = Depends(current_user)):
    # PLANTED V-SHADOW-RATES: built for one partner's nightly sync, never documented, never role-checked.
    async with Session() as s:
        hotels = (await s.execute(select(Hotel).where(Hotel.city == city))).scalars().all()
    return {"city": city, "rates": [{"hotel_id": h.id, "supplier": h.supplier, "sell_price": h.sell_price,
                                     "net_cost": h.supplier_cost, "margin": h.sell_price - h.supplier_cost}
                                    for h in hotels]}


# ---------------------------------------------------------------- portal and health
@app.get("/portal", response_class=HTMLResponse)
async def portal_page():
    return portal.page(await release())


@app.get("/static/{name}")
async def static_file(name: str):
    fname, src = portal.bundle(await release())
    if name != fname:
        raise HTTPException(404, "Not Found")
    return PlainTextResponse(src, media_type="application/javascript")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "release": RELEASE_VERSIONS[await release()]}


# ---------------------------------------------------------------- ops (out of HAWK's scope; the gateway blocks /__ops)
def require_ops(token: str) -> None:
    if not hmac.compare_digest(token, OPS_TOKEN):
        raise HTTPException(403, "ops token required")


class ReleaseIn(BaseModel):
    release: int = Field(ge=1, le=2)


@app.post("/__ops/release")
async def ops_release(body: ReleaseIn, x_ops_token: str = Header(default="")):
    require_ops(x_ops_token)
    async with Session() as s:
        (await s.get(Counter, "release")).value = body.release
        await s.commit()
    event = {"service": "voyage", "environment": "staging", "version": RELEASE_VERSIONS[body.release],
             "commit": hashlib.sha1(f"voyage-r{body.release}".encode()).hexdigest()[:12],
             "deployed_at": datetime.now(UTC).isoformat(), "base_url_hint": "voyage"}
    delivered = False
    if WEBHOOK_URL:
        raw = json.dumps(event).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(WEBHOOK_URL, content=raw, headers={"Content-Type": "application/json",
                                                                    "X-Hawk-Signature": f"sha256={sig}"})
                delivered = r.status_code < 300
        except httpx.HTTPError:
            delivered = False
    return {"event": event, "webhook_delivered": delivered}


class FixIn(BaseModel):
    flaw: str
    fixed: bool = True


@app.post("/__ops/fix")
async def ops_fix(body: FixIn, x_ops_token: str = Header(default="")):
    require_ops(x_ops_token)
    async with Session() as s:
        row = await s.get(Counter, f"fix:{body.flaw}")
        if row:
            row.value = int(body.fixed)
        else:
            s.add(Counter(name=f"fix:{body.flaw}", value=int(body.fixed)))
        await s.commit()
    return {"flaw": body.flaw, "fixed": body.fixed}


class StrainIn(BaseModel):
    latency_ms: int = Field(ge=0, le=5000)


@app.post("/__ops/strain")
async def ops_strain(body: StrainIn, x_ops_token: str = Header(default="")):
    require_ops(x_ops_token)
    STATE["strain_ms"] = body.latency_ms
    return {"strain_ms": body.latency_ms}


@app.post("/__ops/reset")
async def ops_reset(x_ops_token: str = Header(default="")):
    require_ops(x_ops_token)
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(delete(table))
    await seed()
    STATE["strain_ms"] = 0
    return {"reset": True}
