from django.http import JsonResponse
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Product, Dealer, InventoryLog
from .serializers import (
    ProductSerializer,
    DealerSerializer,
    InventoryUpdateSerializer,
)
from .exceptions import NotFoundError, APIError
from .pagination import ContractPagination


def health(request):
    """GET /api/health/ — the grader polls this until the app is ready."""
    return JsonResponse({"status": "ok"})


def root_index(request):
    """GET / — Friendly index for browser visitors listing all API endpoints."""
    return JsonResponse({
        "service": "VIKMO DealerDesk API",
        "track": "aiml",
        "status": "online",
        "endpoints": {
            "health": "/api/health/",
            "products": "/api/products/",
            "dealers": "/api/dealers/",
            "orders": "/api/orders/",
            "reorder_suggestions": "/api/reorder-suggestions/",
            "assistant_chat": "/api/assistant/chat/",
            "admin": "/admin/",
        }
    })



class ProductListCreateView(APIView):
    pagination_class = ContractPagination

    def get(self, request):
        queryset = Product.objects.all().order_by("sku")

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(sku__icontains=search)
                | Q(name__icontains=search)
                | Q(brand__icontains=search)
            )

        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)

        vehicle = request.query_params.get("vehicle")
        if vehicle:
            queryset = queryset.filter(vehicle_fitment__icontains=vehicle)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = ProductSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        raise APIError(
            message="Invalid product data.",
            details=serializer.errors,
            status_code=400,
        )


class ProductDetailView(APIView):
    def get_object(self, sku):
        try:
            return Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            raise NotFoundError(f"Product '{sku}' not found.")

    def get(self, request, sku):
        product = self.get_object(sku)
        serializer = ProductSerializer(product)
        return Response(serializer.data)

    def patch(self, request, sku):
        product = self.get_object(sku)
        if "sku" in request.data and request.data["sku"] != product.sku:
            raise APIError("SKU cannot be changed.", code="VALIDATION_ERROR", status_code=400)
        serializer = ProductSerializer(product, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        raise APIError(
            message="Invalid product update.",
            details=serializer.errors,
            status_code=400,
        )


class InventoryUpdateView(APIView):
    def put(self, request, sku):
        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            raise NotFoundError(f"Product '{sku}' not found.")

        serializer = InventoryUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            raise APIError(
                message="Invalid inventory update.",
                details=serializer.errors,
                status_code=400,
            )

        new_qty = serializer.validated_data["quantity"]
        reason = serializer.validated_data.get("reason", "")
        old_qty = product.stock

        product.stock = new_qty
        product.save(update_fields=["stock", "updated_at"])

        InventoryLog.objects.create(
            product=product,
            previous_stock=old_qty,
            new_stock=new_qty,
            reason=reason,
        )

        return Response({"sku": product.sku, "quantity": product.stock}, status=status.HTTP_200_OK)


class DealerListCreateView(APIView):
    pagination_class = ContractPagination

    def get(self, request):
        queryset = Dealer.objects.all().order_by("id")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = DealerSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        raise APIError(
            message="Invalid dealer data.",
            details=serializer.errors,
            status_code=400,
        )


class DealerDetailView(APIView):
    def get(self, request, id):
        try:
            dealer = Dealer.objects.get(id=id)
        except Dealer.DoesNotExist:
            raise NotFoundError(f"Dealer with id {id} not found.")
        serializer = DealerSerializer(dealer)
        return Response(serializer.data)
