from django.urls import path
from . import views

urlpatterns = [
    path("forecast/<str:sku>/", views.ForecastDetailView.as_view(), name="forecast-detail"),
    path("reorder-suggestions/", views.ReorderSuggestionsView.as_view(), name="reorder-suggestions"),
]
