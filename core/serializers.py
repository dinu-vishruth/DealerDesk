from rest_framework import serializers
from decimal import Decimal, InvalidOperation
from .models import Product, Dealer
from .exceptions import DuplicateError, APIError


class MoneyField(serializers.Field):
    """
    Serializes Decimal as a string with exactly 2 decimal places.
    Deserializes string/number to Decimal, validating > 0 and <= 2 decimal places.
    """
    def __init__(self, min_value=None, **kwargs):
        self.min_value = min_value
        super().__init__(**kwargs)

    def to_representation(self, value):
        if value is None:
            return None
        return f"{Decimal(str(value)):.2f}"

    def to_internal_value(self, data):
        if data is None or data == "":
            raise serializers.ValidationError("Price is required.")
        try:
            val = Decimal(str(data))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError("Must be a valid decimal number.")

        if self.min_value is not None and val <= self.min_value:
            raise serializers.ValidationError(f"Must be strictly greater than {self.min_value}.")

        # Check at most 2 decimal places
        str_val = str(data).strip()
        if "." in str_val:
            decimals = len(str_val.split(".")[1])
            if decimals > 2:
                raise serializers.ValidationError("Cannot have more than 2 decimal places.")

        return val


class ProductSerializer(serializers.ModelSerializer):
    price = MoneyField(min_value=Decimal("0.00"))
    stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "stock",
            "description",
        ]
        extra_kwargs = {
            "sku": {"validators": []},
        }

    def validate_sku(self, value):
        if self.instance is None and Product.objects.filter(sku=value).exists():
            raise DuplicateError(f"Product with SKU '{value}' already exists.")
        return value

    def create(self, validated_data):
        validated_data["stock"] = 0
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "sku" in validated_data and validated_data["sku"] != instance.sku:
            raise APIError("SKU cannot be changed.", code="VALIDATION_ERROR", status_code=400)
        return super().update(instance, validated_data)


class DealerSerializer(serializers.ModelSerializer):
    credit_limit = MoneyField(min_value=Decimal("0.00"), required=False, default=Decimal("0.00"))

    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "dealer_code": {"validators": []},
        }

    def validate_dealer_code(self, value):
        if self.instance is None and Dealer.objects.filter(dealer_code=value).exists():
            raise DuplicateError(f"Dealer with code '{value}' already exists.")
        return value



class InventoryUpdateSerializer(serializers.Serializer):
    quantity = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError("Quantity must be an integer greater than or equal to 0.")
        return value
