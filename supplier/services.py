from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import time

import requests
from django.db import transaction

from core.models import Product

PAGE_LIMIT = 20
MAX_ATTEMPTS = 5
REQUEST_TIMEOUT = 15
# The endpoint must answer within 120 s (§8.2); leave headroom for the apply step.
TIME_BUDGET = 100

MONEY_2DP = Decimal("0.01")
SYNC_FIELDS = ("name", "category", "brand", "vehicle_fitment", "description")


class SupplierUnavailable(Exception):
    """A page could not be fetched within its attempt budget, or the key was rejected."""


def _retry_after(response, attempt):
    raw = response.headers.get("Retry-After")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _backoff(attempt)


def _backoff(attempt):
    return min(0.5 * (2 ** (attempt - 1)), 4.0)


def fetch_feed(base_url, api_key, deadline=None):
    """Follow next_cursor until it is null (§8.3.1), retrying per §8.3.2.

    Returns (items, pages_fetched, requests_made). Raises SupplierUnavailable if any
    request still fails after 5 attempts, so the caller can change nothing at all.
    """
    url = base_url.rstrip("/") + "/v1/products"
    if deadline is None:
        deadline = time.monotonic() + TIME_BUDGET

    session = requests.Session()
    items = []
    pages_fetched = 0
    requests_made = 0
    cursor = None

    while True:
        params = {"limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor

        attempt = 0
        while True:
            attempt += 1
            requests_made += 1
            try:
                response = session.get(
                    url,
                    params=params,
                    headers={"X-Api-Key": api_key},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException:
                response = None

            if response is not None and response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    raise SupplierUnavailable("supplier returned a malformed page")
                items.extend(payload.get("items") or [])
                pages_fetched += 1
                cursor = payload.get("next_cursor")
                break

            if response is not None and response.status_code == 401:
                raise SupplierUnavailable("supplier rejected the API key")

            if attempt >= MAX_ATTEMPTS:
                detail = f"HTTP {response.status_code}" if response is not None else "connection error"
                raise SupplierUnavailable(f"supplier request failed after {MAX_ATTEMPTS} attempts ({detail})")
            if time.monotonic() >= deadline:
                raise SupplierUnavailable("supplier sync exceeded its time budget")

            if response is not None and response.status_code == 429:
                wait = _retry_after(response, attempt)
            else:
                wait = _backoff(attempt)
            time.sleep(max(0.0, min(wait, deadline - time.monotonic())))

        if not cursor:
            return items, pages_fetched, requests_made


def parse_price(value):
    """Positive amount with at most 2 decimals, else None (§8.3.4)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        return None
    return amount.quantize(MONEY_2DP)


def _text(value):
    return value if isinstance(value, str) else ("" if value is None else str(value))


def validate_items(items):
    """Split the raw feed into (normalised valid items, rejected list)."""
    valid = []
    rejected = []

    for item in items:
        if not isinstance(item, dict):
            rejected.append({"supplier_sku": None, "reason": "item is not an object"})
            continue

        supplier_sku = item.get("supplier_sku")
        sku = item.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            rejected.append({"supplier_sku": supplier_sku, "reason": "missing sku"})
            continue

        price = parse_price(item.get("price"))
        if price is None:
            rejected.append({"supplier_sku": supplier_sku, "reason": "invalid price"})
            continue

        valid.append({
            "sku": sku.strip(),
            "supplier_sku": supplier_sku,
            "name": _text(item.get("name")),
            "category": _text(item.get("category")),
            "brand": _text(item.get("brand")),
            "vehicle_fitment": _text(item.get("vehicle_fitment")),
            "description": _text(item.get("description")),
            "price": price,
            "updated_at": item.get("updated_at"),
        })

    return valid, rejected


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return datetime.min.replace(tzinfo=timezone.utc)
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def latest_per_sku(valid_items):
    """Duplicates across pages: keep the newest updated_at (§8.3.5)."""
    newest = {}
    for item in valid_items:
        current = newest.get(item["sku"])
        if current is None or _parse_timestamp(item["updated_at"]) > _parse_timestamp(current["updated_at"]):
            newest[item["sku"]] = item
    return newest


def apply_items(by_sku):
    """Create or update the catalogue without ever touching our stock (§8.3.6, §8.3.7)."""
    created = 0
    updated = 0
    unchanged = 0

    with transaction.atomic():
        existing = {
            product.sku: product
            for product in Product.objects.select_for_update().filter(sku__in=list(by_sku)).order_by("sku")
        }

        for sku, item in by_sku.items():
            product = existing.get(sku)
            if product is None:
                Product.objects.create(
                    sku=sku,
                    name=item["name"],
                    category=item["category"],
                    brand=item["brand"],
                    vehicle_fitment=item["vehicle_fitment"],
                    description=item["description"],
                    price=item["price"],
                    stock=0,
                )
                created += 1
                continue

            field_values = {name: item[name] for name in SYNC_FIELDS}
            field_values["price"] = item["price"]
            changed = {
                field: value
                for field, value in field_values.items()
                if getattr(product, field) != value
            }
            if changed:
                for field, value in changed.items():
                    setattr(product, field, value)
                product.save(update_fields=[*changed, "updated_at"])
                updated += 1
            else:
                unchanged += 1

    return created, updated, unchanged


def run_supplier_sync(base_url, api_key):
    items, pages_fetched, requests_made = fetch_feed(base_url, api_key)
    valid, rejected = validate_items(items)
    by_sku = latest_per_sku(valid)
    created, updated, unchanged = apply_items(by_sku)

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
        "pages_fetched": pages_fetched,
        "requests_made": requests_made,
    }
