# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.76.0](https://github.com/spelech/playwright-invisible-mcp/compare/v0.75.0...v0.76.0) (2026-11-24)


### Features

* **aac:** add Vaultwarden Agent Access login pathway and built-in MCP prompts ([#5](https://github.com/spelech/playwright-invisible-mcp/issues/5)) ([53a7e93](https://github.com/spelech/playwright-invisible-mcp/commit/53a7e93ef8b1a439c97de09adff600bc95fff9f3))

## [0.75.0](https://github.com/spelech/playwright-invisible-mcp/compare/v0.74.0...v0.75.0) (2026-11-17)


### Features

* layer stealth_wrapper, Toolbelt standards, and release-please Docker CI ([df7a1c8](https://github.com/spelech/playwright-invisible-mcp/commit/df7a1c8f9af7a4f6f5c9756cbad2894f60245757))


### Bug Fixes

* **ci:** use clean semver tags in release-please and docker-publish ([53b6ca6](https://github.com/spelech/playwright-invisible-mcp/commit/53b6ca606f01e80864d8c020ef9c0d01e9876f01))
* prevent duplicate SSE events at connect ([#1229](https://github.com/spelech/playwright-invisible-mcp/issues/1229)) ([6244951](https://github.com/spelech/playwright-invisible-mcp/commit/6244951716cf3adfbc77db627b06360aa92607be))


### Documentation

* add GitAds sponsor snippet ([7b62fe3](https://github.com/spelech/playwright-invisible-mcp/commit/7b62fe3456c6ae49d495e4ae9246a3a266ea16ca))
* add LinkedIn badge ([ab4113d](https://github.com/spelech/playwright-invisible-mcp/commit/ab4113d453abb42c39b52b8ef30d8dfc118d6856))
* add sponsor badge ([11ae92a](https://github.com/spelech/playwright-invisible-mcp/commit/11ae92a84721593aff6dc9ee7a25388e93c7afb1))
* link to invisible_playwright, the project this became ([b91c3a2](https://github.com/spelech/playwright-invisible-mcp/commit/b91c3a2111419ddab50dda6243067f87e5338047))
* remove sponsor badge ([f5e8c8e](https://github.com/spelech/playwright-invisible-mcp/commit/f5e8c8e5a8531d8f35de29927edb530e37d747fd))

## [0.74.0](https://github.com/spelech/playwright-invisible-mcp/compare/invisible-playwright-mcp-v0.73.1...invisible-playwright-mcp-v0.74.0) (2026-11-17)


### Features

* layer stealth_wrapper, Toolbelt standards, and release-please Docker CI ([df7a1c8](https://github.com/spelech/playwright-invisible-mcp/commit/df7a1c8f9af7a4f6f5c9756cbad2894f60245757))


### Bug Fixes

* prevent duplicate SSE events at connect ([#1229](https://github.com/spelech/playwright-invisible-mcp/issues/1229)) ([6244951](https://github.com/spelech/playwright-invisible-mcp/commit/6244951716cf3adfbc77db627b06360aa92607be))


### Documentation

* add GitAds sponsor snippet ([7b62fe3](https://github.com/spelech/playwright-invisible-mcp/commit/7b62fe3456c6ae49d495e4ae9246a3a266ea16ca))
* add LinkedIn badge ([ab4113d](https://github.com/spelech/playwright-invisible-mcp/commit/ab4113d453abb42c39b52b8ef30d8dfc118d6856))
* add sponsor badge ([11ae92a](https://github.com/spelech/playwright-invisible-mcp/commit/11ae92a84721593aff6dc9ee7a25388e93c7afb1))
* link to invisible_playwright, the project this became ([b91c3a2](https://github.com/spelech/playwright-invisible-mcp/commit/b91c3a2111419ddab50dda6243067f87e5338047))
* remove sponsor badge ([f5e8c8e](https://github.com/spelech/playwright-invisible-mcp/commit/f5e8c8e5a8531d8f35de29927edb530e37d747fd))

## [0.73.1] - 2026-11-16
### Added
- Forked upstream `oss-invisible/playwright_mcp` (`0.73.1`) into `spelech/playwright-invisible-mcp`.
- Layered modular `stealth_wrapper` package (`src/stealth_wrapper/`) providing stateless Streamable HTTP, `/healthz` and `/health` probes, `ProfileLifecycleGuard`, `DomStabilitySettler`, `BrowserActionService`, `PageDownloadService`, `TabRoutingService`, and `SessionForkService`.
- Added AgenticEngineeringToolbelt compliance docs (`AGENTS.md`, `ARCHITECTURE.md`, `REQUIREMENTS.md`, `WRAPPER.md`), verification script (`scripts/verify_release.py`), `release-please` automation, and native `linux/amd64` GHCR container CI/CD workflows.
