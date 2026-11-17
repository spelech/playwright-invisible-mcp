"""Stealth browser wrapper modules for invisible-playwright-mcp."""

from .browser_action_service import BrowserActionService
from .dom_stability_settler import DomStabilitySettler
from .models import (
    DEFAULT_READ_HTML_MAX_CHARS,
    DEFAULT_READ_TEXT_MAX_CHARS,
    CookieRecord,
    CookieSnapshotResponse,
    DownloadResultResponse,
    ForkCreateResponse,
    ForkDiscardResponse,
    ForkPromoteResponse,
    PasteResultResponse,
    ScrollResultResponse,
    TabActionResponse,
    TabInfo,
    TabListResponse,
)
from .page_download_service import PageDownloadService
from .profile_lifecycle_guard import ProfileLifecycleGuard
from .server import WrapperRuntime, build_wrapper_runtime, main
from .forked_session import ForkedStealthSession
from .session_fork_service import SessionForkService
from .tab_routing_service import TabRoutingService
from .tool_trace_recorder import (
    SequencePatternSummary,
    StreamliningOpportunity,
    ToolStatsSummary,
    ToolTraceEvent,
    ToolTraceRecorder,
    TraceSummaryResponse,
)

__all__ = [
    "BrowserActionService",
    "CookieRecord",
    "CookieSnapshotResponse",
    "DEFAULT_READ_HTML_MAX_CHARS",
    "DEFAULT_READ_TEXT_MAX_CHARS",
    "DomStabilitySettler",
    "DownloadResultResponse",
    "ForkCreateResponse",
    "ForkDiscardResponse",
    "ForkedStealthSession",
    "ForkPromoteResponse",
    "PageDownloadService",
    "PasteResultResponse",
    "ProfileLifecycleGuard",
    "ScrollResultResponse",
    "SequencePatternSummary",
    "SessionForkService",
    "StreamliningOpportunity",
    "TabActionResponse",
    "TabInfo",
    "TabListResponse",
    "TabRoutingService",
    "ToolStatsSummary",
    "ToolTraceEvent",
    "ToolTraceRecorder",
    "TraceSummaryResponse",
    "WrapperRuntime",
    "build_wrapper_runtime",
    "main",
]
