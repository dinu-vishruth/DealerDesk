from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.root_index, name="root-index"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    path("api/", include("orders.urls")),
    path("api/", include("forecasting.urls")),
    path("api/", include("assistant.urls")),
    path("api/", include("supplier.urls")),
]



