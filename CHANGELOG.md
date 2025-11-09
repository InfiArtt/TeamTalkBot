# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2025-08-15
### Added
- Interactive badword management commands (`/bwl`, `/bwa`, `/bwd`) with persistent storage.
- Auto-deletion workflow for inactive user channels, including in-channel warnings and owner presence detection.
- `/v` command to report bot build info, OS details, and TeamTalk SDK version (with admin-only diagnostics).

### Changed
- Channel creation and registration wizards now emit single consolidated success PMs with TTL info when applicable.
- Codebase refactored to ensure PEP 8 compliance, UTF-8 encoding, and consistent docstrings across modules.
- Version reporting logic centralized in `version.py`.

## [0.9.0] - 2025-07-20
### Added
- Robust reconnection strategy with configurable retry limits/delays and graceful shutdown after exhausting attempts.
- Channel inactivity tracking persisted in `channel_owners.dat` for future lifecycle automation.

### Changed
- Channel creation wizard restricted to name-only input; path handling now purely configuration-driven.
- `/rc` and `/ru` flows converted into multi-step interactive sessions.

## [0.8.0] - 2025-06-10
### Added
- Registration wizard introduced (`/ru`) with username availability check against live server state.
- Abuse tracker now resets stages after cooldown windows, improving throttling accuracy.

### Changed
- Badword filter expanded to inspect profile fields and relay violations through a centralized handler.

## [0.7.0] - 2025-05-01
### Added
- Channel wizard overhaul to capture audio properties (Opus-only) and enforce admin-configurable defaults.
- Channel owner transfer (`/to`) and manual owner assignment (`/so`) commands.

### Changed
- `/rc` wizard now enforces leading-character validation and normalized channel paths.

## [0.6.0] - 2025-03-15
### Added
- Abuse tracking for login/join/badword events with staged escalation (warning, kick, temp ban).
- Admin moderation commands (`/kc`, `/bn`, `/ubn`, `/lb`, `/lu`) with chunked PM output for large listings.

### Changed
- Badword filter now respects ignore-admins flag and uses shared helper utilities.

## [0.5.0] - 2025-01-30
### Added
- Initial feature-complete release:
  - Core TeamTalk connectivity, auto-login, and channel join behavior.
  - Channel creation (`/rc`), deletion (`/dc`), and user management (`/du`, `/ru`) flows.
  - Persistent storage helpers for owners and registration throttling.
