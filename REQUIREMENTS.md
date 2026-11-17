# Software Requirements Specification: playwright-invisible-mcp

This specification defines the functional requirements, category taxonomy, and automated test traceability matrix for **`playwright-invisible-mcp`** (`stealth_wrapper` extension layer, baseline version `0.73.1` <!-- x-release-please-version -->).

---

## Requirement Category Taxonomy

Requirement identifiers use `<CATEGORY>-<NN>` format (`GUARD`, `MCP`, `SEC`, `TRANS`).

| Category Code | Domain Scope | Proof Types |
| :--- | :--- | :--- |
| **`GUARD`** | Profile lock sanitization, idempotent opens, and session lifecycle guards | `PositiveFeature` / `FailClosedGuardrail` |
| **`MCP`** | Model Context Protocol tool routing, schema adaptation, pagination, tabs, and forks | `PositiveFeature` / `FailClosedGuardrail` |
| **`SEC`** | Stealth `isTrusted` evaluation guardrails, path containment, and fork launch safety | `FailClosedGuardrail` |
| **`TRANS`** | HTTP transport resilience and bounded SPA DOM stability settling | `PositiveFeature` |

---

## Requirements and Traceability Matrix

| Requirement ID | Category | Type | Summary | Automated Test Proof |
| :--- | :--- | :--- | :--- | :--- |
| **`GUARD-01`** | `GUARD` | `FailClosedGuardrail` | **Stale Container Lock Cleanup** | [`test_profile_lifecycle_guard.py::test_sanitize_profile_holder_clears_foreign_and_dead_locks`](tests/wrapper/test_profile_lifecycle_guard.py) |
| **`GUARD-02`** | `GUARD` | `PositiveFeature` | **Idempotent Session Open & Profile Protection** | [`test_profile_lifecycle_guard.py::test_idempotent_open_reuses_warm_session_and_protects_profile`](tests/wrapper/test_profile_lifecycle_guard.py) |
| **`GUARD-03`** | `GUARD` | `PositiveFeature` | **Auto-Open Interceptors & Status Probes** | [`test_profile_lifecycle_guard.py::test_auto_open_interceptors_and_status`](tests/wrapper/test_profile_lifecycle_guard.py) |
| **`TRANS-01`** | `TRANS` | `PositiveFeature` | **Bounded Navigation Stability Settling** | [`test_dom_stability_settler.py::test_navigate_maps_networkidle_and_settles_dom`](tests/wrapper/test_dom_stability_settler.py) |
| **`TRANS-02`** | `TRANS` | `PositiveFeature` | **Route-Changing Click Settling** | [`test_dom_stability_settler.py::test_route_action_settles_only_when_url_changes`](tests/wrapper/test_dom_stability_settler.py) |
| **`MCP-01`** | `MCP` | `PositiveFeature` | **Scoped Snapshot Filtering & Element Caps** | [`test_browser_action_service.py::test_scoped_snapshot_filters_and_caps_elements`](tests/wrapper/test_browser_action_service.py) |
| **`MCP-02`** | `MCP` | `PositiveFeature` | **Directional Scroll & Selector ScrollIntoView** | [`test_browser_action_service.py::test_scroll_page_directions_fallback_and_selector`](tests/wrapper/test_browser_action_service.py) |
| **`MCP-03`** | `MCP` | `PositiveFeature` | **Bounded Waits & HttpOnly Cookie Snapshots** | [`test_browser_action_service.py::test_wait_and_get_cookies`](tests/wrapper/test_browser_action_service.py) |
| **`MCP-04`** | `MCP` | `PositiveFeature` | **Authenticated CAD2 & Fetch Downloads** | [`test_browser_action_service.py::test_download_url_saves_step_and_handles_errors`](tests/wrapper/test_browser_action_service.py) |
| **`MCP-05`** | `MCP` | `PositiveFeature` | **FastMCP Registration & ASGI Health Endpoints** | [`test_server_wrapper.py::test_server_wrapper_tool_registration_and_dispatch`](tests/wrapper/test_server_wrapper.py) |
| **`MCP-06`** | `MCP` | `PositiveFeature` | **Trusted Clipboard `Control+v` Paste** | [`test_browser_action_service.py::test_paste_text_trusted_clipboard_and_clear_modes`](tests/wrapper/test_browser_action_service.py) |
| **`MCP-07`** | `MCP` | `PositiveFeature` | **Bounded `max_chars` & `offset` Read Pagination** | [`test_browser_action_service.py::test_bounded_read_text_and_read_html_pagination`](tests/wrapper/test_browser_action_service.py) |
| **`MCP-08`** | `MCP` | `PositiveFeature` | **Multi-Tab Pinning & `ContextVar` Scoping** | [`test_tab_and_fork_services.py::test_tab_routing_pinned_and_scoped_navigation`](tests/wrapper/test_tab_and_fork_services.py) |
| **`MCP-09`** | `MCP` | `PositiveFeature` | **Parallel `storage_state` Context Forks** | [`test_tab_and_fork_services.py::test_fork_session_parallel_isolation_and_checkpoint_rollback`](tests/wrapper/test_tab_and_fork_services.py) |
| **`SEC-01`** | `SEC` | `FailClosedGuardrail` | **Read-Only JavaScript Evaluation Guardrail** | [`test_browser_action_service.py::test_evaluate_enforces_stealth_guardrails_and_runs_expressions`](tests/wrapper/test_browser_action_service.py) |
| **`SEC-02`** | `SEC` | `FailClosedGuardrail` | **Reject Auto-Launch for Unknown Fork IDs** | [`test_tab_and_fork_services.py::test_lifecycle_guard_skips_auto_open_for_unknown_forks`](tests/wrapper/test_tab_and_fork_services.py) |

---

## Requirement Definitions

### `GUARD-01`: Stale Container Lock Cleanup
- **Category**: `GUARD` (`FailClosedGuardrail`)
- **Description**: Clear stale Firefox symlink locks created by dead local PIDs or foreign container bridge IPs while preserving active local PID locks.

### `GUARD-02`: Idempotent Session Open & Profile Protection
- **Category**: `GUARD` (`PositiveFeature`)
- **Description**: Reuse active sessions matching identity and profile unless `reopen=True`, recovering gracefully when page inspection fails.

### `GUARD-03`: Auto-Open Interceptors & Status Probes
- **Category**: `GUARD` (`PositiveFeature`)
- **Description**: Automatically launch fixed roles (`main`, `support`) on acting, reading, or typing calls while keeping status probes non-destructive.

### `TRANS-01` & `TRANS-02`: Bounded SPA Stability Settling
- **Category**: `TRANS` (`PositiveFeature`)
- **Description**: Map `networkidle` to `load` and wait within configured millisecond bounds until resource timelines and DOM fingerprints stabilize on navigations and URL-changing clicks.

### `MCP-01` through `MCP-09`: Enhanced FastMCP Surface & Isolation
- **Category**: `MCP` (`PositiveFeature`)
- **Description**: Expose scoped snapshots, directional scrolling, bounded waits, `HttpOnly` cookie inspection, native/authenticated downloads, clipboard paste, paginated reads, pinned multi-tab routing, isolated context forks, and FastMCP/ASGI `/healthz` + `/health` endpoints.

### `SEC-01` & `SEC-02`: Stealth Evaluation & Fork Guardrails
- **Category**: `SEC` (`FailClosedGuardrail`)
- **Description**: Reject empty or mutating JavaScript evaluation strings and prevent auto-launching browsers for nonexistent fork session IDs.
