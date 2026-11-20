from hawk.recon import canonical, js_path_to_template, routes_from_js
from hawk.traffic import templatize

BUNDLE = r'''
const API = "/api/v3";
export const holdBooking = (offer_id, travellers, price) => call("POST", "/bookings/hold", {offer_id, travellers, price});
export const payBooking = (id) => call("POST", `/bookings/${id}/pay`);
export const searchHotels = (city, n) => call("GET", `/hotels/search?city=${city}&nights=${n}`);
'''


def test_routes_from_js():
    routes = {f"{r['method']} {r['template']}": r for r in routes_from_js(BUNDLE)}
    assert routes["POST /api/v3/bookings/hold"]["body_fields"] == ["offer_id", "travellers", "price"]
    assert routes["POST /api/v3/bookings/{booking_id}/pay"]["path_params"] == ["booking_id"]
    assert routes["GET /api/v3/hotels/search"]["query_params"] == ["city", "nights"]


def test_canonical_forms_match_across_sources():
    assert canonical(js_path_to_template("/api/v3/bookings/${x}")[0]) == canonical("/api/v3/bookings/{booking_id}")
    assert templatize("/api/v1/bookings/BK-10003") == "/api/v1/bookings/{}"
