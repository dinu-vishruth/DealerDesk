from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException, ValidationError, NotFound
from rest_framework.response import Response
from django.http import Http404
import logging

logger = logging.getLogger(__name__)


class APIError(APIException):
    status_code = 400
    default_code = "VALIDATION_ERROR"
    default_detail = "An error occurred."

    def __init__(self, message=None, code=None, details=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        self.code = code or self.default_code
        self.message = message or self.default_detail
        self.details = details if isinstance(details, list) else ([] if details is None else [details])
        super().__init__(self.message, self.code)


class DuplicateError(APIError):
    status_code = 409
    default_code = "DUPLICATE"
    default_detail = "Resource already exists."


class NotFoundError(APIError):
    status_code = 404
    default_code = "NOT_FOUND"
    default_detail = "Resource not found."


def custom_exception_handler(exc, context):
    """
    Standardizes all API exceptions into the contract shape:
    {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Human readable text",
            "details": []
        }
    }
    """
    response = exception_handler(exc, context)

    if isinstance(exc, APIError):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": str(exc.message),
                    "details": exc.details,
                }
            },
            status=exc.status_code,
        )

    if isinstance(exc, Http404) or isinstance(exc, NotFound):
        return Response(
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": str(exc) or "Resource not found.",
                    "details": [],
                }
            },
            status=404,
        )

    if isinstance(exc, ValidationError):
        details = []
        if isinstance(exc.detail, dict):
            for k, v in exc.detail.items():
                if isinstance(v, list):
                    for item in v:
                        details.append(f"{k}: {item}")
                else:
                    details.append(f"{k}: {v}")
        elif isinstance(exc.detail, list):
            for item in exc.detail:
                details.append(str(item))
        else:
            details.append(str(exc.detail))

        return Response(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Validation error: " + (details[0] if details else "Invalid input"),
                    "details": details,
                }
            },
            status=400,
        )

    if response is not None:
        code = "VALIDATION_ERROR"
        if response.status_code == 404:
            code = "NOT_FOUND"
        elif response.status_code == 409:
            code = "DUPLICATE"

        msg = "An error occurred."
        details = []
        if isinstance(response.data, dict):
            if "detail" in response.data:
                msg = str(response.data["detail"])
            else:
                for k, v in response.data.items():
                    if isinstance(v, list):
                        for item in v:
                            details.append(f"{k}: {item}")
                    else:
                        details.append(f"{k}: {v}")
                if details:
                    msg = details[0]
        elif isinstance(response.data, list):
            details = [str(x) for x in response.data]
            if details:
                msg = details[0]

        return Response(
            {
                "error": {
                    "code": code,
                    "message": msg,
                    "details": details,
                }
            },
            status=response.status_code,
        )

    logger.exception("Unhandled server error", exc_info=exc)
    return Response(
        {
            "error": {
                "code": "SERVER_ERROR",
                "message": "Internal server error",
                "details": [],
            }
        },
        status=500,
    )
