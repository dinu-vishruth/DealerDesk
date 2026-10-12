from rest_framework import serializers
from decimal import Decimal
from .models import Order, OrderItem
from core.serializers import MoneyField


class OrderItemSerializer(serializers.ModelSerializer):
    unit_price = MoneyField()
    line_subtotal = MoneyField()
    line_tax = MoneyField()
    line_total = MoneyField()

    class Meta:
        model = OrderItem
        fields = [
            "sku",
            "name",
            "quantity",
            "unit_price",
            "line_subtotal",
            "line_tax",
            "line_total",
        ]


class OrderSerializer(serializers.ModelSerializer):
    dealer_id = serializers.IntegerField(source="dealer.id")
    items = OrderItemSerializer(many=True, read_only=True)
    subtotal = MoneyField()
    tax_total = MoneyField()
    grand_total = MoneyField()

    def to_representation(self, instance):
        from django.utils import timezone
        ret = super().to_representation(instance)
        if instance.created_at:
            ret["created_at"] = timezone.localtime(instance.created_at).isoformat()
        if instance.updated_at:
            ret["updated_at"] = timezone.localtime(instance.updated_at).isoformat()
        return ret



    class Meta:
        model = Order
        fields = [
            "order_number",
            "dealer_id",
            "status",
            "items",
            "subtotal",
            "tax_total",
            "grand_total",
            "created_at",
            "updated_at",
        ]
