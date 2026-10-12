from django.db import models
from core.models import Dealer


class AssistantSession(models.Model):
    session_id = models.CharField(max_length=128, unique=True, db_index=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.CASCADE, related_name="assistant_sessions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Session {self.session_id} (Dealer: {self.dealer_id})"


class AssistantMessage(models.Model):
    session = models.ForeignKey(AssistantSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20)  # system, user, assistant, tool
    content = models.TextField(blank=True, default="")
    tool_calls = models.JSONField(null=True, blank=True)
    tool_call_id = models.CharField(max_length=128, null=True, blank=True)
    name = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.session.session_id} [{self.role}]: {self.content[:40]}"
