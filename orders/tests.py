from django.test import TestCase, TransactionTestCase
from django.db import close_old_connections
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from core.models import Product, Dealer
from orders.models import Order, OrderItem
from orders.services import confirm_order


class OrderTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-ORD-1",
            name="Apex Motors",
            credit_limit=Decimal("100000.00"),
        )
        self.p1 = Product.objects.create(
            sku="BRK-001",
            name="Front Brake Pads",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("1000.55"),
            stock=50,
        )
        self.p2 = Product.objects.create(
            sku="CHN-001",
            name="Chain Lube",
            category="Chains",
            brand="Motul",
            vehicle_fitment="Universal",
            price=Decimal("450.00"),
            stock=100,
        )

    def test_create_draft_order_pricing_and_gst(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [
                {"sku": self.p1.sku, "quantity": 2},
                {"sku": self.p2.sku, "quantity": 1},
            ],
        }
        res = self.client.post("/api/orders/", payload, content_type="application/json")
        self.assertEqual(res.status_code, 201)
        data = res.json()

        self.assertEqual(data["status"], "DRAFT")
        self.assertTrue(data["order_number"].startswith("ORD-"))

        # Line 1: 1000.55 * 2 = 2001.10. 18% GST = 360.198 -> rounded 360.20. Line total = 2361.30
        item1 = next(it for it in data["items"] if it["sku"] == self.p1.sku)
        self.assertEqual(item1["unit_price"], "1000.55")
        self.assertEqual(item1["line_subtotal"], "2001.10")
        self.assertEqual(item1["line_tax"], "360.20")
        self.assertEqual(item1["line_total"], "2361.30")

        # Line 2: 450.00 * 1 = 450.00. 18% GST = 81.00. Line total = 531.00
        item2 = next(it for it in data["items"] if it["sku"] == self.p2.sku)
        self.assertEqual(item2["line_subtotal"], "450.00")
        self.assertEqual(item2["line_tax"], "81.00")
        self.assertEqual(item2["line_total"], "531.00")

        # Totals
        self.assertEqual(data["subtotal"], "2451.10")
        self.assertEqual(data["tax_total"], "441.20")
        self.assertEqual(data["grand_total"], "2892.30")

    def test_order_validations(self):
        # Empty items
        r1 = self.client.post("/api/orders/", {"dealer_id": self.dealer.id, "items": []}, content_type="application/json")
        self.assertEqual(r1.status_code, 400)
        self.assertEqual(r1.json()["error"]["code"], "VALIDATION_ERROR")

        # Non-existent dealer
        r2 = self.client.post("/api/orders/", {"dealer_id": 99999, "items": [{"sku": self.p1.sku, "quantity": 1}]}, content_type="application/json")
        self.assertEqual(r2.status_code, 400)

        # Duplicate SKU in items
        r3 = self.client.post("/api/orders/", {
            "dealer_id": self.dealer.id,
            "items": [
                {"sku": self.p1.sku, "quantity": 1},
                {"sku": self.p1.sku, "quantity": 2},
            ]
        }, content_type="application/json")
        self.assertEqual(r3.status_code, 400)

        # Non-existent SKU
        r4 = self.client.post("/api/orders/", {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "NON-EXISTENT-SKU", "quantity": 1}]
        }, content_type="application/json")
        self.assertEqual(r4.status_code, 400)


class OrderLifecycleTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(dealer_code="DLR-LC-1", name="Ring Road Motors")
        self.brake = Product.objects.create(
            sku="BRK-200", name="Front Brake Pads", category="Brakes", brand="Brembo",
            vehicle_fitment="KTM Duke 390", price=Decimal("1000.00"), stock=10,
        )
        self.chain = Product.objects.create(
            sku="CHN-200", name="Chain Kit", category="Chains", brand="RK",
            vehicle_fitment="Universal", price=Decimal("500.00"), stock=2,
        )

    def create(self, items):
        res = self.client.post(
            "/api/orders/",
            {"dealer_id": self.dealer.id, "items": items},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["order_number"]

    def action(self, number, verb):
        return self.client.post(f"/api/orders/{number}/{verb}/", content_type="application/json")

    def test_full_lifecycle_transitions(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 3}])

        confirmed = self.action(number, "confirm")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["status"], "CONFIRMED")

        dispatched = self.action(number, "dispatch")
        self.assertEqual(dispatched.status_code, 200, dispatched.content)
        self.assertEqual(dispatched.json()["status"], "DISPATCHED")

        delivered = self.action(number, "deliver")
        self.assertEqual(delivered.status_code, 200, delivered.content)
        self.assertEqual(delivered.json()["status"], "DELIVERED")

        # DELIVERED is final.
        again = self.action(number, "cancel")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["code"], "INVALID_TRANSITION")

        self.brake.refresh_from_db()
        self.assertEqual(self.brake.stock, 7)

    def test_dispatch_requires_confirmed(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 1}])
        res = self.action(number, "dispatch")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "INVALID_TRANSITION")
        self.brake.refresh_from_db()
        self.assertEqual(self.brake.stock, 10)

    def test_confirm_is_all_or_nothing_and_reports_every_short_line(self):
        number = self.create([
            {"sku": self.brake.sku, "quantity": 11},
            {"sku": self.chain.sku, "quantity": 5},
        ])
        res = self.action(number, "confirm")

        self.assertEqual(res.status_code, 409)
        error = res.json()["error"]
        self.assertEqual(error["code"], "INSUFFICIENT_STOCK")
        self.assertEqual(
            error["details"],
            [
                {"sku": self.brake.sku, "requested": 11, "available": 10},
                {"sku": self.chain.sku, "requested": 5, "available": 2},
            ],
        )
        self.assertIn("Insufficient stock for Front Brake Pads. Available: 10, Requested: 11", error["message"])
        self.assertIn("Insufficient stock for Chain Kit. Available: 2, Requested: 5", error["message"])

        # Nothing changed: no stock moved and the order is still a draft.
        self.brake.refresh_from_db()
        self.chain.refresh_from_db()
        self.assertEqual(self.brake.stock, 10)
        self.assertEqual(self.chain.stock, 2)
        self.assertEqual(Order.objects.get(order_number=number).status, "DRAFT")

    def test_confirm_locks_the_price_at_confirm_time(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 2}])
        self.brake.price = Decimal("1200.00")
        self.brake.save()

        draft = self.client.get(f"/api/orders/{number}/").json()
        self.assertEqual(draft["items"][0]["unit_price"], "1200.00")

        self.assertEqual(self.action(number, "confirm").status_code, 200)

        self.brake.price = Decimal("999.00")
        self.brake.save()

        confirmed = self.client.get(f"/api/orders/{number}/").json()
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["items"][0]["unit_price"], "1200.00")
        self.assertEqual(confirmed["items"][0]["line_subtotal"], "2400.00")
        self.assertEqual(confirmed["tax_total"], "432.00")
        self.assertEqual(confirmed["grand_total"], "2832.00")

    def test_cancel_confirmed_restores_stock_once(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 4}])
        self.action(number, "confirm")
        self.brake.refresh_from_db()
        self.assertEqual(self.brake.stock, 6)

        cancelled = self.action(number, "cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.brake.refresh_from_db()
        self.assertEqual(self.brake.stock, 10)

        replay = self.action(number, "cancel")
        self.assertEqual(replay.status_code, 409)
        self.brake.refresh_from_db()
        self.assertEqual(self.brake.stock, 10)

    def test_cancel_draft_leaves_stock_alone(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 4}])
        self.assertEqual(self.action(number, "cancel").status_code, 200)
        self.brake.refresh_from_db()
        self.assertEqual(self.brake.stock, 10)

    def test_edit_only_while_draft(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 1}])

        edited = self.client.put(
            f"/api/orders/{number}/",
            {"items": [{"sku": self.chain.sku, "quantity": 2}]},
            content_type="application/json",
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        body = edited.json()
        self.assertEqual([it["sku"] for it in body["items"]], [self.chain.sku])
        self.assertEqual(body["subtotal"], "1000.00")

        self.action(number, "confirm")
        locked = self.client.put(
            f"/api/orders/{number}/",
            {"items": [{"sku": self.brake.sku, "quantity": 1}]},
            content_type="application/json",
        )
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(locked.json()["error"]["code"], "ORDER_LOCKED")

    def test_edit_validates_like_create(self):
        number = self.create([{"sku": self.brake.sku, "quantity": 1}])
        res = self.client.put(
            f"/api/orders/{number}/",
            {"items": [{"sku": "NOPE-000", "quantity": 0}]},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            [it.sku for it in Order.objects.get(order_number=number).items.all()],
            [self.brake.sku],
        )


class OrderIdempotencyTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(dealer_code="DLR-IDEM-1", name="MG Road Spares")
        self.product = Product.objects.create(
            sku="FIL-300", name="Oil Filter", category="Filters", brand="Bosch",
            vehicle_fitment="Universal", price=Decimal("250.00"), stock=100,
        )
        self.body = {"dealer_id": self.dealer.id, "items": [{"sku": self.product.sku, "quantity": 2}]}

    def post(self, key):
        return self.client.post(
            "/api/orders/", self.body, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key
        )

    def test_same_key_same_body_returns_original(self):
        first = self.post("key-1")
        self.assertEqual(first.status_code, 201)
        replay = self.post("key-1")
        self.assertIn(replay.status_code, (200, 201))
        self.assertEqual(replay.json()["order_number"], first.json()["order_number"])
        self.assertEqual(Order.objects.count(), 1)

    def test_same_key_different_body_conflicts(self):
        self.post("key-2")
        other = self.client.post(
            "/api/orders/",
            {"dealer_id": self.dealer.id, "items": [{"sku": self.product.sku, "quantity": 5}]},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-2",
        )
        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(Order.objects.count(), 1)

    def test_requests_without_a_key_are_not_deduplicated(self):
        self.post(None)
        self.post(None)
        self.assertEqual(Order.objects.count(), 2)



class ConfirmConcurrencyTests(TransactionTestCase):
    """§7.3: stock must never be oversold when confirms arrive at the same moment."""

    def test_parallel_confirms_cannot_oversell(self):
        dealer = Dealer.objects.create(dealer_code="DLR-CONC-1", name="Park Street Autos")
        product = Product.objects.create(
            sku="ELE-900", name="Regulator", category="Electricals", brand="Moto Torque",
            vehicle_fitment="Universal", price=Decimal("100.00"), stock=10,
        )

        numbers = []
        for _ in range(15):
            order = Order.objects.create(
                order_number=f"ORD-CONC-{len(numbers):04d}",
                dealer=dealer,
                status="DRAFT",
                subtotal=Decimal("0.00"),
                tax_total=Decimal("0.00"),
                grand_total=Decimal("0.00"),
            )
            OrderItem.objects.create(
                order=order, product=product, sku=product.sku, name=product.name,
                quantity=2, unit_price=product.price,
                line_subtotal=Decimal("200.00"), line_tax=Decimal("36.00"), line_total=Decimal("236.00"),
            )
            numbers.append(order.order_number)

        def confirm(number):
            close_old_connections()
            try:
                confirm_order(number)
                return "ok"
            except Exception:
                return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=15) as pool:
            outcomes = list(pool.map(confirm, numbers))

        product.refresh_from_db()
        succeeded = outcomes.count("ok")

        self.assertEqual(succeeded, 5)
        self.assertEqual(product.stock, 0)
        self.assertEqual(Order.objects.filter(status="CONFIRMED").count(), 5)
        self.assertEqual(Order.objects.filter(status="DRAFT").count(), 10)
