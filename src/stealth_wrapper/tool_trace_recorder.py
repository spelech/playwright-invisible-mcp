"""Structured tool call tracing, sequence pattern analysis, and streamlining opportunity detection."""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("stealth_wrapper.trace")

SENSITIVE_ARG_MARKERS = ("key", "token", "secret", "password", "cookie", "auth", "totp", "otp")
READ_TOOLS = ("browser_snapshot", "browser_read_text", "browser_read_html", "browser_evaluate")
ACTION_TOOLS = (
    "browser_navigate",
    "browser_click",
    "browser_click_at",
    "browser_type",
    "browser_paste",
    "browser_select_option",
    "browser_press_key",
    "browser_scroll",
)


class ToolTraceEvent(BaseModel):
    """Single traced FastMCP tool invocation with timing, session, URL, and payload telemetry."""

    seq: int
    timestamp: str
    tool: str
    sessionId: str = "main"
    tabId: Optional[int] = None
    args: dict[str, Any] = Field(default_factory=dict)
    durationMs: float
    interToolGapMs: Optional[float] = None
    prevTool: Optional[str] = None
    urlBefore: Optional[str] = None
    urlAfter: Optional[str] = None
    urlChanged: bool = False
    status: str = "ok"
    responseChars: int = 0
    error: Optional[str] = None


class ToolStatsSummary(BaseModel):
    """Aggregated execution statistics for a single tool."""

    tool: str
    calls: int = 0
    errors: int = 0
    totalDurationMs: float = 0.0
    avgDurationMs: float = 0.0
    maxDurationMs: float = 0.0
    totalResponseChars: int = 0
    avgResponseChars: int = 0


class SequencePatternSummary(BaseModel):
    """Frequency and latency statistics for a recurring 2-step or 3-step tool sequence."""

    pattern: str
    steps: list[str]
    occurrences: int
    avgCombinedDurationMs: float
    avgInterToolGapMs: float


class StreamliningOpportunity(BaseModel):
    """Actionable candidate for combining multi-step tool call sequences into a single step."""

    category: str
    pattern: str
    occurrences: int
    avgCombinedDurationMs: float
    avgInterToolGapMs: float
    estimatedSavedCalls: int
    suggestion: str


class TraceSummaryResponse(BaseModel):
    """Structured telemetry summary returned by `browser_trace_summary` and `GET /traces`."""

    enabled: bool = True
    traceFile: Optional[str] = None
    totalCalls: int = 0
    totalErrors: int = 0
    bufferedEvents: int = 0
    toolStats: list[ToolStatsSummary] = Field(default_factory=list)
    topBigrams: list[SequencePatternSummary] = Field(default_factory=list)
    topTrigrams: list[SequencePatternSummary] = Field(default_factory=list)
    streamliningOpportunities: list[StreamliningOpportunity] = Field(default_factory=list)
    recentEvents: list[ToolTraceEvent] = Field(default_factory=list)


def sanitize_trace_args(raw_args: dict[str, Any], *, max_str_len: int = 160) -> dict[str, Any]:
    """Redact secret-like keys and truncate long string values so trace logs stay compact."""
    sanitized: dict[str, Any] = {}
    for key, value in raw_args.items():
        lower_key = key.lower()
        if any(marker in lower_key for marker in SENSITIVE_ARG_MARKERS) and key != "selector":
            sanitized[key] = "<redacted>"
        elif isinstance(value, str) and len(value) > max_str_len:
            sanitized[key] = f"{value[:max_str_len]}...<{len(value)} chars>"
        elif isinstance(value, (list, tuple)) and len(value) > 10:
            sanitized[key] = f"<list len={len(value)}>"
        else:
            sanitized[key] = value
    return sanitized


def _classify_opportunity(
    steps: list[str],
    pattern: str,
    occurrences: int,
    avg_combined_ms: float,
    avg_gap_ms: float,
) -> Optional[StreamliningOpportunity]:
    """Evaluate a bigram or trigram and return a streamlining opportunity when actionable."""
    saved_per_occurrence = max(1, len(steps) - 1)
    estimated_saved = occurrences * saved_per_occurrence
    if len(steps) == 2:
        first, second = steps
        if first == second and first in ("browser_scroll", "browser_read_text", "browser_read_html"):
            return StreamliningOpportunity(
                category="repeated_step_loop",
                pattern=pattern,
                occurrences=occurrences,
                avgCombinedDurationMs=avg_combined_ms,
                avgInterToolGapMs=avg_gap_ms,
                estimatedSavedCalls=estimated_saved,
                suggestion=(
                    f"Repeated `{first}` calls detected; consider a larger scroll `amount`/selector "
                    "target or larger `max_chars` window to avoid multi-round-trip loops."
                ),
            )
        if first in ACTION_TOOLS and second in READ_TOOLS:
            return StreamliningOpportunity(
                category="action_then_read",
                pattern=pattern,
                occurrences=occurrences,
                avgCombinedDurationMs=avg_combined_ms,
                avgInterToolGapMs=avg_gap_ms,
                estimatedSavedCalls=estimated_saved,
                suggestion=(
                    f"`{first}` is frequently followed immediately by `{second}`; candidate for an "
                    f"inline `include_snapshot` / `read_after` option on `{first}`."
                ),
            )
        if first in ("browser_paste", "browser_type") and second in (
            "browser_click",
            "browser_press_key",
        ):
            return StreamliningOpportunity(
                category="input_then_submit",
                pattern=pattern,
                occurrences=occurrences,
                avgCombinedDurationMs=avg_combined_ms,
                avgInterToolGapMs=avg_gap_ms,
                estimatedSavedCalls=estimated_saved,
                suggestion=(
                    f"`{first}` followed by `{second}` forms an input-and-submit pair; candidate for "
                    f"`submit_selector` or `press_enter=True` on `{first}`."
                ),
            )
    if len(steps) == 3 and steps[2] in READ_TOOLS and (
        steps[0] in ACTION_TOOLS or steps[1] in ("browser_wait", "browser_scroll")
    ):
        return StreamliningOpportunity(
            category="multi_step_action_chain",
            pattern=pattern,
            occurrences=occurrences,
            avgCombinedDurationMs=avg_combined_ms,
            avgInterToolGapMs=avg_gap_ms,
            estimatedSavedCalls=estimated_saved,
            suggestion=(
                f"3-step chain `{pattern}` occurs {occurrences}x; candidate for a composite workflow "
                "step combining navigation/action, scroll/wait, and scoped snapshot."
            ),
        )
    return None


class ToolTraceRecorder:
    """Records FastMCP tool executions and computes sequence/streamlining analytics."""

    def __init__(
        self,
        work: Any,
        *,
        enabled: bool = True,
        trace_file: Optional[Path] = None,
        max_events: int = 500,
        log_events: bool = True,
    ) -> None:
        self._work = work
        self.enabled = enabled
        self.trace_file = trace_file
        self.max_events = max(10, max_events)
        self.log_events = log_events
        self._seq = 0
        self._total_calls = 0
        self._total_errors = 0
        self._last_finished_mono: Optional[float] = None
        self._last_tool_by_session: dict[str, str] = {}
        self._events: deque[ToolTraceEvent] = deque(maxlen=self.max_events)
        self._stats: dict[str, dict[str, float]] = {}
        self._bigram_stats: dict[tuple[str, str], list[ tuple[float, float] ]] = {}
        self._trigram_stats: dict[tuple[str, str, str], list[ tuple[float, float] ]] = {}
        self._session_recent: dict[str, deque[ToolTraceEvent]] = {}

    def reset(self) -> None:
        """Clear in-memory telemetry counters, sequence tables, and recent events."""
        self._total_calls = 0
        self._total_errors = 0
        self._last_finished_mono = None
        self._last_tool_by_session.clear()
        self._events.clear()
        self._stats.clear()
        self._bigram_stats.clear()
        self._trigram_stats.clear()
        self._session_recent.clear()

    def _peek_session_url(self, session_id: str) -> Optional[str]:
        """Read the current page URL for an already-open session without opening a browser."""
        try:
            open_map = getattr(self._work, "_open", None) or {}
            session = open_map.get(session_id)
            if session is None or not session.is_usable():
                return None
            return str(session.page().url or "")
        except Exception:
            return None

    def wrap_tool(self, tool_name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Return an async wrapper around `fn` that records telemetry while preserving signature."""
        if getattr(fn, "_trace_wrapped", False):
            return fn
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())

        async def _traced(*args: Any, **kwargs: Any) -> Any:
            if not self.enabled or tool_name == "browser_trace_summary":
                return await fn(*args, **kwargs)

            bound = dict(zip(param_names, args))
            bound.update(kwargs)
            session_id = str(
                bound.get("browser")
                or bound.get("session_id")
                or bound.get("from_browser")
                or "main"
            )
            tab_raw = bound.get("tab_id")
            tab_id = int(tab_raw) if isinstance(tab_raw, int) else None
            url_before = self._peek_session_url(session_id)
            start_mono = time.perf_counter()
            gap_ms = (
                round((start_mono - self._last_finished_mono) * 1000.0, 2)
                if self._last_finished_mono is not None
                else None
            )
            prev_tool = self._last_tool_by_session.get(session_id)
            status = "ok"
            error_msg: Optional[str] = None
            response_chars = 0
            try:
                result = await fn(*args, **kwargs)
                response_chars = len(result) if isinstance(result, str) else len(str(result))
                return result
            except Exception as exc:
                status = "error"
                error_msg = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                end_mono = time.perf_counter()
                duration_ms = round((end_mono - start_mono) * 1000.0, 2)
                self._last_finished_mono = end_mono
                url_after = self._peek_session_url(session_id)
                self._seq += 1
                event = ToolTraceEvent(
                    seq=self._seq,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    tool=tool_name,
                    sessionId=session_id,
                    tabId=tab_id,
                    args=sanitize_trace_args(bound),
                    durationMs=duration_ms,
                    interToolGapMs=gap_ms,
                    prevTool=prev_tool,
                    urlBefore=url_before,
                    urlAfter=url_after,
                    urlChanged=bool(url_before and url_after and url_before != url_after),
                    status=status,
                    responseChars=response_chars,
                    error=error_msg,
                )
                self._record_event(event)

        _traced.__name__ = getattr(fn, "__name__", tool_name)
        _traced.__doc__ = getattr(fn, "__doc__", None)
        _traced.__signature__ = sig  # type: ignore[attr-defined]
        _traced.__annotations__ = dict(getattr(fn, "__annotations__", {}))
        _traced._trace_wrapped = True  # type: ignore[attr-defined]
        return _traced

    def wrap_registered_tools(
        self,
        mcp: Any,
        tools_dict: dict[str, Callable[..., Any]],
    ) -> dict[str, Callable[..., Any]]:
        """Instrument all FastMCP tools in `mcp._tool_manager._tools` and synchronize `tools_dict`."""
        wrapped_map: dict[str, Callable[..., Any]] = {}
        for name, tool_obj in list(mcp._tool_manager._tools.items()):
            wrapped_fn = self.wrap_tool(name, tool_obj.fn)
            tool_obj.fn = wrapped_fn
            wrapped_map[name] = wrapped_fn
        for name, fn in list(tools_dict.items()):
            wrapped_map[name] = wrapped_map.get(name) or self.wrap_tool(name, fn)
        tools_dict.update(wrapped_map)
        return tools_dict

    def _record_event(self, event: ToolTraceEvent) -> None:
        """Update counters, sequence n-grams, ring buffer, JSONL file sink, and structured logs."""
        self._total_calls += 1
        if event.status == "error":
            self._total_errors += 1
        self._last_tool_by_session[event.sessionId] = event.tool
        self._events.append(event)

        stat = self._stats.setdefault(
            event.tool,
            {"calls": 0.0, "errors": 0.0, "dur": 0.0, "max": 0.0, "chars": 0.0},
        )
        stat["calls"] += 1
        if event.status == "error":
            stat["errors"] += 1
        stat["dur"] += event.durationMs
        stat["max"] = max(stat["max"], event.durationMs)
        stat["chars"] += event.responseChars

        sess_window = self._session_recent.setdefault(event.sessionId, deque(maxlen=3))
        sess_window.append(event)
        if len(sess_window) >= 2:
            e1, e2 = sess_window[-2], sess_window[-1]
            key2 = (e1.tool, e2.tool)
            self._bigram_stats.setdefault(key2, []).append(
                (e1.durationMs + e2.durationMs, e2.interToolGapMs or 0.0)
            )
        if len(sess_window) == 3:
            e1, e2, e3 = sess_window[0], sess_window[1], sess_window[2]
            key3 = (e1.tool, e2.tool, e3.tool)
            gap_sum = (e2.interToolGapMs or 0.0) + (e3.interToolGapMs or 0.0)
            self._trigram_stats.setdefault(key3, []).append(
                (e1.durationMs + e2.durationMs + e3.durationMs, gap_sum)
            )

        if self.trace_file is not None:
            try:
                self.trace_file.parent.mkdir(parents=True, exist_ok=True)
                with self.trace_file.open("a", encoding="utf-8") as fh:
                    fh.write(event.model_dump_json() + "\n")
            except OSError:
                pass

        if self.log_events:
            logger.info(
                "mcp_tool_trace seq=%d tool=%s session=%s tab=%s status=%s duration_ms=%.2f "
                "gap_ms=%s prev=%s url_changed=%s chars=%d",
                event.seq,
                event.tool,
                event.sessionId,
                event.tabId,
                event.status,
                event.durationMs,
                event.interToolGapMs,
                event.prevTool,
                event.urlChanged,
                event.responseChars,
            )

    def summarize(
        self,
        *,
        limit_recent: int = 25,
        min_sequence_count: int = 2,
        reset: bool = False,
    ) -> TraceSummaryResponse:
        """Build a `TraceSummaryResponse` with tool stats, n-grams, and streamlining suggestions."""
        min_count = max(1, min_sequence_count)
        tool_summaries = [
            ToolStatsSummary(
                tool=name,
                calls=int(s["calls"]),
                errors=int(s["errors"]),
                totalDurationMs=round(s["dur"], 2),
                avgDurationMs=round(s["dur"] / max(1.0, s["calls"]), 2),
                maxDurationMs=round(s["max"], 2),
                totalResponseChars=int(s["chars"]),
                avgResponseChars=int(round(s["chars"] / max(1.0, s["calls"]))),
            )
            for name, s in sorted(
                self._stats.items(), key=lambda item: (-item[1]["calls"], item[0])
            )
        ]

        bigrams = self._build_patterns(self._bigram_stats, min_count=min_count)
        trigrams = self._build_patterns(self._trigram_stats, min_count=min_count)

        opportunities: list[StreamliningOpportunity] = []
        for pat in [*trigrams, *bigrams]:
            opp = _classify_opportunity(
                pat.steps,
                pat.pattern,
                pat.occurrences,
                pat.avgCombinedDurationMs,
                pat.avgInterToolGapMs,
            )
            if opp is not None:
                opportunities.append(opp)
        opportunities.sort(
            key=lambda o: (-o.estimatedSavedCalls, -o.occurrences, -o.avgCombinedDurationMs)
        )

        recent = list(self._events)[-max(0, limit_recent) :] if limit_recent > 0 else []
        resp = TraceSummaryResponse(
            enabled=self.enabled,
            traceFile=str(self.trace_file) if self.trace_file else None,
            totalCalls=self._total_calls,
            totalErrors=self._total_errors,
            bufferedEvents=len(self._events),
            toolStats=tool_summaries,
            topBigrams=bigrams[:15],
            topTrigrams=trigrams[:15],
            streamliningOpportunities=opportunities[:15],
            recentEvents=recent,
        )
        if reset:
            self.reset()
        return resp

    def summarize_json(
        self,
        *,
        limit_recent: int = 25,
        min_sequence_count: int = 2,
        reset: bool = False,
    ) -> str:
        """Serialize `summarize(...)` as formatted JSON for the `browser_trace_summary` MCP tool."""
        return json.dumps(
            self.summarize(
                limit_recent=limit_recent,
                min_sequence_count=min_sequence_count,
                reset=reset,
            ).model_dump(),
            indent=2,
        )

    @staticmethod
    def _build_patterns(
        table: dict[Any, list[tuple[float, float]]],
        *,
        min_count: int,
    ) -> list[SequencePatternSummary]:
        results: list[SequencePatternSummary] = []
        for steps_tuple, samples in table.items():
            count = len(samples)
            if count < min_count:
                continue
            avg_dur = round(sum(s[0] for s in samples) / count, 2)
            avg_gap = round(sum(s[1] for s in samples) / count, 2)
            steps = list(steps_tuple)
            results.append(
                SequencePatternSummary(
                    pattern=" -> ".join(steps),
                    steps=steps,
                    occurrences=count,
                    avgCombinedDurationMs=avg_dur,
                    avgInterToolGapMs=avg_gap,
                )
            )
        results.sort(key=lambda item: (-item.occurrences, -item.avgCombinedDurationMs, item.pattern))
        return results
