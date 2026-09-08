"""Input contracts and shared primitives for the Xero Bills Agent Skill."""

from .errors import AppError
from .models import AttachmentRequest, BillRequest, CommandResult, ItemRequest

__all__ = [
    "AppError",
    "AttachmentRequest",
    "BillRequest",
    "CommandResult",
    "ItemRequest",
]
