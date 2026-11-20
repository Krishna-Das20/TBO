"""Every planted flaw reproduces, and every decoy stays safe. If this fails, HAWK's ground truth is wrong."""
import asyncio
import os
import tempfile

os.environ["VOYAGE_DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db").replace("\\", "/")
os.environ["VOYAGE_ACCESS_LOG"] = ""

import httpx
import pytest
from voyage.app import app
from voyage.seed import seed

PW = {"sub@skyline.voyage.test": "Skyline-Sub-2026!", "sub@coral.voyage.test": "Coral-Sub-2026!",
      "admin@skyline.voyage.test": "Skyline-Admin-2026!"}


@pytest.fixture
async def client():
    await seed()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://voyage") as c:
        yield c


async def auth(c, email):
    r = await c.post("/api/v3/auth/login", json={"email": email, "password": PW[email]})
    return {"Authorization": "Bearer " + r.json()["token"]}


async def test_idor_and_decoys(client):
    a = await auth(client, "sub@skyline.voyage.test")
    r = await client.get("/api/v3/bookings/BK-10003", headers=a)  # Coral's booking
    assert r.status_code == 200 and r.json()["agency_id"] == "AG-2002"
    assert (await client.post("/api/v3/bookings/BK-10003/cancel", headers=a)).status_code == 404
    assert (await client.post("/api/v3/bookings/BK-10003/pay", headers=a)).status_code == 404


async def test_fare_tamper(client):
    a = await auth(client, "sub@skyline.voyage.test")
    r = await client.post("/api/v3/bookings/hold", headers=a, json={
        "offer_id": "OF~HT-DXB-01~2026-12-10~3~1", "travellers": [{"name": "X", "passport": "T0"}], "price": 1})
    assert r.json()["amount"] == 1


async def test_refund_race(client):
    a = await auth(client, "admin@skyline.voyage.test")
    bid = (await client.post("/api/v3/bookings/hold", headers=a, json={
        "offer_id": "OF~HT-GOA-02~2026-12-10~1~1", "travellers": [{"name": "X", "passport": "T0"}]})).json()["booking_id"]
    before = (await client.post(f"/api/v3/bookings/{bid}/pay", headers=a)).json()["wallet_balance"]
    rs = await asyncio.gather(*[client.post(f"/api/v3/bookings/{bid}/cancel", headers=a) for _ in range(2)])
    assert [r.status_code for r in rs] == [200, 200]
    after = (await client.get("/api/v3/wallet", headers=a)).json()["balance"]
    assert after - before == 2 * 4200


async def test_mass_assignment(client):
    c = await auth(client, "sub@coral.voyage.test")
    assert (await client.get("/api/v3/agency/users", headers=c)).status_code == 403
    await client.patch("/api/v3/users/me", headers=c, json={"role": "agency_admin"})
    assert (await client.get("/api/v3/agency/users", headers=c)).status_code == 200


async def test_zombie_shadow_and_retired(client):
    assert (await client.get("/api/v1/bookings/BK-10001")).status_code == 200
    assert (await client.get("/api/v2/bookings/BK-10001")).status_code == 410
    a = await auth(client, "sub@skyline.voyage.test")
    assert "net_cost" in (await client.get("/api/v3/partner/rates?city=Goa", headers=a)).json()["rates"][0]
    assert (await client.get("/api/v3/bookings/BK-10003/invoice", headers=a)).status_code == 404  # release 1
