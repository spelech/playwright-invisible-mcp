# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.73.1] - 2026-11-16
### Added
- Forked upstream `oss-invisible/playwright_mcp` (`0.73.1`) into `spelech/playwright-invisible-mcp`.
- Layered modular `stealth_wrapper` package (`src/stealth_wrapper/`) providing stateless Streamable HTTP, `/healthz` and `/health` probes, `ProfileLifecycleGuard`, `DomStabilitySettler`, `BrowserActionService`, `PageDownloadService`, `TabRoutingService`, and `SessionForkService`.
- Added AgenticEngineeringToolbelt compliance docs (`AGENTS.md`, `ARCHITECTURE.md`, `REQUIREMENTS.md`, `WRAPPER.md`), verification script (`scripts/verify_release.py`), `release-please` automation, and native `linux/amd64` GHCR container CI/CD workflows.
