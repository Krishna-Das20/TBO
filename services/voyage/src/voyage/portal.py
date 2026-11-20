"""The agency web portal: a small page and a hashed JS bundle that calls the v3 API.

HAWK's recon reads this bundle for API routes, the way it would read a real front end. Release 2
ships a new bundle (new hash) that adds the invoice download.
"""
from __future__ import annotations

import hashlib

BUNDLE_R1 = r"""
// voyage agency portal (build r1)
const API = "/api/v3";
let token = null;
async function call(method, path, body) {
  const r = await fetch(API + path, {method, headers: {"Content-Type": "application/json",
    ...(token ? {Authorization: "Bearer " + token} : {})}, body: body ? JSON.stringify(body) : undefined});
  return r.json();
}
export async function login(email, password) { const r = await call("POST", "/auth/login", {email, password}); token = r.token; return r; }
export const searchHotels = (city, checkin, nights, rooms) =>
  call("GET", `/hotels/search?city=${city}&checkin=${checkin}&nights=${nights}&rooms=${rooms}`);
export const holdBooking = (offer_id, travellers, price) => call("POST", "/bookings/hold", {offer_id, travellers, price});
export const payBooking = (id) => call("POST", `/bookings/${id}/pay`);
export const listBookings = () => call("GET", "/bookings");
export const getBooking = (id) => call("GET", `/bookings/${id}`);
export const cancelBooking = (id) => call("POST", `/bookings/${id}/cancel`);
export const wallet = () => call("GET", "/wallet");
export const me = () => call("GET", "/users/me");
export const updateProfile = (patch) => call("PATCH", "/users/me", patch);
export const agencyUsers = () => call("GET", "/agency/users");
"""

BUNDLE_R2 = BUNDLE_R1.replace("(build r1)", "(build r2)") + r"""
export const downloadInvoice = (id) => call("GET", `/bookings/${id}/invoice`);
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Voyage Agency Portal</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui;background:#FBFAF7;color:#1E2130;padding:40px">
<h1>Voyage Agency Portal</h1>
<p>Sandbox B2B travel platform for HAWK testing. Synthetic data only.</p>
<script type="module" src="/static/{bundle}"></script>
</body></html>"""


def bundle(release: int) -> tuple[str, str]:
    """Return (file name, source) of the bundle for a release."""
    src = BUNDLE_R2 if release >= 2 else BUNDLE_R1
    digest = hashlib.sha256(src.encode()).hexdigest()[:10]
    return f"app.{digest}.js", src


def page(release: int) -> str:
    return PAGE.replace("{bundle}", bundle(release)[0])
