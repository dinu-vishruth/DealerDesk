from django.urls import path
from . import views

urlpatterns = [
    path("assistant/chat/", views.AssistantChatView.as_view(), name="assistant-chat"),
]
