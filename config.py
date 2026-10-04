"""Configuration loader that merges defaults with overrides from config.json."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Static configuration variables with type annotations and defaults
TEAMTALK_LICENSE_NAME: str = ""
TEAMTALK_LICENSE_KEY: str = ""
SERVER_HOST: str = "tt.infiartt.com"
TCP_PORT: int = 10333
UDP_PORT: int = 10333
SERVER_ENCRYPTION_ENABLED: bool = False
TLS_CERTIFICATE_FILE: str = ""
TLS_PRIVATE_KEY_FILE: str = ""
TLS_CA_FILE: str = ""
TLS_CA_DIR: str = ""
TLS_VERIFY_SERVER: bool = False
TLS_VERIFY_CLIENT_ONCE: bool = False
TLS_VERIFY_DEPTH: int = 0
ADMIN_USERNAME: str = "admin"
ADMIN_PASSWORD: str = "admin"
BOT_NICKNAME: str = "TeamTalk administration"
CLIENT_NAME: str = "Lanthera"
BOT_DEFAULT_STATUS_MESSAGE: str = "Send /help for assistance."
BOT_DEFAULT_STATUS_MODE: int = 0
DEFAULT_USER_RIGHTS: Dict[str, str] = {
    "USERRIGHT_MULTI_LOGIN": "no",
    "USERRIGHT_VIEW_ALL_USERS": "yes",
    "USERRIGHT_CREATE_TEMPORARY_CHANNEL": "yes",
    "USERRIGHT_MODIFY_CHANNELS": "no",
    "USERRIGHT_TEXTMESSAGE_BROADCAST": "no",
    "USERRIGHT_TEXTMESSAGE_USER": "yes",
    "USERRIGHT_TEXTMESSAGE_CHANNEL": "yes",
    "USERRIGHT_KICK_USERS": "no",
    "USERRIGHT_BAN_USERS": "no",
    "USERRIGHT_MOVE_USERS": "no",
    "USERRIGHT_OPERATOR_ENABLE": "no",
    "USERRIGHT_UPLOAD_FILES": "yes",
    "USERRIGHT_DOWNLOAD_FILES": "yes",
    "USERRIGHT_UPDATE_SERVERPROPERTIES": "no",
    "USERRIGHT_TRANSMIT_VOICE": "yes",
    "USERRIGHT_TRANSMIT_VIDEOCAPTURE": "yes",
    "USERRIGHT_TRANSMIT_DESKTOP": "yes",
    "USERRIGHT_TRANSMIT_DESKTOPINPUT": "yes",
    "USERRIGHT_TRANSMIT_MEDIAFILE_AUDIO": "yes",
    "USERRIGHT_TRANSMIT_MEDIAFILE_VIDEO": "yes",
    "USERRIGHT_TRANSMIT_MEDIAFILE": "no",
    "USERRIGHT_LOCKED_NICKNAME": "no",
    "USERRIGHT_LOCKED_STATUS": "no",
    "USERRIGHT_RECORD_VOICE": "no",
    "USERRIGHT_VIEW_HIDDEN_CHANNELS": "no",
}
LOGIN_CHANNEL_PATH: str = "/"
LOGIN_CHANNEL_PASSWORD: str = ""
CREATE_CHANNEL_PARENT_PATH: str = "/User_channel"
CHANNEL_DEFAULTS: Dict[str, Any] = {
    "max_users": 100,
    "disk_quota_mb": 100,
    "permanent": True,
    "hidden": False,
    "no_interruptions": False,
    "classroom": False,
    "operator_receive_only": False,
    "no_voice_activation": False,
    "no_recording": False,
}
AUDIO_DEFAULTS: Dict[str, Any] = {
    "application": "voip",
    "sample_rate": 48000,
    "channels": "mono",
    "bitrate_kbps": 64,
    "variable_bitrate": True,
    "ignore_silence": False,
    "transmit_interval_ms": 20,
    "frame_size_ms": 20,
    "fixed_audio_volume": False,
}
CHANNEL_INACTIVITY_TIMEOUT: Dict[str, Any] = {"value": 0, "unit": "days"}
CHANNEL_DELETION_WARNING_SECONDS: int = 30
BAN_TARGET: str = "USERNAME"
CHANNEL_CREATION_MAX_PER_USER: int = 1
CHANNEL_CREATION_LIMIT_MESSAGE: str = "You have reached the channel creation limit ({limit}). Remove an existing channel first."
CHANNEL_CREATION_BLOCKED_USERNAMES: List[str] = ["guest"]
CHANNEL_CREATION_BLOCKED_MESSAGE: str = "You are not permitted to create channels."
REGISTRATION_ALLOWED_USERNAMES: List[str] = ["guest"]
REGISTRATION_NOT_ALLOWED_MESSAGE: str = "You already have a registered account and do not need to register again."
# Accounts that many people log in with. They cannot be deleted via the bot,
# moderation tells their users apart by nickname + IP instead of username, and
# (unless they are admin accounts) they cannot create or manage channels.
SHARED_ACCOUNTS: List[str] = [
    "tamu", "murid", "hadirin", "osis", "pemateri", "f.osis", "guest",
]
REGISTRATION_IP_LIMIT: int = 1
REGISTRATION_IP_WINDOW_MINUTES: int = 2880
REGISTRATION_IP_LIMIT_MESSAGE: str = "Account creation failed: your IP has reached the registration limit. Please try again later."
ABUSE_LOGIN_ENABLED: bool = True
ABUSE_JOIN_ENABLED: bool = True
ABUSE_LOGIN_COUNT: int = 5
ABUSE_JOIN_COUNT: int = 5
ABUSE_WINDOW_SEC: int = 90
ABUSE_TEMP_BAN_MINUTES: int = 5
ABUSE_WHITELIST_FILE: str = "abuse_whitelist.txt"
ABUSE_LOGIN_WARNINGS: List[str] = [
    "First warning: stop spamming login/logout.",
    "Second warning: you will be kicked if you continue login spam.",
    "Third warning: you will be temporarily banned for {duration} minutes due to login spam.",
]
ABUSE_JOIN_WARNINGS: List[str] = [
    "First warning: stop spamming channel join/leave.",
    "Second warning: you will be kicked if you continue channel join spam.",
    "Third warning: you will be temporarily banned for {duration} minutes due to channel join spam.",
]
BADWORDS_ENABLED: bool = True
BADWORDS_INTERCEPT_TYPES: List[str] = ["CHANNEL"]
BADWORDS_FILE: str = "badwords/words.txt"
BADWORDS_IGNORE_ADMINS: bool = False
BADWORDS_PROFILE_CHECK_ENABLED: bool = True
BADWORD_ABUSE_COUNT: int = 1
BADWORDS_WARNINGS: List[str] = [
    "First warning: stop using offensive language.",
    "Second warning: continued offensive language will get you kicked.",
    "Third warning: you will be temporarily banned for {duration} minutes for continued offensive language.",
]
ANTISPAM_MESSAGE_ENABLED: bool = True
ANTISPAM_MESSAGE_COUNT: int = 7
ANTISPAM_MESSAGE_WINDOW_SEC: int = 10
ANTISPAM_INTERCEPT_TYPES: List[str] = ["CHANNEL", "PRIVATE"]
ANTISPAM_IGNORE_ADMINS: bool = True
ABUSE_MESSAGE_WARNINGS: List[str] = [
    "First warning: please do not spam messages.",
    "Second warning: you will be kicked if you continue spamming messages.",
    "Third warning: you will be temporarily banned for {duration} minutes due to message spam.",
]
EVENT_LOOP_WAIT_MS: int = 200
EVENT_LOOP_SLEEP_SEC: float = 0.01
RECONNECT_MAX_ATTEMPTS: int = 0
RECONNECT_RETRY_DELAY_SEC: int = 5
LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

DEFAULTS: Dict[str, Any] = {
    "TEAMTALK_LICENSE_NAME": TEAMTALK_LICENSE_NAME,
    "TEAMTALK_LICENSE_KEY": TEAMTALK_LICENSE_KEY,
    "SERVER_HOST": SERVER_HOST,
    "TCP_PORT": TCP_PORT,
    "UDP_PORT": UDP_PORT,
    "SERVER_ENCRYPTION_ENABLED": SERVER_ENCRYPTION_ENABLED,
    "TLS_CERTIFICATE_FILE": TLS_CERTIFICATE_FILE,
    "TLS_PRIVATE_KEY_FILE": TLS_PRIVATE_KEY_FILE,
    "TLS_CA_FILE": TLS_CA_FILE,
    "TLS_CA_DIR": TLS_CA_DIR,
    "TLS_VERIFY_SERVER": TLS_VERIFY_SERVER,
    "TLS_VERIFY_CLIENT_ONCE": TLS_VERIFY_CLIENT_ONCE,
    "TLS_VERIFY_DEPTH": TLS_VERIFY_DEPTH,
    "ADMIN_USERNAME": ADMIN_USERNAME,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "BOT_NICKNAME": BOT_NICKNAME,
    "CLIENT_NAME": CLIENT_NAME,
    "BOT_DEFAULT_STATUS_MESSAGE": BOT_DEFAULT_STATUS_MESSAGE,
    "BOT_DEFAULT_STATUS_MODE": BOT_DEFAULT_STATUS_MODE,
    "DEFAULT_USER_RIGHTS": DEFAULT_USER_RIGHTS,
    "LOGIN_CHANNEL_PATH": LOGIN_CHANNEL_PATH,
    "LOGIN_CHANNEL_PASSWORD": LOGIN_CHANNEL_PASSWORD,
    "CREATE_CHANNEL_PARENT_PATH": CREATE_CHANNEL_PARENT_PATH,
    "CHANNEL_DEFAULTS": CHANNEL_DEFAULTS,
    "AUDIO_DEFAULTS": AUDIO_DEFAULTS,
    "CHANNEL_INACTIVITY_TIMEOUT": CHANNEL_INACTIVITY_TIMEOUT,
    "CHANNEL_DELETION_WARNING_SECONDS": CHANNEL_DELETION_WARNING_SECONDS,
    "BAN_TARGET": BAN_TARGET,
    "CHANNEL_CREATION_MAX_PER_USER": CHANNEL_CREATION_MAX_PER_USER,
    "CHANNEL_CREATION_LIMIT_MESSAGE": CHANNEL_CREATION_LIMIT_MESSAGE,
    "CHANNEL_CREATION_BLOCKED_USERNAMES": CHANNEL_CREATION_BLOCKED_USERNAMES,
    "CHANNEL_CREATION_BLOCKED_MESSAGE": CHANNEL_CREATION_BLOCKED_MESSAGE,
    "REGISTRATION_ALLOWED_USERNAMES": REGISTRATION_ALLOWED_USERNAMES,
    "REGISTRATION_NOT_ALLOWED_MESSAGE": REGISTRATION_NOT_ALLOWED_MESSAGE,
    "SHARED_ACCOUNTS": SHARED_ACCOUNTS,
    "REGISTRATION_IP_LIMIT": REGISTRATION_IP_LIMIT,
    "REGISTRATION_IP_WINDOW_MINUTES": REGISTRATION_IP_WINDOW_MINUTES,
    "REGISTRATION_IP_LIMIT_MESSAGE": REGISTRATION_IP_LIMIT_MESSAGE,
    "ABUSE_LOGIN_ENABLED": ABUSE_LOGIN_ENABLED,
    "ABUSE_JOIN_ENABLED": ABUSE_JOIN_ENABLED,
    "ABUSE_LOGIN_COUNT": ABUSE_LOGIN_COUNT,
    "ABUSE_JOIN_COUNT": ABUSE_JOIN_COUNT,
    "ABUSE_WINDOW_SEC": ABUSE_WINDOW_SEC,
    "ABUSE_TEMP_BAN_MINUTES": ABUSE_TEMP_BAN_MINUTES,
    "ABUSE_WHITELIST_FILE": ABUSE_WHITELIST_FILE,
    "ABUSE_LOGIN_WARNINGS": ABUSE_LOGIN_WARNINGS,
    "ABUSE_JOIN_WARNINGS": ABUSE_JOIN_WARNINGS,
    "BADWORDS_ENABLED": BADWORDS_ENABLED,
    "BADWORDS_INTERCEPT_TYPES": BADWORDS_INTERCEPT_TYPES,
    "BADWORDS_FILE": BADWORDS_FILE,
    "BADWORDS_IGNORE_ADMINS": BADWORDS_IGNORE_ADMINS,
    "BADWORDS_PROFILE_CHECK_ENABLED": BADWORDS_PROFILE_CHECK_ENABLED,
    "BADWORD_ABUSE_COUNT": BADWORD_ABUSE_COUNT,
    "BADWORDS_WARNINGS": BADWORDS_WARNINGS,
    "ANTISPAM_MESSAGE_ENABLED": ANTISPAM_MESSAGE_ENABLED,
    "ANTISPAM_MESSAGE_COUNT": ANTISPAM_MESSAGE_COUNT,
    "ANTISPAM_MESSAGE_WINDOW_SEC": ANTISPAM_MESSAGE_WINDOW_SEC,
    "ANTISPAM_INTERCEPT_TYPES": ANTISPAM_INTERCEPT_TYPES,
    "ANTISPAM_IGNORE_ADMINS": ANTISPAM_IGNORE_ADMINS,
    "ABUSE_MESSAGE_WARNINGS": ABUSE_MESSAGE_WARNINGS,
    "EVENT_LOOP_WAIT_MS": EVENT_LOOP_WAIT_MS,
    "EVENT_LOOP_SLEEP_SEC": EVENT_LOOP_SLEEP_SEC,
    "RECONNECT_MAX_ATTEMPTS": RECONNECT_MAX_ATTEMPTS,
    "RECONNECT_RETRY_DELAY_SEC": RECONNECT_RETRY_DELAY_SEC,
    "LOG_LEVEL": LOG_LEVEL,
    "LOG_FORMAT": LOG_FORMAT,
    "LOG_DATE_FORMAT": LOG_DATE_FORMAT,
}


def _ensure_config_file() -> bool:
    if os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, "w", encoding="utf-8") as file_handle:
        json.dump(DEFAULTS, file_handle, indent=2)
    return True


CONFIG_CREATED = _ensure_config_file()


def _load_config() -> Dict[str, Any]:
    # Fail loudly: silently falling back to DEFAULTS would connect to the
    # default server with default credentials and drop every configured policy.
    try:
        # utf-8-sig tolerates the BOM some Windows editors add
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as file_handle:
            data = json.load(file_handle)
    except Exception as exc:
        raise RuntimeError(f"Failed to load {CONFIG_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{CONFIG_PATH} must contain an object at the top level.")
    merged = dict(DEFAULTS)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(DEFAULTS.get(key), dict):
            nested = dict(DEFAULTS[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


for _key, _value in _load_config().items():
    globals()[_key] = _value
