# TeamTalkBot

A full-featured administration bot for [TeamTalk 5](https://bearware.dk/teamtalk.html) servers. It logs in as an admin, automates channel/user workflows, enforces badword and abuse policies, and exposes interactive slash commands that users can invoke via private messages.

👉 **Need a deeper guide?** See [`docs/index.html`](docs/index.html) for the full manual.

## Features

- **Interactive Wizards**
  - `/ru` – guided user registration with username availability checks.
  - `/rc` – channel creation wizard (password/topic, channel flags, audio params).
- **Moderation Tools**
  - `/kc`, `/bn`, `/ubn`, `/lb`, `/lu` for kicks/bans/listings.
  - Abuse tracker with escalating warnings, kicks, and temporary bans.
  - Badword detector (channel/private/broadcast/profile) with live management (`/bwl`, `/bwa`, `/bwd`).
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
- TeamTalk SDK native library (`libTeamTalk5.so` on Linux/WSL, `TeamTalk5.dll` on Windows) in `TeamTalk_DLL/`
- A TeamTalk admin account for the bot to use
- (Optional) TeamTalk SDK license name/key if you want to avoid demo-mode limitations

## Quick Start

1. Ensure `libTeamTalk5.so`/`TeamTalk5.dll` is inside `TeamTalk_DLL/`.
2. Run the bot once:
   ```bash
   python3 main.py
   ```
   The bot will create `config.json` with defaults, print a reminder, and exit.
3. Edit `config.json` to match your server and policies (see [Configuration](#configuration)).
4. Start the bot again:
   ```bash
   python3 main.py
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
| **Abuse Throttling** | `ABUSE_LOGIN_COUNT`, `ABUSE_JOIN_COUNT`, `BADWORD_ABUSE_COUNT`, `ABUSE_TEMP_BAN_MINUTES`, warning message arrays |
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
| `/bwl`, `/bwa words`, `/bwd words` | List/add/delete badwords (admin). |
| `/cs <status>` | Admin: change bot status message. |

All wizards support `/cancel` to abort. Output is chunked automatically if responses exceed TeamTalk’s message limit.

## Maintenance Guidelines

- Treat `TeamTalkPy/` and `TeamTalk_DLL/` as vendor directories—do **not** edit SDK files.
- Keep Python files UTF-8 encoded, PEP 8 compliant, and documented (docstrings per module/class/function).
- Update [`CHANGELOG.md`](CHANGELOG.md) and `version.__version__` whenever you add features or refactor behavior.
- When adding commands, update both `command_handler.py` and `help_texts.py`, and extend the docs if new config keys are introduced.
- Use `python3 -m compileall` or your linter of choice before deploying.

## Contact

Developed by **Rexya Muhamad Rizki**  
📧 <rexya2017@gmail.com>  
🌐 <https://infiartt.ccom>
