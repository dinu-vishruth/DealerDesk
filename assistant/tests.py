from django.test import TestCase
from decimal import Decimal
from core.models import Product, Dealer
from assistant.models import AssistantSession, AssistantMessage
from assistant.tools import execute_search_parts, execute_check_stock, execute_create_draft_order


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
