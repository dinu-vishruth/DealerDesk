from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from core.exceptions import NotFoundError
from .services import ForecastingService


class ForecastDetailView(APIView):
    def get(self, request, sku):
        service = ForecastingService.get_instance()
        forecast = service.get_forecast_for_sku(sku)
        if not forecast:
            raise NotFoundError(f"Forecast for SKU '{sku}' not found.")

        # Ensure sorted by week_start
        sorted_forecast = sorted(forecast, key=lambda x: x["week_start"])
        return Response({"sku": sku, "forecast": sorted_forecast}, status=status.HTTP_200_OK)


class ReorderSuggestionsView(APIView):
    def get(self, request):
        service = ForecastingService.get_instance()
        results = service.get_reorder_suggestions()
        return Response({"results": results}, status=status.HTTP_200_OK)
