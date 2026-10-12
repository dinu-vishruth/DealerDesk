from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import zoneinfo
from django.db import IntegrityError, transaction
from django.utils import timezone
from core.models import Dealer, Product
from core.exceptions import APIError
from .models import Order, OrderItem, DailyOrderSequence

KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
GST_RATE = Decimal("0.18")
CENT = Decimal("0.01")


def generate_order_number() -> str:
    """
    Generates unique order number in format ORD-YYYYMMDD-NNNN
    using Asia/Kolkata timezone with concurrency-safe atomic sequence.
    """
    now_ist = timezone.now().astimezone(KOLKATA_TZ)
    today = now_ist.date()
    date_str = today.strftime("%Y%m%d")

    seq_obj, _ = DailyOrderSequence.objects.select_for_update().get_or_create(
        date=today,
        defaults={"last_seq": 0},
    )
    seq_obj.last_seq += 1
    seq_obj.save(update_fields=["last_seq"])

    return f"ORD-{date_str}-{seq_obj.last_seq:04d}"


def price_line(unit_price: Decimal, quantity: int):
    line_subtotal = (Decimal(unit_price) * Decimal(quantity)).quantize(CENT)
    # 18% GST rounded ROUND_HALF_UP per line to 2 decimals
    line_tax = (line_subtotal * GST_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    return line_subtotal, line_tax, line_subtotal + line_tax


def get_order_or_404(order_number: str) -> Order:
    try:
        return Order.objects.get(order_number=order_number)
    except Order.DoesNotExist:
        raise APIError(f"Order '{order_number}' not found.", code="NOT_FOUND", status_code=404)


def validate_order_payload(dealer_id, items_data):
    """Contract §7.1 rules. Returns (dealer, validated_lines)."""
    if not dealer_id:
        raise APIError("dealer_id is required.", code="VALIDATION_ERROR", status_code=400)

    try:
        dealer = Dealer.objects.get(id=dealer_id)
    except (Dealer.DoesNotExist, ValueError, TypeError):
        raise APIError(f"Dealer with id {dealer_id} does not exist.", code="VALIDATION_ERROR", status_code=400)

    if not isinstance(items_data, list) or len(items_data) == 0:
        raise APIError("items must be a non-empty list.", code="VALIDATION_ERROR", status_code=400)

    seen_skus = set()
    validated_lines = []

    for item in items_data:
        if not isinstance(item, dict):
            raise APIError("Each item must be an object.", code="VALIDATION_ERROR", status_code=400)

        sku = item.get("sku")
        if not sku or not isinstance(sku, str):
            raise APIError("Item sku is required.", code="VALIDATION_ERROR", status_code=400)

        sku = sku.strip()
        if sku in seen_skus:
            raise APIError(f"Duplicate SKU '{sku}' in order items.", code="VALIDATION_ERROR", status_code=400)
        seen_skus.add(sku)

        quantity = item.get("quantity")
        if quantity is None or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise APIError(
                f"Quantity for SKU '{sku}' must be an integer >= 1.",
                code="VALIDATION_ERROR",
                status_code=400,
            )

        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            raise APIError(f"Product with SKU '{sku}' does not exist.", code="VALIDATION_ERROR", status_code=400)

        unit_price = Decimal(str(product.price))
        line_subtotal, line_tax, line_total = price_line(unit_price, quantity)

        validated_lines.append({
            "product": product,
            "sku": sku,
            "name": product.name,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_subtotal": line_subtotal,
            "line_tax": line_tax,
            "line_total": line_total,
        })

    return dealer, validated_lines


def build_order(order: Order, validated_lines) -> Order:
    order.subtotal = sum(l["line_subtotal"] for l in validated_lines)
    order.tax_total = sum(l["line_tax"] for l in validated_lines)
    order.grand_total = order.subtotal + order.tax_total
    order.save()

    OrderItem.objects.bulk_create([
        OrderItem(
            order=order,
            product=l["product"],
            sku=l["sku"],
            name=l["name"],
            quantity=l["quantity"],
            unit_price=l["unit_price"],
            line_subtotal=l["line_subtotal"],
            line_tax=l["line_tax"],
            line_total=l["line_total"],
        )
        for l in validated_lines
    ])
    return order


def create_draft_order_service(dealer_id: int, items_data: list) -> Order:
    """
    Validated service to create a draft order.
    Follows contract §7.1 and §7.4 rules.
    """
    dealer, validated_lines = validate_order_payload(dealer_id, items_data)

    with transaction.atomic():
        order = Order.objects.create(
            order_number=generate_order_number(),
            dealer=dealer,
            status="DRAFT",
            subtotal=Decimal("0.00"),
            tax_total=Decimal("0.00"),
            grand_total=Decimal("0.00"),
        )
        build_order(order, validated_lines)

    return order


def canonical_request_hash(dealer_id, items_data) -> str:
    """Stable fingerprint of an order body for Idempotency-Key reuse (§7.5)."""
    normalized_items = []
    for item in items_data if isinstance(items_data, list) else []:
        if isinstance(item, dict):
            normalized_items.append({"sku": item.get("sku"), "quantity": item.get("quantity")})
    payload = json.dumps(
        {"dealer_id": dealer_id, "items": normalized_items},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_order_idempotent(dealer_id, items_data, idempotency_key):
    """Returns (order, created). Same key + same body replays the original order."""
    if not idempotency_key:
        return create_draft_order_service(dealer_id, items_data), True

    request_hash = canonical_request_hash(dealer_id, items_data)

    def match(existing):
        if existing.idempotency_hash != request_hash:
            raise APIError(
                "Idempotency-Key was already used with a different request body.",
                code="IDEMPOTENCY_CONFLICT",
                status_code=409,
            )
        return existing, False

    existing = Order.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return match(existing)

    try:
        with transaction.atomic():
            order = create_draft_order_service(dealer_id, items_data)
            Order.objects.filter(pk=order.pk).update(
                idempotency_key=idempotency_key,
                idempotency_hash=request_hash,
            )
            order.idempotency_key = idempotency_key
            order.idempotency_hash = request_hash
    except IntegrityError:
        # Another concurrent request with this key won the unique index; its
        # transaction has committed by now, so read it back.
        order = Order.objects.filter(idempotency_key=idempotency_key).first()
        if order is None:
            raise
        return match(order)

    return order, True


def refresh_draft_pricing(order: Order) -> Order:
    """§7.4: while an order is DRAFT its unit_price is the product's current price."""
    if order.status != "DRAFT":
        return order

    items = list(OrderItem.objects.filter(order_id=order.pk).select_related("product"))
    stale = [
        i for i in items
        if i.unit_price != Decimal(str(i.product.price))
    ]
    if not stale:
        return order

    for item in stale:
        item.unit_price = Decimal(str(item.product.price))
        item.line_subtotal, item.line_tax, item.line_total = price_line(item.unit_price, item.quantity)
        item.save(update_fields=["unit_price", "line_subtotal", "line_tax", "line_total"])

    order.subtotal = sum(i.line_subtotal for i in items)
    order.tax_total = sum(i.line_tax for i in items)
    order.grand_total = order.subtotal + order.tax_total
    order.save(update_fields=["subtotal", "tax_total", "grand_total"])
    return order


def _lock_order(order_number: str) -> Order:
    return Order.objects.select_for_update().get(pk=get_order_or_404(order_number).pk)


def _require_transition(order: Order, allowed_from: tuple, action: str) -> None:
    if order.status not in allowed_from:
        raise APIError(
            f"Cannot {action} an order with status {order.status}.",
            code="INVALID_TRANSITION",
            status_code=409,
        )


def confirm_order(order_number: str) -> Order:
    """DRAFT -> CONFIRMED. All-or-nothing stock deduction (§7.2, §7.3)."""
    with transaction.atomic():
        order = _lock_order(order_number)
        _require_transition(order, ("DRAFT",), "confirm")

        items = list(OrderItem.objects.filter(order_id=order.pk).select_related("product"))

        # Lock the product rows in a stable order so simultaneous confirms of
        # different orders cannot deadlock and never oversell.
        products = {
            p.pk: p
            for p in Product.objects.select_for_update().filter(
                pk__in=[i.product_id for i in items]
            ).order_by("pk")
        }

        shortages = []
        for item in items:
            product = products[item.product_id]
            # §7.4: the catalogue price at confirm time is the locked price.
            item.unit_price = Decimal(str(product.price))
            item.line_subtotal, item.line_tax, item.line_total = price_line(item.unit_price, item.quantity)
            if item.quantity > product.stock:
                shortages.append({
                    "sku": item.sku,
                    "requested": item.quantity,
                    "available": product.stock,
                    "name": product.name,
                })

        if shortages:
            raise APIError(
                " ".join(
                    f"Insufficient stock for {s['name']}. Available: {s['available']}, Requested: {s['requested']}"
                    for s in shortages
                ),
                code="INSUFFICIENT_STOCK",
                details=[
                    {"sku": s["sku"], "requested": s["requested"], "available": s["available"]}
                    for s in shortages
                ],
                status_code=409,
            )

        for item in items:
            products[item.product_id].stock -= item.quantity
            products[item.product_id].save(update_fields=["stock"])
            item.save(update_fields=["unit_price", "line_subtotal", "line_tax", "line_total"])

        order.subtotal = sum(i.line_subtotal for i in items)
        order.tax_total = sum(i.line_tax for i in items)
        order.grand_total = order.subtotal + order.tax_total
        order.status = "CONFIRMED"
        order.save(update_fields=["subtotal", "tax_total", "grand_total", "status", "updated_at"])

    return order


def _advance(order_number: str, allowed_from: tuple, action: str, new_status: str) -> Order:
    with transaction.atomic():
        order = _lock_order(order_number)
        _require_transition(order, allowed_from, action)
        order.status = new_status
        order.save(update_fields=["status", "updated_at"])
    return order


def dispatch_order(order_number: str) -> Order:
    """CONFIRMED -> DISPATCHED (§7.2)."""
    return _advance(order_number, ("CONFIRMED",), "dispatch", "DISPATCHED")


def deliver_order(order_number: str) -> Order:
    """DISPATCHED -> DELIVERED (§7.2)."""
    return _advance(order_number, ("DISPATCHED",), "deliver", "DELIVERED")


def cancel_order(order_number: str) -> Order:
    """DRAFT -> CANCELLED, or CONFIRMED -> CANCELLED restoring stock exactly once (§7.3)."""
    with transaction.atomic():
        order = _lock_order(order_number)
        _require_transition(order, ("DRAFT", "CONFIRMED"), "cancel")

        restores_stock = order.status == "CONFIRMED"
        items = list(OrderItem.objects.filter(order_id=order.pk)) if restores_stock else []

        if restores_stock:
            products = {
                p.pk: p
                for p in Product.objects.select_for_update().filter(
                    pk__in=[i.product_id for i in items]
                ).order_by("pk")
            }
            for item in items:
                products[item.product_id].stock += item.quantity
                products[item.product_id].save(update_fields=["stock"])

        order.status = "CANCELLED"
        order.save(update_fields=["status", "updated_at"])

    return order


def replace_order_items(order_number: str, items_data: list) -> Order:
    """PUT /api/orders/{order_number}/ — replaces all items while DRAFT (§7.1, §7.2)."""
    order = get_order_or_404(order_number)
    if order.status != "DRAFT":
        raise APIError(
            f"Order '{order.order_number}' is {order.status} and can no longer be edited.",
            code="ORDER_LOCKED",
            status_code=409,
        )

    _, validated_lines = validate_order_payload(order.dealer_id, items_data)

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status != "DRAFT":
            raise APIError(
                f"Order '{locked.order_number}' is {locked.status} and can no longer be edited.",
                code="ORDER_LOCKED",
                status_code=409,
            )
        OrderItem.objects.filter(order_id=locked.pk).delete()
        locked.status = "DRAFT"
        build_order(locked, validated_lines)

    return locked
