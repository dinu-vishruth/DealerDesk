from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response

from core.exceptions import APIError
from .services import SupplierUnavailable, run_supplier_sync


class SupplierSyncView(APIView):
    """POST /api/sync/supplier/ — one full supplier sync (§8.2)."""

    def post(self, request):
        base_url = settings.SUPPLIER_API_URL
        if not base_url:
            raise APIError(
                "SUPPLIER_API_URL is not configured.",
                code="SUPPLIER_UNAVAILABLE",
                status_code=502,
            )

        try:
            result = run_supplier_sync(base_url, settings.SUPPLIER_API_KEY)
        except SupplierUnavailable as exc:
            # §8.3.3: nothing was created or updated.
            raise APIError(str(exc), code="SUPPLIER_UNAVAILABLE", status_code=502)

        return Response(result)
