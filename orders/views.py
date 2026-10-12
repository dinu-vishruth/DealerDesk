from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from core.pagination import ContractPagination
from core.exceptions import NotFoundError, APIError
from .models import Order
from .serializers import OrderSerializer
from .services import (
    cancel_order,
    confirm_order,
    create_order_idempotent,
    deliver_order,
    dispatch_order,
    get_order_or_404,
    refresh_draft_pricing,
    replace_order_items,
)


def _order_response(order, created=True):
    serializer = OrderSerializer(order)
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return Response(serializer.data, status=code)


class OrderListCreateView(APIView):
    pagination_class = ContractPagination

    def get(self, request):
        queryset = Order.objects.all().prefetch_related("items").select_related("dealer")

        dealer_id = request.query_params.get("dealer_id")
        if dealer_id:
            queryset = queryset.filter(dealer_id=dealer_id)

        order_status = request.query_params.get("status")
        if order_status:
            queryset = queryset.filter(status=order_status)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = OrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        if not isinstance(request.data, dict):
            raise APIError("Request body must be a JSON object.", code="VALIDATION_ERROR", status_code=400)

        dealer_id = request.data.get("dealer_id")
        items = request.data.get("items")
        idempotency_key = request.headers.get("Idempotency-Key")

        order, created = create_order_idempotent(dealer_id, items, idempotency_key)
        return _order_response(order, created)


class OrderDetailView(APIView):
    def get(self, request, order_number):
        order = get_order_or_404(order_number)
        with transaction.atomic():
            order = refresh_draft_pricing(order)
            order = (
                Order.objects.prefetch_related("items__product").select_related("dealer").get(pk=order.pk)
            )
        return Response(OrderSerializer(order).data)

    def put(self, request, order_number):
        if not isinstance(request.data, dict):
            raise APIError("Request body must be a JSON object.", code="VALIDATION_ERROR", status_code=400)

        items = request.data.get("items")
        order = replace_order_items(order_number, items)
        order = Order.objects.prefetch_related("items__product").select_related("dealer").get(pk=order.pk)
        return Response(OrderSerializer(order).data)


class OrderStatusActionView(APIView):
    """POST /api/orders/{order_number}/<action>/ — §7.2 status transitions."""

    actions = {
        "confirm": confirm_order,
        "dispatch": dispatch_order,
        "deliver": deliver_order,
        "cancel": cancel_order,
    }

    def post(self, request, order_number, action):
        handler = self.actions[action]
        order = handler(order_number)
        order = Order.objects.prefetch_related("items__product").select_related("dealer").get(pk=order.pk)
        return Response(OrderSerializer(order).data)
