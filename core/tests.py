from django.test import TestCase
from decimal import Decimal
from core.models import Product, Dealer, InventoryLog


class CoreAPITests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-TEST-1",
            name="Test Auto",
            city="Bengaluru",
            phone="9876543210",
            email="test@example.com",
            credit_limit=Decimal("50000.00"),
        )
        self.product = Product.objects.create(
            sku="BRK-9999",
            name="Ceramic Brake Pads",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("1500.00"),
            stock=20,
            description="High performance ceramic brake pads.",
        )

    def test_health_check(self):
        res = self.client.get("/api/health/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    def test_products_list_and_filters(self):
        res = self.client.get("/api/products/?search=Ceramic")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 1)

        res_cat = self.client.get("/api/products/?category=Brakes")
        self.assertEqual(res_cat.status_code, 200)
        self.assertEqual(res_cat.json()["count"], 1)

        res_veh = self.client.get("/api/products/?vehicle=Duke")
        self.assertEqual(res_veh.status_code, 200)
        self.assertEqual(res_veh.json()["count"], 1)

    def test_product_create_and_duplicate(self):
        payload = {
            "sku": "BRK-8888",
            "name": "Front Disc Rotor",
            "category": "Brakes",
            "brand": "Endurance",
            "vehicle_fitment": "Bajaj Pulsar 150",
            "price": "2200.50",
            "description": "Standard rotor",
        }
        res = self.client.post("/api/products/", payload, content_type="application/json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["stock"], 0)  # initializes to 0

        # Duplicate SKU -> 409 DUPLICATE
        dup = self.client.post("/api/products/", payload, content_type="application/json")
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.json()["error"]["code"], "DUPLICATE")

    def test_inventory_update(self):
        res = self.client.put(
            f"/api/inventory/{self.product.sku}/",
            {"quantity": 45, "reason": "Monthly audit"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["quantity"], 45)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 45)

        # Negative quantity rejected
        neg = self.client.put(
            f"/api/inventory/{self.product.sku}/",
            {"quantity": -5},
            content_type="application/json",
        )
        self.assertEqual(neg.status_code, 400)
        self.assertEqual(neg.json()["error"]["code"], "VALIDATION_ERROR")
