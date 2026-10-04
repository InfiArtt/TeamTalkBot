# TeamTalkBot

A full-featured administration bot for [TeamTalk 5](https://bearware.dk/teamtalk.html) servers. It logs in as an admin, automates channel/user workflows, enforces badword and abuse policies, and exposes interactive slash commands that users can invoke via private messages.

👉 **Need a deeper guide?** See [`docs/index.html`](docs/index.html) for the full manual.

## Features

- **Interactive Wizards**
  - `/ru` – guided user registration with username availability checks.
  - `/rc` – channel creation wizard (password/topic, channel flags, audio params).
- **Moderation Tools**
  - `/kc`, `/bn`, `/ubn`, `/lb`, `/lu` for kicks/bans/listings.
  - fail2ban-style control of the automatic moderation: `/ab` menu, `/abs` status, `/abf` forgive, `/abw` whitelist, `/tb` temporary ban (temp bans survive restarts).
  - Every moderation feature can be switched on/off at runtime with `/abt` or from the `/ab` menu; switches are remembered in `feature_toggles.json`.
  - Abuse tracker with escalating warnings, kicks, and temporary bans.
  - Badword detector (channel/private/broadcast/profile) with live management (`/bw` menu, `/bwl`, `/bwa`, `/bwd`, `/bwt`) and `*`/`?` wildcards (e.g. `anj*ng`).
- **Channel Lifecycle Automation**
  - Auto tracks channel ownership; enforces per-user limits.
  - Inactivity TTL with “owner absent” warnings and automatic deletion.
- **Runtime Utilities**
  - `/dc`, `/du`, `/so`, `/to`, `/oc` for channel/user maintenance.
  - `/v` shows bot/OS/TeamTalk SDK version (admins also see binding/library paths).
  - `/help` (or `/help <topic>`) for all commands.
- **Config-Driven**
  - Single `config.json` file controls server/TLS creds, channel defaults, abuse thresholds, logging, and TeamTalk SDK license.

## Requirements

- Python 3.12+
- The TeamTalk 5 SDK by BearWare.dk. It is **not included** in this repository (it has its own license, see [License](#license)); `tools/download_sdk.py` installs it. Unpacking the SDK archive needs 7-Zip (`sudo apt install p7zip-full` on Linux) or `pip install py7zr`.
- A TeamTalk admin account for the bot to use
- (Optional) TeamTalk SDK license name/key if you want to avoid demo-mode limitations

## Quick Start

1. Install the TeamTalk SDK:
   ```bash
   python3 tools/download_sdk.py
   ```
   This puts `libTeamTalk5.so` next to `main.sh` (Linux) or `TeamTalk5.dll` in `TeamTalk_DLL/` (Windows), plus the Python binding in `TeamTalkPy/`, all from the same SDK release. If bearware.dk cannot be reached, download `tt5sdk_<version>_<platform>.7z` by hand from <https://bearware.dk/teamtalksdk/> and run `python3 tools/download_sdk.py --archive <file>`. Use `--force` to replace an existing installation.
2. Run the bot once (on Linux use `./main.sh`, which puts the library on `LD_LIBRARY_PATH`; on Windows `python main.py`):
   ```bash
   ./main.sh
   ```
   The bot will create `config.json` with defaults, print a reminder, and exit.
3. Edit `config.json` to match your server and policies (see [Configuration](#configuration)).
4. Start the bot again:
   ```bash
   ./main.sh
   ```
5. Interact with the bot from TeamTalk via private messages (e.g., `/help`).

## Configuration

All settings live in `config.json`. Highlights:

| Category | Keys |
| --- | --- |
| **Server & TLS** | `SERVER_HOST`, `TCP_PORT`, `UDP_PORT`, `SERVER_ENCRYPTION_ENABLED`, TLS cert/CA paths, `TEAMTALK_LICENSE_NAME`, `TEAMTALK_LICENSE_KEY` |
| **Bot Identity** | `BOT_NICKNAME`, `CLIENT_NAME`, default status message/mode |
| **User Rights Template** | `DEFAULT_USER_RIGHTS` – `yes`/`no` per `UserRight` enum |
| **Channel Defaults** | `LOGIN_CHANNEL_PATH`, `CREATE_CHANNEL_PARENT_PATH`, `CHANNEL_DEFAULTS` (permanent, hidden, class flags, max users) |
| **Audio Defaults** | `AUDIO_DEFAULTS` (application, sample rate, mono/stereo, bitrate, VBR, ignore silence/DTX, transmit interval/frame size, fixed volume) |
| **Lifecycle** | `CHANNEL_INACTIVITY_TIMEOUT` (`{"value": X, "unit": "days"}`), `CHANNEL_DELETION_WARNING_SECONDS` |
| **Channel Rules** | `CHANNEL_CREATION_MAX_PER_USER`, limit/block messages, `CHANNEL_CREATION_BLOCKED_USERNAMES` |
| **Registration** | `REGISTRATION_ALLOWED_USERNAMES`, per-IP limits/messages |
| **Abuse Throttling** | `ABUSE_LOGIN_ENABLED`, `ABUSE_JOIN_ENABLED`, `ABUSE_LOGIN_COUNT`, `ABUSE_JOIN_COUNT`, `BADWORD_ABUSE_COUNT`, `ABUSE_WINDOW_SEC`, `ABUSE_TEMP_BAN_MINUTES`, `BAN_TARGET`, `ABUSE_WHITELIST_FILE`, warning message arrays |
| **Message Anti-Spam** | `ANTISPAM_MESSAGE_ENABLED`, `ANTISPAM_MESSAGE_COUNT`, `ANTISPAM_MESSAGE_WINDOW_SEC`, `ANTISPAM_INTERCEPT_TYPES`, `ANTISPAM_IGNORE_ADMINS`, warning message arrays |
| **Badwords** | `BADWORDS_ENABLED`, `BADWORDS_INTERCEPT_TYPES` (`"PRIVATE"`, `"CHANNEL"`, `"BROADCAST"`), `BADWORDS_FILE`, `BADWORDS_IGNORE_ADMINS`, `BADWORDS_PROFILE_CHECK_ENABLED` |
| **Runtime** | `RECONNECT_MAX_ATTEMPTS`, `RECONNECT_RETRY_DELAY_SEC`, `EVENT_LOOP_WAIT_MS`, `EVENT_LOOP_SLEEP_SEC`, logging format/level |

The bot auto-generates `config.json` on first launch; edit the JSON file afterwards and re-run.

## Command Reference

| Command | Description |
| --- | --- |
| `/help [topic]` | Overview or topic-specific help. |
| `/v` | Show bot/OS/SDK version (admins also see binding paths). |
| `/ru [username]` | Start registration wizard. |
| `/rc <channel_name>` | Start channel wizard (password/topic/flags/audio). |
| `/dc <path> [--force]` | Delete a channel (admins or owners). |
| `/du <username> [--force]` | Delete a user (admins or self). |
| `/lc [path]` | List channels. |
| `/oc <path>` | Show cached owner info. |
| `/so <path>|<username>` | Admin: set channel owner. |
| `/to <path>|<username>` | Admin/current owner: transfer ownership. |
| `/kc <nick[,nick2...]>[|reason]` | Kick users by nickname. |
| `/bn <nick[,nick2...]>[|reason]` | Ban users (auto-kick follows). |
| `/ubn <value[,value2...]>` | Remove bans (username/IP). |
| `/lb`, `/lu` | List bans / registered accounts (admin). |
| `/ab` | Step-by-step auto-moderation menu: status, forgive, whitelist, temp ban (admin). |
| `/abs` | Numbered list of current warnings and temp bans with time left (admin). |
| `/abf <number\|username\|IP[,...]>` | Clear warnings, cancel pending kicks/bans, lift temp bans (admin). |
| `/abw`, `/abw add x`, `/abw del n` | Whitelist usernames/IPs that auto-moderation never punishes (admin). |
| `/tb <nick[,nick2...]> <minutes>[\|reason]` | Temporary ban lifted automatically, even after a restart (admin). |
| `/abt [<number\|name> [on\|off]]` | Switch moderation features on/off at runtime: login, join, spam, badwords, profile, pm (admin). |
| `/bw` | Step-by-step badword menu: list, search, add, delete, test (admin). |
| `/bwl [search]`, `/bwa words`, `/bwd numbers\|words` | Numbered list (optionally filtered) / add / delete by list number or word (admin). |
| `/bwt <text>` | Show which badword entries would flag a text (admin). |
| `/cs <status>` | Admin: change bot status message. |

All wizards support `/cancel` to abort. Output is chunked automatically if responses exceed TeamTalk’s message limit.

## Deployment

Work on a branch and open a pull request into `main`. GitHub Actions checks every pull request, and merging into `main` updates and restarts the bot on the server over SSH (`tools/deploy.sh`). The one-time server and GitHub setup is in [`docs/deploy.md`](docs/deploy.md).

`badwords/default_words.txt` is the shipped badword list; the bot copies it to `badwords/words.txt` on first run, and that copy (with the admins' `/bwa`/`/bwd` edits) stays on the server and out of git.

## Maintenance Guidelines

- Treat `TeamTalkPy/`, `TeamTalk_DLL/` and `libTeamTalk5.so` as vendor files installed by `tools/download_sdk.py`—do **not** edit or commit them.
- Keep Python files UTF-8 encoded, PEP 8 compliant, and documented (docstrings per module/class/function).
- Update [`CHANGELOG.md`](CHANGELOG.md) and `version.__version__` whenever you add features or refactor behavior.
- When adding commands, update both `command_handler.py` and `help_texts.py`, and extend the docs if new config keys are introduced.
- Use `python3 -m compileall` or your linter of choice before deploying.

## License

Copyright (C) 2025-2026 Rexya Muhamad Rizki, Rafli and contributors

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [`LICENSE`](LICENSE).

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

**Additional permission under GNU GPL version 3 section 7:** If you modify this Program, or any covered work, by linking or combining it with the TeamTalk 5 SDK by BearWare.dk (or a modified version of that library), containing parts covered by the terms of the TeamTalk 5 SDK License Agreement, the licensors of this Program grant you additional permission to convey the resulting work.

**TeamTalk 5 SDK:** the SDK files (`TeamTalk_DLL/`, `libTeamTalk5.so`, `TeamTalkPy/`) are not part of this project and are not covered by the GPL. They belong to BearWare.dk and are licensed under the TeamTalk 5 SDK License Agreement (`License.txt` in the SDK archive); `tools/download_sdk.py` downloads them from BearWare.dk. Whoever runs the bot is responsible for following that agreement, including any license key it requires.

## Contact

Developed by:

- **Rexya Muhamad Rizki** ([@rexya2017](https://github.com/rexya2017)) — 📧 <rexya2017@gmail.com>
- **Rafli** ([@raf-li](https://github.com/raf-li))

🌐 <https://infiartt.com>
