from django.urls import path
from . import views

urlpatterns = [
    path("orders/", views.OrderListCreateView.as_view(), name="order-list-create"),
    path("orders/<str:order_number>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:order_number>/confirm/", views.OrderStatusActionView.as_view(), {"action": "confirm"}, name="order-confirm"),
    path("orders/<str:order_number>/dispatch/", views.OrderStatusActionView.as_view(), {"action": "dispatch"}, name="order-dispatch"),
    path("orders/<str:order_number>/deliver/", views.OrderStatusActionView.as_view(), {"action": "deliver"}, name="order-deliver"),
    path("orders/<str:order_number>/cancel/", views.OrderStatusActionView.as_view(), {"action": "cancel"}, name="order-cancel"),
]
