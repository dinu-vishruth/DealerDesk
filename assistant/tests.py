from django.test import TestCase
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch
from core.models import Product, Dealer
from assistant.models import AssistantSession, AssistantMessage
from assistant.tools import execute_search_parts, execute_check_stock, execute_create_draft_order
from orders.models import Order


class AssistantTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-AST-1",
            name="Metro Motors",
            credit_limit=Decimal("50000.00"),
        )
        self.product = Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - Bajaj Pulsar 150",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50"),
            stock=136,
        )

    def test_tool_search_parts(self):
        res = execute_search_parts(query="brake pads", vehicle="Bajaj Pulsar 150")
        self.assertGreaterEqual(res["count"], 1)
        self.assertEqual(res["products"][0]["sku"], "BRK-1044")

    def test_tool_check_stock(self):
        res = execute_check_stock("BRK-1044")
        self.assertEqual(res["sku"], "BRK-1044")
        self.assertEqual(res["stock"], 136)
        self.assertEqual(res["price"], "1459.50")

    def test_tool_create_draft_order(self):
        actions = []
        res = execute_create_draft_order(
            dealer_id=self.dealer.id,
            items=[{"sku": "BRK-1044", "quantity": 3}],
            actions_list=actions,
        )
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["order_number"].startswith("ORD-"))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "draft_order_created")

    def test_session_memory_isolation(self):
        s1 = AssistantSession.objects.create(session_id="session-1", dealer=self.dealer)
        s2 = AssistantSession.objects.create(session_id="session-2", dealer=self.dealer)

        AssistantMessage.objects.create(session=s1, role="user", content="Hello session 1")
        AssistantMessage.objects.create(session=s2, role="user", content="Hello session 2")

        self.assertEqual(s1.messages.count(), 1)
        self.assertEqual(s1.messages.first().content, "Hello session 1")
        self.assertEqual(s2.messages.count(), 1)
        self.assertEqual(s2.messages.first().content, "Hello session 2")

    @patch("assistant.service.get_llm_client")
    def test_chat_draft_uses_request_dealer(self, get_client):
        call = SimpleNamespace(
            id="draft-call", function=SimpleNamespace(
                name="create_draft_order",
                arguments=json.dumps({"items": [{"sku": self.product.sku, "quantity": 2}]}),
            ),
        )
        get_client.return_value.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=""))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[], content="Draft created."))]),
        ]
        response = self.client.post("/api/assistant/chat/", {
            "session_id": "draft-session", "dealer_id": self.dealer.id,
            "message": "Create a draft for two brake pad sets.",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get()
        self.assertEqual(order.dealer_id, self.dealer.id)
        self.assertEqual(order.status, "DRAFT")
        self.assertEqual(response.json()["actions"], [{
            "type": "draft_order_created", "order_number": order.order_number,
        }])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 136)

    @patch("assistant.service.get_llm_client")
    def test_session_owner_is_checked_before_saving_or_loading_messages(self, get_client):
        other = Dealer.objects.create(dealer_code="DLR-AST-2", name="Other Dealer")
        session = AssistantSession.objects.create(session_id="owned-session", dealer=self.dealer)
        AssistantMessage.objects.create(session=session, role="user", content="Original context")
        response = self.client.post("/api/assistant/chat/", {
            "session_id": session.session_id, "dealer_id": other.id, "message": "Hello",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(session.messages.count(), 1)
        get_client.assert_not_called()

    @patch("assistant.service.get_llm_client")
    def test_invalid_identity_fields_do_not_create_session(self, get_client):
        for dealer_id, session_id in [(True, "valid"), ("invalid", "valid"), (self.dealer.id, "   "), (self.dealer.id, "s" * 129)]:
            with self.subTest(dealer_id=dealer_id, session_id=session_id):
                response = self.client.post("/api/assistant/chat/", {
                    "session_id": session_id, "dealer_id": dealer_id, "message": "Hello",
                }, content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(AssistantSession.objects.count(), 0)
        get_client.assert_not_called()
