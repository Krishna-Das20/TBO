"""Ground truth: the flaws planted in Voyage. HAWK never reads this; tests and the demo scoreboard do."""

PLANTED = [
    {
        "id": "V-IDOR-BOOKING",
        "release": 1,
        "class": "access_control",
        "endpoint": "GET /api/v3/bookings/{booking_id}",
        "summary": "Any logged-in user can read another agency's booking, including traveller passports.",
    },
    {
        "id": "V-FARE-TAMPER",
        "release": 1,
        "class": "business_logic",
        "endpoint": "POST /api/v3/bookings/hold",
        "summary": "The hold trusts a client-supplied price instead of re-pricing the offer.",
    },
    {
        "id": "V-REFUND-RACE",
        "release": 1,
        "class": "business_logic",
        "endpoint": "POST /api/v3/bookings/{booking_id}/cancel",
        "summary": "Concurrent cancels both pass the status check and refund the wallet twice.",
    },
    {
        "id": "V-MASS-ASSIGN-ROLE",
        "release": 1,
        "class": "access_control",
        "endpoint": "PATCH /api/v3/users/me",
        "summary": "Profile update accepts a 'role' field, so a sub-agent can make itself agency admin.",
    },
    {
        "id": "V-ZOMBIE-V1",
        "release": 1,
        "class": "zombie_api",
        "endpoint": "GET /api/v1/bookings/{booking_id}",
        "summary": "The retired v1 booking lookup still answers, with no authentication at all.",
    },
    {
        "id": "V-SHADOW-RATES",
        "release": 1,
        "class": "shadow_api",
        "endpoint": "GET /api/v3/partner/rates",
        "summary": "An undocumented partner feed, used daily, shows supplier net cost to any logged-in sub-agent.",
    },
    {
        "id": "V-IDOR-INVOICE",
        "release": 2,
        "class": "access_control",
        "endpoint": "GET /api/v3/bookings/{booking_id}/invoice",
        "summary": "Shipped in release 2: the new invoice endpoint has no agency check (GSTIN, amounts).",
    },
]

# Deliberately correct endpoints that look similar to flawed ones. A precise scanner reports nothing here.
DECOYS = [
    "POST /api/v3/bookings/{booking_id}/cancel (cross-agency: checks ownership correctly)",
    "POST /api/v3/bookings/{booking_id}/pay (cross-agency: checks ownership correctly)",
    "GET /api/v2/bookings/{booking_id} (properly retired: 410 Gone)",
    "GET /api/v3/agency/users (admin-only: enforced from the database role)",
]
