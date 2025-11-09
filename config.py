"""Configuration loader that merges defaults with overrides from config.json."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS: Dict[str, Any] = {
    "TEAMTALK_LICENSE_NAME": "",
    "TEAMTALK_LICENSE_KEY": "",
    "SERVER_HOST": "tt.infiartt.com",
    "TCP_PORT": 10333,
    "UDP_PORT": 10333,
    "SERVER_ENCRYPTION_ENABLED": False,
    "TLS_CERTIFICATE_FILE": "",
    "TLS_PRIVATE_KEY_FILE": "",
    "TLS_CA_FILE": "",
    "TLS_CA_DIR": "",
    "TLS_VERIFY_SERVER": False,
    "TLS_VERIFY_CLIENT_ONCE": False,
    "TLS_VERIFY_DEPTH": 0,
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin",
    "BOT_NICKNAME": "TeamTalk administration",
    "CLIENT_NAME": "Lanthera",
    "BOT_DEFAULT_STATUS_MESSAGE": "Send /help for assistance.",
    "BOT_DEFAULT_STATUS_MODE": 0,
    "DEFAULT_USER_RIGHTS": {
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
    },
    "LOGIN_CHANNEL_PATH": "/",
    "LOGIN_CHANNEL_PASSWORD": "",
    "CREATE_CHANNEL_PARENT_PATH": "/User_channel",
    "CHANNEL_DEFAULTS": {
        "max_users": 100,
        "disk_quota_mb": 100,
        "permanent": True,
        "hidden": False,
        "no_interruptions": False,
        "classroom": False,
        "operator_receive_only": False,
        "no_voice_activation": False,
        "no_recording": False,
    },
    "AUDIO_DEFAULTS": {
        "application": "voip",
        "sample_rate": 48000,
        "channels": "mono",
        "bitrate_kbps": 64,
        "variable_bitrate": True,
        "ignore_silence": False,
        "transmit_interval_ms": 20,
        "frame_size_ms": 20,
        "fixed_audio_volume": False,
    },
    "CHANNEL_INACTIVITY_TIMEOUT": {"value": 0, "unit": "days"},
    "CHANNEL_DELETION_WARNING_SECONDS": 30,
    "BAN_TARGET": "USERNAME",
    "CHANNEL_CREATION_MAX_PER_USER": 1,
    "CHANNEL_CREATION_LIMIT_MESSAGE": "You have reached the channel creation limit ({limit}). Remove an existing channel first.",
    "CHANNEL_CREATION_BLOCKED_USERNAMES": ["guest"],
    "CHANNEL_CREATION_BLOCKED_MESSAGE": "You are not permitted to create channels.",
    "REGISTRATION_ALLOWED_USERNAMES": ["guest"],
    "REGISTRATION_NOT_ALLOWED_MESSAGE": "You already have a registered account and do not need to register again.",
    "REGISTRATION_IP_LIMIT": 1,
    "REGISTRATION_IP_WINDOW_MINUTES": 2880,
    "REGISTRATION_IP_LIMIT_MESSAGE": "Account creation failed: your IP has reached the registration limit. Please try again later.",
    "ABUSE_LOGIN_COUNT": 5,
    "ABUSE_JOIN_COUNT": 5,
    "ABUSE_WINDOW_SEC": 90,
    "ABUSE_TEMP_BAN_MINUTES": 5,
    "ABUSE_LOGIN_WARNINGS": [
        "First warning: stop spamming login/logout.",
        "Second warning: you will be kicked if you continue login spam.",
        "Third warning: you will be temporarily banned for {duration} minutes due to login spam.",
    ],
    "ABUSE_JOIN_WARNINGS": [
        "First warning: stop spamming channel join/leave.",
        "Second warning: you will be kicked if you continue channel join spam.",
        "Third warning: you will be temporarily banned for {duration} minutes due to channel join spam.",
    ],
    "BADWORDS_ENABLED": True,
    "BADWORDS_INTERCEPT_TYPES": ["CHANNEL"],
    "BADWORDS_FILE": "badwords/words.txt",
    "BADWORDS_IGNORE_ADMINS": False,
    "BADWORDS_PROFILE_CHECK_ENABLED": True,
    "BADWORD_ABUSE_COUNT": 1,
    "BADWORDS_WARNINGS": [
        "First warning: stop using offensive language.",
        "Second warning: continued offensive language will get you kicked.",
        "Third warning: you will be temporarily banned for {duration} minutes for continued offensive language.",
    ],
    "EVENT_LOOP_WAIT_MS": 200,
    "EVENT_LOOP_SLEEP_SEC": 0.01,
    "RECONNECT_MAX_ATTEMPTS": 0,
    "RECONNECT_RETRY_DELAY_SEC": 5,
    "LOG_LEVEL": "INFO",
    "LOG_FORMAT": "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    "LOG_DATE_FORMAT": "%Y-%m-%d %H:%M:%S",
}


def _ensure_config_file() -> bool:
    if os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, "w", encoding="utf-8") as file_handle:
        json.dump(DEFAULTS, file_handle, indent=2)
    return True


CONFIG_CREATED = _ensure_config_file()


def _load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
            if not isinstance(data, dict):
                raise ValueError("config.json must contain an object at the top level.")
    except Exception:
        data = {}
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
