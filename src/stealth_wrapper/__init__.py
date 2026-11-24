"""Stealth browser wrapper modules for invisible-playwright-mcp."""

from .browser_action_service import BrowserActionService
from .credential_access_service import CredentialAccessService
from .dom_stability_settler import DomStabilitySettler
from .mcp_prompts import register_wrapper_prompts
from .models import (
    DEFAULT_READ_HTML_MAX_CHARS,
    DEFAULT_READ_TEXT_MAX_CHARS,
    AacStatusResponse,
    CookieRecord,
    CookieSnapshotResponse,
    CredentialFillResponse,
    DownloadResultResponse,
    ForkCreateResponse,
    ForkDiscardResponse,
    ForkPromoteResponse,
    LoginFormFields,
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
    "AacStatusResponse",
    "BrowserActionService",
    "CookieRecord",
    "CookieSnapshotResponse",
    "CredentialAccessService",
    "CredentialFillResponse",
    "DEFAULT_READ_HTML_MAX_CHARS",
    "DEFAULT_READ_TEXT_MAX_CHARS",
    "DomStabilitySettler",
    "DownloadResultResponse",
    "ForkCreateResponse",
    "ForkDiscardResponse",
    "ForkedStealthSession",
    "ForkPromoteResponse",
    "LoginFormFields",
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
    "register_wrapper_prompts",
]
