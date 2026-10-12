from django.test import TestCase
from decimal import Decimal
import math
from core.models import Product
from forecasting.services import ForecastingService


class ForecastingAPITests(TestCase):
    def setUp(self):
        self.p = Product.objects.create(
            sku="BRK-1019",
            name="Test Brake Pad",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1200.00"),
            stock=100,
        )

    def test_forecast_endpoint(self):
        res = self.client.get("/api/forecast/BRK-1019/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["sku"], "BRK-1019")
        self.assertEqual(len(data["forecast"]), 4)

        # 404 for unknown sku
        res_404 = self.client.get("/api/forecast/NON-EXISTENT/")
        self.assertEqual(res_404.status_code, 404)
        self.assertEqual(res_404.json()["error"]["code"], "NOT_FOUND")

    def test_reorder_suggestions(self):
        res = self.client.get("/api/reorder-suggestions/")
        self.assertEqual(res.status_code, 200)
        results = res.json().get("results")
        self.assertEqual(len(results), 40)

        # Test calculation formula on first item
        item = next(x for x in results if x["sku"] == "BRK-1019")
        L = item["lead_time_weeks"]
        ltd = item["lead_time_demand"]
        ss = item["safety_stock"]
        rp = item["reorder_point"]
        stock = item["current_stock"]
        rq = item["reorder_qty"]

        self.assertEqual(round(ltd + ss, 1), rp)
        self.assertEqual(max(0, math.ceil(rp - stock)), rq)
