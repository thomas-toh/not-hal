"""Model adapters (Contract B). One async interface; every model plugs in
behind it. B1 (Claude API) came first; B2 is the OpenAI-compatible adapter; B3 is for adapters that run their own tools."""
from .base import (
    ModelAdapter,
    ModelEvent,
    Done,
    Error,
    Session,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "ModelAdapter",
    "ModelEvent",
    "Done",
    "Error",
    "Session",
    "TextDelta",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
]
