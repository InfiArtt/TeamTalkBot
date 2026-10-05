# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Semantic Versioning](https://semver.org/).

## [Unreleased]
### Added
- Automated checks on every pull request: flake8 looks for undefined names (such as the `chan_name` that broke every `@ai` answer) and `tests/` holds unit tests for the parts that run without the TeamTalk SDK (AI chat and its tool calls, AI review, badword filter, abuse tracker, help texts). Run them locally with `python -m unittest discover -s tests -t .`.
- `@ai <question>` in a channel: the AI (Cloudflare Workers AI, `ai_chat.py`) answers in that channel, as short plain text for screen readers, followed by `AI_CHAT_DISCLAIMER` ("AI bisa salah. Periksa kembali info penting."). It remembers the channel's last few questions for follow-ups, never gets nicknames, and has a per-person cooldown and a daily limit; answers with clear badwords are replaced by a refusal. Off by default (`/abt aichat`); users can read `/help ai`.
- Natural language bot commands via Function Calling for `@ai`: users can speak commands naturally (e.g. `@ai matiin word filter dong`, `@ai matiin moderation`, `@ai kick orang yang namanya kak fian dong`, `@ai siapa aja yang lagi online?`). Features 18 distinct tools covering moderation toggling (`toggle_moderation_feature`, `get_moderation_status`, `forgive_user`, `temp_ban_user`, `unban_user`), user management, badwords list management, and server inspection. Sensitive operations strictly verify admin privileges (`USERTYPE_ADMIN`), rejecting unauthorized calls with a Gen Z refusal. If an unsupported action is requested, the AI informs the user that the function is not yet available. Includes safe cross-thread tool dispatching and screen reader sanitized plain-text output.
- Guest accounts (`tamu`, `hadirin`) are blocked from using `@ai <question>` in channels by default (`AI_CHAT_BLOCKED_USERS`), with a private notification (`AI_CHAT_BLOCKED_MESSAGE`).
- Optional AI context check (`ai_review.py`, Cloudflare Workers AI): when every badword in a message is an ambiguous one (`AI_AMBIGUOUS_WORDS`, e.g. anjing, babi, tahi, telanjang, gay), the bot asks the AI whether it was an insult instead of counting it right away, so "anjing tetanggaku berisik" is not punished but "anjing lah" is. The AI also sees up to `AI_CONTEXT_MESSAGES` earlier messages and two follow-ups (kept in memory only, without names), so a lone "anjing" after "aku punya binatang baru" is read as the pet. Runs in the background; if the AI does not answer the message is not counted. Off by default, switched with `/abt ai` (refused until the credentials are in `config.json`); `/bwt` shows the AI's verdict; `tools/ai_eval.py` measures a model on 42 labelled sentences. Setup: `docs/ai.md`.
- GitHub Actions workflow (`.github/workflows/deploy.yml`): pull requests into `main` are checked (all Python files must compile), and merging into `main` updates and restarts the bot on the server over SSH through `tools/deploy.sh`, using a key that can only run that script. Setup: `docs/deploy.md`.

### Fixed
- An `@ai` question with a badword (`@ai anjing`) got a badword warning and was still answered by the AI. A question that gets a warning is no longer answered. With `/abt ai on`, a question with only ambiguous words is judged by the AI right away (no wait for follow-ups) and the AI is told it is a question to the assistant: asking about a word is not counted and is answered, insulting someone in the question is counted and is not answered.
- Anyone logged in with a shared account (e.g. `murid`) could delete that account for everyone with `/du murid` + `y`, because the bot treated it as deleting their own account. Accounts listed in the new `SHARED_ACCOUNTS` setting (default `tamu`, `murid`, `hadirin`, `osis`, `guest`) cannot be deleted via the bot, not even by admins.
- Join/login spam detection counted everyone on a shared account behind one IP (all students on `murid` from the school network) as one person. For shared accounts it now tells people apart by nickname + IP.
- Channels created by a shared account (owned by e.g. `murid`) could be deleted or given away by anyone on that account. Non-admin shared accounts can no longer create, delete or transfer channels via the bot; admin accounts (`pemateri`, `F.OSIS`) keep their admin rights. `pemateri` and `f.osis` are in the default `SHARED_ACCOUNTS`.

### Changed
- The shipped badword list is now `badwords/default_words.txt`; `badwords/words.txt` (with the admins' `/bwa`/`/bwd` edits) is git-ignored and created from the default list on first run, so updates no longer conflict with or overwrite those edits.
- `TeamTalkBot.service` is a template for a non-root account and logs to the journal.
- `main.sh`, `tools/deploy.sh` and `tools/download_sdk.py` are executable in git (no more `chmod +x` after cloning).
- README, docs and `/help` credit Rafli alongside Rexya; website address fixed (infiartt.com).

## [1.1.1] - 2026-09-28
### Added
- Licensed under GPL-3.0-or-later (`LICENSE`), with an additional permission (section 7) to combine the bot with the proprietary TeamTalk 5 SDK.
- `tools/download_sdk.py` installs the TeamTalk 5 SDK from BearWare.dk (native library + `TeamTalkPy` binding from the same release; `--archive` for a file downloaded by hand). The SDK is no longer kept in the repository: `TeamTalkPy/` is now git-ignored like the native libraries.
- Wildcards in badword entries: `*` matches any characters inside one word (symbols included) and `?` exactly one character, e.g. `anj*ng` catches `anjing`, `anjeng`, `anjiing` and the censored `anj*ng`. Entries need at least 3 letters or digits besides the wildcards; `/bwa` rejects broader ones with an explanation and they are ignored when found in the file.
- Stretched spellings are caught automatically: a word with a letter typed 3+ times in a row is also compared with repeated letters collapsed, so `anjing` catches `anjiiiing` and `annnnjing` (also for phrases and wildcard entries). Plain double letters are not collapsed, so `cook` is not read as `cok`.
- fail2ban-style control of the automatic moderation: `/abs` shows everyone with a warning and every active temp ban (numbered, with time left); `/abf <number|username|IP>` clears warnings, cancels kicks/bans that are about to run and lifts temp bans at once; `/abw` manages a whitelist (usernames/IPs never auto-punished, stored in `ABUSE_WHITELIST_FILE`); `/tb <nick> <minutes>[|reason]` issues a temporary ban that lifts itself; `/ab` is a step-by-step menu for all of these.
- Runtime feature switches: `/abt` (and option 5 of the `/ab` menu) turns login-spam, join-spam, message-spam, badword (messages), badword (nickname/status) and private-message checks on or off; option 6 of `/bw` flips the badword filter. Defaults come from `config.json` (new keys `ABUSE_LOGIN_ENABLED`, `ABUSE_JOIN_ENABLED`); only switches that differ are stored, in `feature_toggles.json`, so they survive restarts without the bot rewriting `config.json`. Switching a feature off clears its current warnings and cancels kicks/bans still pending.
- `/bw` menu that walks admins through listing, searching, adding, deleting and testing badwords.
- `/bwl [search]` shows a numbered list (optionally filtered); `/bwd` accepts those numbers (`3`, `3,5`, `3-5`), which keep pointing at the list the admin heard even after deletions.
- `/bwt <text>` shows which entries would flag a text, without warning anyone.
- `/bwa` and `/bwd` answer in a single message, including entries that were already present or not found.

### Changed
- Wizards (`/rc`, `/ru`) end after 3 minutes without a reply, and expired `/dc`/`/du` confirmations now notify the user. Open prompts are dropped when the user logs out.
- `/dc` checks channel ownership before asking for confirmation instead of after.
- `badwords/words.txt` rebuilt for Indonesian and English with wildcards (94 → 142 entries): suffix patterns such as `kontol*`, `bangs?t*`, `goblo*`, `*fuck*`, `shit*`, plus common English profanity and slurs. Checked against the 50,000 most frequent words of each language (OpenSubtitles frequency lists): patterns that hit normal words were dropped (e.g. `memek*` matched *memekik*/*memekakkan*, `anjing*` matched *anjingku* in pet talk, `*shit*` matched Japanese names like *ashita*). It catches 73/73 test variants (old list: 17/73) and none of 50 look-alike normal words. Entries admins removed on the server (gila, sinting, anjir, iblis, setan, vcs, monyed) stay out.
- Login abuse is counted per person (username + IP) instead of per IP, so several students logging in from one school network no longer trigger warnings (and eventually an IP ban for the whole network).

### Fixed
- Unstable connections were punished as join/login spam: every drop-and-reconnect counted as a login and the client's automatic rejoin as a channel join (89% of repeated join warnings in production came from a new session on the same IP). Joins within 10 s after a session's login are no longer counted, joins are counted per person (username + IP) instead of per IP, and a login arriving while the same username+IP is still online or within 3 s after its logout is treated as a reconnect (up to 5 per person per 5 minutes).
- When the bot itself (re)logged in, the server's replay of all online users was counted as fresh logins and joins, producing warnings right after bot restarts. Events during the bot's login command are now ignored.
- Admins were flagged by the private-message badword filter for their own `/bwa`/`/bwd` commands (the command text contains the badword), up to being kicked. Admin badword commands (`/bw`, `/bwl`, `/bwa`, `/bwd`, `/bwt`) are no longer checked; admins' normal chat still is.
- While a user had a wizard or confirmation open, private messages they sent to *other* users (intercepted for moderation) were taken as answers; e.g. a "y" to a friend confirmed a pending `/dc`, or chat text became the channel password. The TeamTalk client library never fills `nToUserID` (the server sends `destuserid`, the client reads `userid`), so the bot now pauses PM interception for a user while their prompt is open and resumes it afterwards. Everyone else stays intercepted.
- Temporary bans silently failed when the stage-2 kick disconnected the user first (`CMDERR_USER_NOT_FOUND`, 3006), which then made the auto-unban fail with 3007. Temp bans now target the IP (`doBanIPAddress`) or username (`doBan`) directly, and the username is captured when stage 3 triggers.
- Temporary bans were only remembered in memory, so a bot restart during a ban made it permanent. Active temp bans are now stored in `temp_bans.dat` and lifted after a restart (once the bot is logged in); `/ubn` also removes them from that list.
- `/ubn` unbanned by username when `BAN_TARGET` was `"IPADDRESS"`, so IP bans could not be lifted from the bot. Ban and unban now share one IP/username check.
- Long messages split by the SDK into several `bMore` fragments were processed fragment by fragment: one pasted text counted as several messages for anti-spam, badwords spanning two fragments were missed, and each fragment reached wizards as a separate answer. Fragments are now joined before processing.
- Multi-word badword entries such as `orang gila` could never match; they now match consecutive words.
- A malformed `config.json` was silently ignored and the bot ran with built-in defaults (default server, `admin`/`admin`, default policies). The bot now stops with an error naming the problem; a UTF-8 BOM is accepted.

### Security
- `/lc` no longer lists hidden channels (or channels nested under them) to non-admin users.
- Bot log files (`teamtalk.log`, `teamtalk.err`), which contain user IP addresses, are now git-ignored.

## [1.1.0] - 2026-08-02
### Added
- Comprehensive Message Anti-Spam system for `CHANNEL`, `BROADCAST`, and `PRIVATE` messages, fully integrated with AbuseTracker.
- Added per-category time windows support to `AbuseTracker` (e.g., a short 10s window for message spam vs a 90s window for login abuse).
- Added `ANTISPAM_*` configuration variables to `config.py` and `config.json`, including an `ANTISPAM_IGNORE_ADMINS` bypass.

### Fixed
- Fixed an issue where the bot incorrectly replied "Command not recognized" to casual chat containing `/` commands intercepted from other users in private messaging.
- Fixed a bug where message spam warnings were not being delivered to violators due to a missing category mapping in `_get_warning_message`.
- Fixed an automatic temporary unban failure (Error 3007) for IP addresses caused by a mismatched string check (`IPADDR` vs `IPADDRESS`).

## [1.0.0] - 2025-08-15
### Added
- Interactive badword management commands (`/bwl`, `/bwa`, `/bwd`) with persistent storage.
- Auto-deletion workflow for inactive user channels, including in-channel warnings and owner presence detection.
- `/v` command to report bot build info, OS details, and TeamTalk SDK version (with admin-only diagnostics).
- JSON-based configuration loader (`config.json`) with automatic first-run generation and instructions.
- HTML (`docs/index.html`) and Markdown (`README.md`) documentation detailing all features, config keys, and maintenance rules.

### Changed
- Channel creation and registration wizards now emit single consolidated success PMs with TTL info when applicable.
- Codebase refactored to ensure PEP 8 compliance, UTF-8 encoding, and consistent docstrings across modules.
- Version reporting logic centralized in `version.py`.
- TeamTalk SDK license is now configured solely via `config.json` (`TEAMTALK_LICENSE_NAME` / `TEAMTALK_LICENSE_KEY`), removing hardcoded or environment fallback logic.

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
