from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from core.exceptions import APIError
from .service import process_chat_message


class AssistantChatView(APIView):
    def post(self, request):
        if not isinstance(request.data, dict):
            raise APIError("Request body must be a JSON object.", code="VALIDATION_ERROR", status_code=400)

        session_id = request.data.get("session_id")
        dealer_id = request.data.get("dealer_id")
        message = request.data.get("message")

        result = process_chat_message(
            session_id=session_id,
            dealer_id=dealer_id,
            message_text=message,
        )

        return Response(result, status=status.HTTP_200_OK)
