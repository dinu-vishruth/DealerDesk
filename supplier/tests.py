from decimal import Decimal
from unittest import mock

from django.test import TestCase

from core.models import Product
from supplier import services


def feed_item(sku="BRK-1044", **overrides):
    item = {
        "supplier_sku": "SUP-1",
        "sku": sku,
        "name": "Brake Pad Set",
        "category": "Brakes",
        "brand": "Endurance",
        "vehicle_fitment": "Bajaj Pulsar 150",
        "description": "Sintered pads",
        "price": "1459.50",
        "supplier_stock": 340,
        "updated_at": "2026-10-06T09:15:00Z",
    }
    item.update(overrides)
    return item


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {"error": "boom"}
        self.headers = headers or {}

    def json(self):
        return self._body


class FetchFeedTests(TestCase):
    """§8.3.1 and §8.3.2 — every page, retries, and the 5-attempt ceiling."""

    def setUp(self):
        patcher = mock.patch.object(services.requests, "Session")
        self.session_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.session = self.session_cls.return_value
        self.sleep = mock.patch.object(services.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_follows_cursor_to_the_last_page(self):
        self.session.get.side_effect = [
            FakeResponse(200, {"items": [feed_item(sku="A-1")], "next_cursor": "c1"}),
            FakeResponse(200, {"items": [feed_item(sku="A-2")], "next_cursor": None}),
        ]
        items, pages, requests_made = services.fetch_feed("http://supplier.test", "key")

        self.assertEqual([i["sku"] for i in items], ["A-1", "A-2"])
        self.assertEqual((pages, requests_made), (2, 2))

    def test_rate_limit_waits_for_retry_after_then_repeats_the_request(self):
        self.session.get.side_effect = [
            FakeResponse(429, {"error": "rate limited"}, {"Retry-After": "3"}),
            FakeResponse(200, {"items": [feed_item()], "next_cursor": None}),
        ]
        items, pages, requests_made = services.fetch_feed("http://supplier.test", "key")

        self.assertEqual(len(items), 1)
        self.assertEqual((pages, requests_made), (1, 2))
        self.sleep.assert_called_once_with(3.0)

    def test_transient_server_errors_are_retried(self):
        self.session.get.side_effect = [
            FakeResponse(503), FakeResponse(502),
            FakeResponse(200, {"items": [feed_item()], "next_cursor": None}),
        ]
        items, pages, requests_made = services.fetch_feed("http://supplier.test", "key")

        self.assertEqual(len(items), 1)
        self.assertEqual((pages, requests_made), (1, 3))

    def test_gives_up_after_five_attempts(self):
        self.session.get.return_value = FakeResponse(500)
        with self.assertRaises(services.SupplierUnavailable):
            services.fetch_feed("http://supplier.test", "key")
        self.assertEqual(self.session.get.call_count, services.MAX_ATTEMPTS)

    def test_connection_errors_count_as_attempts(self):
        self.session.get.side_effect = services.requests.RequestException("down")
        with self.assertRaises(services.SupplierUnavailable):
            services.fetch_feed("http://supplier.test", "key")
        self.assertEqual(self.session.get.call_count, services.MAX_ATTEMPTS)

    def test_rejected_key_fails_without_retrying(self):
        self.session.get.return_value = FakeResponse(401)
        with self.assertRaises(services.SupplierUnavailable):
            services.fetch_feed("http://supplier.test", "key")
        self.assertEqual(self.session.get.call_count, 1)


class ValidateAndDedupeTests(TestCase):
    def test_invalid_items_are_rejected_not_fatal(self):
        valid, rejected = services.validate_items([
            feed_item(sku=""),
            feed_item(supplier_sku="SUP-9", sku=None),
            feed_item(price="N/A"),
            feed_item(price="12.345"),
            feed_item(price="0.00"),
            feed_item(price="-5.00"),
            feed_item(sku="OK-1"),
        ])
        self.assertEqual([i["sku"] for i in valid], ["OK-1"])
        self.assertEqual([r["reason"] for r in rejected], [
            "missing sku", "missing sku", "invalid price", "invalid price", "invalid price", "invalid price",
        ])

    def test_price_keeps_at_most_two_decimals(self):
        self.assertEqual(services.parse_price("1499"), Decimal("1499.00"))
        self.assertIsNone(services.parse_price("1.999"))
        self.assertIsNone(services.parse_price(""))
        self.assertIsNone(services.parse_price(None))

    def test_duplicate_sku_uses_the_latest_updated_at(self):
        valid, _ = services.validate_items([
            feed_item(sku="BRK-1", price="200.00", updated_at="2026-10-05T00:00:00Z"),
            feed_item(sku="BRK-1", price="100.00", updated_at="2026-10-09T00:00:00Z"),
            feed_item(sku="BRK-1", price="150.00", updated_at="2026-10-07T00:00:00Z"),
        ])
        by_sku = services.latest_per_sku(valid)
        self.assertEqual(by_sku["BRK-1"]["price"], Decimal("100.00"))


class ApplyItemsTests(TestCase):
    def test_create_update_unchanged_without_touching_stock(self):
        Product.objects.create(
            sku="OLD-1", name="Old Name", category="Brakes", brand="Bosch",
            vehicle_fitment="Universal", price=Decimal("500.00"), stock=42, description="keep",
        )
        same = feed_item(sku="OLD-1", name="Old Name", category="Brakes", brand="Bosch",
                        vehicle_fitment="Universal", price="500.00", description="keep")
        new = feed_item(sku="NEW-1", price="250.00")

        valid, _ = services.validate_items([same, new])
        created, updated, unchanged = services.apply_items(services.latest_per_sku(valid))

        self.assertEqual((created, updated, unchanged), (1, 0, 1))
        old = Product.objects.get(sku="OLD-1")
        self.assertEqual(old.stock, 42)
        self.assertEqual(Product.objects.get(sku="NEW-1").stock, 0)

    def test_only_listed_fields_are_applied(self):
        Product.objects.create(
            sku="OLD-2", name="Old", category="Brakes", brand="Bosch",
            vehicle_fitment="Pulsar", price=Decimal("500.00"), stock=7, description="old text",
        )
        valid, _ = services.validate_items([
            feed_item(sku="OLD-2", name="New", price="560.00", description="new text")
        ])
        created, updated, unchanged = services.apply_items(services.latest_per_sku(valid))

        self.assertEqual((created, updated, unchanged), (0, 1, 0))
        product = Product.objects.get(sku="OLD-2")
        self.assertEqual(product.name, "New")
        self.assertEqual(product.price, Decimal("560.00"))
        self.assertEqual(product.description, "new text")
        self.assertEqual(product.stock, 7)

    def test_sync_is_idempotent(self):
        valid, _ = services.validate_items([feed_item(sku="REP-1")])
        services.apply_items(services.latest_per_sku(valid))
        created, updated, unchanged = services.apply_items(services.latest_per_sku(valid))
        self.assertEqual((created, updated, unchanged), (0, 0, 1))


class SupplierSyncEndpointTests(TestCase):
    def test_502_when_the_feed_cannot_be_fetched(self):
        with mock.patch.object(
            services, "fetch_feed", side_effect=services.SupplierUnavailable("supplier is down")
        ):
            res = self.client.post("/api/sync/supplier/")

        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["error"]["code"], "SUPPLIER_UNAVAILABLE")
        self.assertEqual(Product.objects.count(), 0)

    def test_response_shape(self):
        def fake_fetch(base_url, api_key, deadline=None):
            return [feed_item(sku="EP-1")], 1, 2

        with mock.patch.object(services, "fetch_feed", side_effect=fake_fetch):
            res = self.client.post("/api/sync/supplier/")

        self.assertEqual(res.status_code, 200)
        body = res.json()
        for key in ("created", "updated", "unchanged", "rejected", "pages_fetched", "requests_made"):
            self.assertIn(key, body)
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["requests_made"], 2)
