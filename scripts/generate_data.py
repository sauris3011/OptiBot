"""Generate the synthetic eCommerce dataset OptiBot runs against.

Deterministic (fixed seed) so baseline and optimized runs are compared against
byte-identical data. Writes JSON into backend/app/data/.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20260729
TODAY = datetime(2026, 7, 29)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "backend" / "app" / "data"

FIRST_NAMES = [
    "Alice", "Marcus", "Priya", "Diego", "Yuki", "Fatima", "Ethan", "Nadia",
    "Owen", "Sofia", "Liam", "Amara", "Noah", "Elena", "Jonas", "Mei",
    "Rafael", "Zoe", "Hassan", "Clara", "Theo", "Ines", "Kwame", "Lena",
    "Arjun", "Maya",
]
LAST_NAMES = [
    "Chen", "Okafor", "Nguyen", "Silva", "Tanaka", "Haddad", "Brooks", "Petrov",
    "Kelly", "Moreau", "Osei", "Lindqvist", "Rivera", "Novak", "Ibrahim",
    "Watanabe", "Costa", "Dubois", "Mensah", "Farrell", "Bauer", "Rossi",
]
CITIES = [
    ("San Francisco", "CA"), ("Portland", "OR"), ("Seattle", "WA"),
    ("Reno", "NV"), ("Phoenix", "AZ"), ("Austin", "TX"), ("Chicago", "IL"),
    ("Denver", "CO"), ("Boston", "MA"), ("Atlanta", "GA"), ("Miami", "FL"),
    ("New York", "NY"), ("Columbus", "OH"), ("Nashville", "TN"),
    ("Anchorage", "AK"), ("Honolulu", "HI"),
]
STREETS = [
    "Maple Ave", "Cedar St", "Birch Ln", "Willow Way", "Juniper Dr",
    "Aspen Ct", "Sycamore Blvd", "Poplar Rd", "Chestnut Pl", "Hawthorn Ter",
]

PRODUCT_CATALOG = [
    ("Electronics", "Wireless Noise-Cancelling Headphones", 179.99),
    ("Electronics", "Mechanical Keyboard (Tactile)", 129.00),
    ("Electronics", "27-inch 4K Monitor", 349.99),
    ("Electronics", "USB-C Docking Station", 89.50),
    ("Electronics", "Portable SSD 1TB", 114.99),
    ("Electronics", "Smart Doorbell Camera", 159.00),
    ("Electronics", "Bluetooth Speaker (Waterproof)", 64.99),
    ("Electronics", "Ergonomic Vertical Mouse", 42.00),
    ("Clothing", "Merino Wool Crew Sweater", 98.00),
    ("Clothing", "Waterproof Hiking Jacket", 189.00),
    ("Clothing", "Selvedge Denim Jeans", 138.00),
    ("Clothing", "Cotton Oxford Shirt", 68.00),
    ("Clothing", "Running Shoes (Neutral)", 132.00),
    ("Clothing", "Packable Down Vest", 110.00),
    ("Clothing", "Leather Chelsea Boots", 215.00),
    ("Home", "Cast Iron Dutch Oven 5.5qt", 145.00),
    ("Home", "Linen Duvet Cover (Queen)", 175.00),
    ("Home", "Air Purifier (Medium Room)", 219.99),
    ("Home", "Ceramic Pour-Over Coffee Set", 54.00),
    ("Home", "Standing Desk Converter", 245.00),
    ("Home", "Wool Area Rug 5x7", 289.00),
    ("Home", "Blackout Curtains (Pair)", 72.00),
    ("Books", "The Pragmatic Programmer", 44.99),
    ("Books", "Designing Data-Intensive Applications", 54.99),
    ("Books", "A Short History of Nearly Everything", 19.99),
    ("Books", "Atlas of Remote Islands", 28.00),
    ("Books", "The Making of Prince of Persia", 32.00),
    ("Books", "Thinking in Systems", 24.50),
    ("Books", "Seeing Like a State", 29.95),
    ("Books", "The Soul of a New Machine", 21.00),
]

# Status distribution mirrors a real support queue: most questions are about
# orders that are in motion, not ones that are long settled.
STATUS_WEIGHTS = [
    ("Processing", 15),
    ("Shipped", 20),
    ("In Transit", 25),
    ("Delivered", 30),
    ("Returned", 5),
    ("Cancelled", 5),
]

CARRIERS = ["FedEx", "UPS", "USPS"]
CARRIER_PREFIX = {"FedEx": "FX", "UPS": "1Z", "USPS": "94"}

SHIPPING_METHODS = [
    ("Standard", 5, 7),
    ("Express", 2, 3),
    ("Overnight", 1, 1),
]

TRANSIT_HUBS = [
    ("Memphis", "TN"), ("Louisville", "KY"), ("Chicago", "IL"),
    ("Dallas", "TX"), ("Salt Lake City", "UT"), ("Newark", "NJ"),
]


def business_days_after(start: datetime, days: int) -> datetime:
    """Add N business days, skipping weekends."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def date_only(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def build_products(rng: random.Random) -> list[dict]:
    products = []
    for i, (category, name, price) in enumerate(PRODUCT_CATALOG, start=1):
        products.append(
            {
                "product_id": f"PROD-{i:03d}",
                "name": name,
                "category": category,
                "price": price,
                "description": f"{name} — {category.lower()} item sold by ShopFast.",
                "final_sale": category == "Books" and i % 7 == 0,
            }
        )
    return products


def build_customers(rng: random.Random, count: int) -> list[dict]:
    customers = []
    used = set()
    for i in range(1, count + 1):
        while True:
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            if (first, last) not in used:
                used.add((first, last))
                break
        city, state = rng.choice(CITIES)
        customers.append(
            {
                "customer_id": f"CUST-{i:03d}",
                "name": f"{first} {last}",
                # Synthetic PII on purpose — the guardrail layer must mask it.
                "email": f"{first.lower()}.{last.lower()}@example.com",
                "phone": f"555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
                "address": {
                    "street": f"{rng.randint(12, 9880)} {rng.choice(STREETS)}",
                    "city": city,
                    "state": state,
                    "zip": f"{rng.randint(10000, 99999)}",
                },
            }
        )
    return customers


def weighted_status(rng: random.Random) -> str:
    population = [s for s, _ in STATUS_WEIGHTS]
    weights = [w for _, w in STATUS_WEIGHTS]
    return rng.choices(population, weights=weights, k=1)[0]


def build_tracking_events(
    rng: random.Random,
    status: str,
    ship_date: datetime,
    delivery_date: datetime,
    origin: tuple[str, str],
    destination: dict,
) -> list[dict]:
    """Realistic carrier scan sequence, truncated at the current status."""
    events = [
        {
            "status": "Label Created",
            "timestamp": iso(ship_date.replace(hour=8, minute=0, second=0)),
            "location": f"{origin[0]}, {origin[1]}",
        }
    ]
    if status == "Shipped":
        return events

    events.append(
        {
            "status": "Picked Up",
            "timestamp": iso(ship_date.replace(hour=14, minute=30, second=0)),
            "location": f"{origin[0]}, {origin[1]}",
        }
    )

    span = max((delivery_date - ship_date).days, 1)
    for step in range(1, min(span, 3)):
        hub = rng.choice(TRANSIT_HUBS)
        events.append(
            {
                "status": "In Transit",
                "timestamp": iso(
                    (ship_date + timedelta(days=step)).replace(
                        hour=rng.randint(3, 20), minute=rng.choice([0, 15, 30, 45]), second=0
                    )
                ),
                "location": f"{hub[0]}, {hub[1]}",
            }
        )

    if status == "In Transit":
        return events

    # Delivered / Returned both passed through delivery.
    events.append(
        {
            "status": "Out for Delivery",
            "timestamp": iso(delivery_date.replace(hour=7, minute=10, second=0)),
            "location": f"{destination['city']}, {destination['state']}",
        }
    )
    events.append(
        {
            "status": "Delivered",
            "timestamp": iso(delivery_date.replace(hour=rng.randint(11, 18), minute=0, second=0)),
            "location": f"{destination['city']}, {destination['state']}",
        }
    )

    if status == "Returned":
        return_scan = delivery_date + timedelta(days=rng.randint(2, 9))
        events.append(
            {
                "status": "Return In Transit",
                "timestamp": iso(return_scan.replace(hour=12, minute=0, second=0)),
                "location": f"{destination['city']}, {destination['state']}",
            }
        )
        events.append(
            {
                "status": "Return Received",
                "timestamp": iso((return_scan + timedelta(days=2)).replace(hour=9, minute=0, second=0)),
                "location": f"{origin[0]}, {origin[1]}",
            }
        )
    return events


def build_orders_and_shipments(
    rng: random.Random, customers: list[dict], products: list[dict], count: int
):
    orders: list[dict] = []
    shipments: list[dict] = []

    for i in range(1, count + 1):
        order_id = f"ORD-{10000 + i}"
        customer = rng.choice(customers)
        status = weighted_status(rng)

        # Order placed somewhere in the last 60 days.
        days_ago = rng.randint(1, 60)
        order_date = TODAY - timedelta(days=days_ago)

        method, lo_days, hi_days = rng.choice(SHIPPING_METHODS)
        n_items = rng.choices([1, 2, 3], weights=[60, 30, 10], k=1)[0]
        chosen = rng.sample(products, n_items)

        items = []
        subtotal = 0.0
        for product in chosen:
            qty = rng.choices([1, 2], weights=[85, 15], k=1)[0]
            line = round(product["price"] * qty, 2)
            subtotal += line
            items.append(
                {
                    "product_id": product["product_id"],
                    "name": product["name"],
                    "category": product["category"],
                    "quantity": qty,
                    "unit_price": product["price"],
                    "line_total": line,
                }
            )

        shipping_cost = 0.0
        if method == "Standard":
            shipping_cost = 0.0 if subtotal > 35 else 4.99
        elif method == "Express":
            shipping_cost = 12.99
        else:
            shipping_cost = 24.99
        total = round(subtotal + shipping_cost, 2)

        order: dict = {
            "order_id": order_id,
            "customer_id": customer["customer_id"],
            "status": status,
            "items": items,
            "subtotal": round(subtotal, 2),
            "shipping_cost": shipping_cost,
            "total": total,
            "order_date": date_only(order_date),
            "shipping_method": method,
            "shipping_address": customer["address"],
        }

        if status in ("Processing", "Cancelled"):
            order["estimated_delivery"] = date_only(
                business_days_after(order_date + timedelta(days=1), hi_days)
            )
            order["tracking_number"] = None
            order["carrier"] = None
            if status == "Cancelled":
                order["cancelled_date"] = date_only(order_date + timedelta(days=rng.randint(0, 2)))
            orders.append(order)
            continue

        ship_date = order_date + timedelta(days=1)
        transit = rng.randint(lo_days, hi_days)
        # Alaska and Hawaii add 3 business days, per the shipping policy.
        if customer["address"]["state"] in ("AK", "HI"):
            transit += 3
        estimated_delivery = business_days_after(ship_date, transit)

        carrier = rng.choice(CARRIERS)
        tracking = f"{CARRIER_PREFIX[carrier]}{rng.randint(10**9, 10**10 - 1)}"

        order["carrier"] = carrier
        order["tracking_number"] = tracking
        order["ship_date"] = date_only(ship_date)
        order["estimated_delivery"] = date_only(estimated_delivery)

        if status == "Delivered":
            actual = estimated_delivery - timedelta(days=rng.choice([0, 0, 1]))
            if actual > TODAY:
                actual = TODAY - timedelta(days=1)
            order["delivered_date"] = date_only(actual)
            delivery_for_events = actual
        elif status == "Returned":
            actual = estimated_delivery - timedelta(days=rng.choice([0, 1]))
            if actual > TODAY - timedelta(days=5):
                actual = TODAY - timedelta(days=rng.randint(6, 20))
            order["delivered_date"] = date_only(actual)
            order["return_status"] = rng.choice(
                ["Return Received", "Refund Processing", "Refunded"]
            )
            delivery_for_events = actual
        else:
            delivery_for_events = estimated_delivery

        origin = ("Reno", "NV") if customer["address"]["state"] in (
            "CA", "OR", "WA", "NV", "AZ"
        ) else ("Columbus", "OH")

        shipments.append(
            {
                "shipment_id": f"SHIP-{10000 + i}",
                "order_id": order_id,
                "carrier": carrier,
                "tracking_number": tracking,
                "events": build_tracking_events(
                    rng, status, ship_date, delivery_for_events, origin,
                    customer["address"],
                ),
            }
        )
        orders.append(order)

    return orders, shipments


def main() -> None:
    rng = random.Random(SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    products = build_products(rng)
    customers = build_customers(rng, 50)
    orders, shipments = build_orders_and_shipments(rng, customers, products, 200)

    outputs = {
        "products.json": products,
        "customers.json": customers,
        "orders.json": orders,
        "shipments.json": shipments,
    }
    for filename, payload in outputs.items():
        path = DATA_DIR / filename
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {len(payload):>4} records -> {path.relative_to(ROOT)}")

    counts: dict[str, int] = {}
    for order in orders:
        counts[order["status"]] = counts.get(order["status"], 0) + 1
    print("\nstatus distribution:")
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<12} {n:>3}")


if __name__ == "__main__":
    main()
