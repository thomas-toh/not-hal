"""Model adapters (Contract B, spec/20). One async interface; every model plugs in
behind it. B1 (Claude API) is the M0 build (step 5); B2/B3 land at M2/M4."""
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
