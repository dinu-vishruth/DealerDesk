from django.urls import path
from . import views

urlpatterns = [
    path("sync/supplier/", views.SupplierSyncView.as_view(), name="supplier-sync"),
]
