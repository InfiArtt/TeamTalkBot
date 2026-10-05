"""High-level wrapper around TeamTalk SDK providing bot behavior."""

from ctypes import byref
import logging
import heapq
import re
from typing import Dict, List, Any, Optional, Tuple
import time

from command_handler import parse_private_command
from help_texts import get_topic_help
from badwords import BadWordsFilter
from abuse_tracker import AbuseTracker
from abuse_whitelist import AbuseWhitelist
from ai_chat import AIChat
from ai_review import AIReviewer, INSULT, OK, OTHER, SAME
from collections import deque
import datetime
import abuse_menu
import badword_menu
import feature_toggles
import moderation_utils
import temp_ban_store
import channel_wizard
import cache_store
import config
import registration_throttle
import registration_wizard
import version
from tt_compat import assign_tt_char_array, from_tt_char, to_tt_char


# TeamTalk5 import notes:
# This file is imported after main.py adjusts the DLL path so TeamTalk5.dll can be located.
from TeamTalkPy.TeamTalk5 import (
    TeamTalk,
    TextMsgType,
    TextMessage,
    User,
    UserAccount,
    UserRight,
    UserType,
    Channel,
    ChannelType,
    TT_STRLEN,
    AudioCodec,
    OpusCodec,
    Codec,
    OPUS_APPLICATION_VOIP,
    buildTextMessage,
    BanType,
    BannedUser,
    Subscription,
    setLicense,
    ClientEvent,
)

logger = logging.getLogger(__name__)


def _resolve_user_rights(config_value) -> int:
    rights = 0

    if isinstance(config_value, dict):
        iterable = [
            name
            for name, enabled in config_value.items()
            if _config_bool(enabled, default=False)
        ]
    elif isinstance(config_value, (list, tuple, set)):
        iterable = config_value
    elif isinstance(config_value, str):
        iterable = [part.strip() for part in config_value.split(",")]
    elif config_value is None:
        iterable = []
    else:
        try:
            iterable = list(config_value)
        except Exception:
            logger.warning(
                "Unsupported DEFAULT_USER_RIGHTS type: %s", type(config_value)
            )
            iterable = []

    for name in iterable:
        key = str(name or "").strip()
        if not key:
            continue
        if not hasattr(UserRight, key):
            logger.warning("Unknown user right in config: %s", key)
            continue
        rights |= getattr(UserRight, key)

    return rights


def safe_call(fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("safe_call failed for %s", getattr(fn, "__name__", repr(fn)))
        return default


def _config_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default if value is None else bool(value)


class BotClient(TeamTalk):
    """TeamTalk client wrapper that implements all bot features."""

    _LICENSE_APPLIED = False
    _ACTIVE_LICENSE = None

    @staticmethod
    def _tt_value(value):
        return to_tt_char(value)

    @classmethod
    def _ensure_license(cls):
        if cls._LICENSE_APPLIED:
            return
        name = str(getattr(config, "TEAMTALK_LICENSE_NAME", "") or "").strip()
        key = str(getattr(config, "TEAMTALK_LICENSE_KEY", "") or "").strip()
        if not name or not key:
            logger.warning("TeamTalk SDK license not configured; running in demo mode.")
            return
        try:
            ok = setLicense(to_tt_char(name), to_tt_char(key))
        except Exception:
            ok = False
        if ok is None:
            ok = True
        if ok:
            cls._LICENSE_APPLIED = True
            cls._ACTIVE_LICENSE = name
            logger.info("TeamTalk SDK license applied for %s", name)
        else:
            logger.warning("Failed to apply TeamTalk SDK license from config. Running in demo mode.")

    def __init__(self):
        self._ensure_license()
        super().__init__()
        self._connected = False
        self._logged_in = False
        self._pending_cmd = (
            {}
        )  # cmdid -> {"requester": int, "desc": str, "notify": bool}
        self._confirmations = {}  # user_id -> { kind, data, desc, ts }
        self._pending_create_owner = {}  # cmdid -> { path, owner }
        self._pending_delete_path = {}  # cmdid -> path
        self._channel_wizards = {}  # user_id -> session dict
        self._registration_wizards = {}  # user_id -> session dict
        self._badword_menus = {}  # user_id -> /bw menu session dict
        self._abuse_menus = {}  # user_id -> /ab menu session dict
        # user_id -> entries of the last numbered list shown to that admin
        self._badword_list_snapshots: Dict[int, List[str]] = {}
        self._abuse_status_snapshots: Dict[int, List[tuple]] = {}
        self._whitelist_snapshots: Dict[int, List[str]] = {}
        self._username_checks = (
            {}
        )  # cmdid -> {"requester": int, "username": str, "found": bool}
        self._username_check_cmd_by_user = {}  # requester_id -> cmdid
        self._channel_expiry = {}  # path -> expiry_ts
        self._channel_expiry_warning = {}  # path -> bool
        self._reconnect_attempts = 0
        self._reconnect_scheduled = False
        # Min-heap for delayed/scheduled actions (timestamp, seq, fn, args, kwargs)
        self._scheduled = []
        self._scheduled_seq = 0
        # List-bans aggregation state (instance-level)
        self._list_bans_requester = None
        self._list_bans_buffer = []
        self._list_bans_cmdid = None
        # List-users aggregation state (instance-level)
        self._list_users_requester = None
        self._list_users_buffer = []
        self._list_users_cmdid = None
        # Admin on/off switches that override config.json (see /abt)
        known_features = {name for name, _desc, _key in self._FEATURES}
        self._feature_overrides: Dict[str, bool] = {
            k: v for k, v in (safe_call(feature_toggles.read, default={}) or {}).items()
            if k in known_features
        }
        # Second opinion on ambiguous badwords (see ai_review.py and /abt ai)
        self._ai = AIReviewer(
            getattr(config, "AI_CLOUDFLARE_ACCOUNT_ID", ""),
            getattr(config, "AI_CLOUDFLARE_API_TOKEN", ""),
            getattr(config, "AI_MODEL", ""),
            float(getattr(config, "AI_TIMEOUT_SEC", 10) or 10),
        )
        # Recent messages per conversation, kept in memory only while the AI
        # check is on, as context ("aku punya binatang baru" ... "anjing")
        self._recent_messages: Dict[tuple, deque] = {}
        self._message_seq = 0
        # (conversation, seq) of the message being handled right now
        self._current_message: Optional[Tuple[tuple, int]] = None
        # The "@ai" question in that message, if any; a badword in it holds the
        # answer back (see handle_badword_text)
        self._current_ai_question: Optional[dict] = None
        # "@ai <question>" in a channel (see /abt aichat)
        self._ai_chat = AIChat(
            getattr(config, "AI_CLOUDFLARE_ACCOUNT_ID", ""),
            getattr(config, "AI_CLOUDFLARE_API_TOKEN", ""),
            getattr(config, "AI_CHAT_MODEL", "") or getattr(config, "AI_MODEL", ""),
            max(30.0, float(getattr(config, "AI_TIMEOUT_SEC", 10) or 10)),
            int(getattr(config, "AI_CHAT_MAX_TOKENS", 300) or 300),
        )
        self._ai_chat_last: Dict[int, float] = {}  # user id -> last question time
        self._ai_chat_history: Dict[int, deque] = {}  # channel id -> (time, question, answer)
        self._ai_chat_day = ""
        self._ai_chat_count = 0
        # Init badwords and abuse tracker
        self._badwords = BadWordsFilter()
        try:
            badword_path = str(
                getattr(config, "BADWORDS_FILE", "badwords/words.txt")
                or "badwords/words.txt"
            )
        except Exception:
            badword_path = "badwords/words.txt"
        try:
            self._badwords.load_file(badword_path)
        except Exception:
            logger.warning("Failed to load badwords file: %s", badword_path)
        self._abuse = AbuseTracker(
            login_count=int(getattr(config, "ABUSE_LOGIN_COUNT", 10) or 10),
            join_count=int(getattr(config, "ABUSE_JOIN_COUNT", 10) or 10),
            badword_count=int(getattr(config, "BADWORD_ABUSE_COUNT", 1) or 1),
            window_sec=int(getattr(config, "ABUSE_WINDOW_SEC", 60) or 60),
            message_count=int(getattr(config, "ANTISPAM_MESSAGE_COUNT", 7) or 7),
            message_window_sec=int(getattr(config, "ANTISPAM_MESSAGE_WINDOW_SEC", 10) or 10),
        )
        self._temp_ban_minutes = int(
            getattr(config, "ABUSE_TEMP_BAN_MINUTES", 30) or 30
        )
        # Who each tracked (kind, key) refers to, for /abs and /abf
        self._abuse_subjects: Dict[Tuple[str, str], Dict[str, str]] = {}
        # abuse key -> when an admin forgave it; cancels kicks/bans still pending
        self._forgiven: Dict[str, float] = {}
        self._whitelist = AbuseWhitelist()
        whitelist_path = str(
            getattr(config, "ABUSE_WHITELIST_FILE", "") or "abuse_whitelist.txt"
        )
        try:
            self._whitelist.load_file(whitelist_path)
        except Exception:
            logger.warning("Failed to load abuse whitelist: %s", whitelist_path)
        # "<mode>:<label>" -> active temp ban; persisted so that a restart during
        # a ban cannot turn it into a permanent one
        try:
            self._temp_bans: Dict[str, Dict[str, Any]] = temp_ban_store.read()
        except Exception:
            self._temp_bans = {}
        self._subscription_cache: Dict[int, int] = {}
        self._bot_user_id: int = 0  # set after login; used to filter intercepted PMs
        # Holds the real nToUserID of the PM being processed in the current event loop
        # iteration.  The TeamTalk library strips this field before calling
        # onCmdUserTextMessage, so we capture it ourselves in runEventLoop.
        self._current_pm_to_user_id: int = 0
        # Buffers fragments of long text messages (bMore) until the final part
        self._text_fragments: Dict[tuple, Dict[str, Any]] = {}
        # Users answering a prompt (wizard/confirmation) -> last activity time.
        # Their PMs are not intercepted meanwhile; see _sync_prompt_intercepts.
        self._prompt_activity: Dict[int, float] = {}
        # While our own login is processed the server replays every online user
        # as logged-in/joined events; those are not user actions.
        self._login_cmdid = 0
        self._login_sync_until = 0.0
        # user_id -> when that session logged in (only logins seen live)
        self._session_login_ts: Dict[int, float] = {}
        # (username, ip) -> last logout time / times of recent reconnects
        self._recent_logouts: Dict[Tuple[str, str], float] = {}
        self._reconnect_history: Dict[Tuple[str, str], List[float]] = {}
        self._status_mode = None
        self._status_msg = None
        logger.info(
            "BotClient initialized (badwords_enabled=%s, abuse_window_sec=%s)",
            self._bw_enabled(),
            getattr(config, "ABUSE_WINDOW_SEC", 60),
        )
        try:
            self._registration_history = registration_throttle.read()
        except Exception:
            self._registration_history = {}
        self._registration_cleanup()
        try:
            now = self._now()
            for ip, payload in self._registration_history.items():
                if not isinstance(payload, dict):
                    continue
                try:
                    expiry = float(payload.get("expiry", 0) or 0)
                except Exception:
                    continue
                if expiry > now:
                    self._schedule_action(expiry, self._expire_registration_ip, ip)
        except Exception:
            pass
        self._initialize_channel_expiry()
        self._schedule_action(
            self._now() + self._PROMPT_CHECK_INTERVAL_SEC, self._prompt_housekeeping
        )
        self._schedule_action(
            self._now() + self._MODERATION_HOUSEKEEPING_SEC,
            self._moderation_housekeeping,
        )
        # Lift bans that were active before a restart (right away if overdue;
        # _lift_temp_ban waits for the login)
        for entry in list(self._temp_bans.values()):
            self._schedule_action(
                max(entry["until"], self._now() + 5),
                self._lift_temp_ban,
                entry["mode"],
                entry["label"],
                entry.get("key", ""),
                entry.get("kind", ""),
            )

    def connect(
        self,
        szHostAddress,
        nTcpPort,
        nUdpPort,
        nLocalTcpPort=0,
        nLocalUdpPort=0,
        bEncrypted=False,
    ):
        return super().connect(
            self._tt_value(szHostAddress),
            nTcpPort,
            nUdpPort,
            nLocalTcpPort,
            nLocalUdpPort,
            bEncrypted,
        )

    def doLogin(self, szNickname, szUsername, szPassword, szClientname):
        return super().doLogin(
            self._tt_value(szNickname),
            self._tt_value(szUsername),
            self._tt_value(szPassword),
            self._tt_value(szClientname),
        )

    def doJoinChannelByID(self, nChannelID: int, szPassword):
        return super().doJoinChannelByID(nChannelID, self._tt_value(szPassword))

    def doChangeNickname(self, szNewNick) -> int:
        return super().doChangeNickname(self._tt_value(szNewNick))

    def doChangeStatus(self, nStatusMode: int, szStatusMessage):
        return super().doChangeStatus(nStatusMode, self._tt_value(szStatusMessage))

    def doChannelOpEx(
        self, nUserID: int, nChannelID: int, szOpPassword, bMakeOperator: bool
    ):
        return super().doChannelOpEx(
            nUserID, nChannelID, self._tt_value(szOpPassword), bMakeOperator
        )

    def doBanIPAddress(self, szIPAddress, nChannelID: int) -> int:
        return super().doBanIPAddress(self._tt_value(szIPAddress), nChannelID)

    def doUnBanUser(self, szIPAddress, nChannelID: int) -> int:
        return super().doUnBanUser(self._tt_value(szIPAddress), nChannelID)

    def doDeleteUserAccount(self, szUsername) -> int:
        return super().doDeleteUserAccount(self._tt_value(szUsername))

    def doMakeChannel(self, channel: Channel) -> int:
        return super().doMakeChannel(byref(channel))

    def doNewUserAccount(self, account: UserAccount) -> int:
        return super().doNewUserAccount(byref(account))

    def doUnbanUserEx(self, banned_user: BannedUser) -> int:
        return super().doUnbanUserEx(byref(banned_user))

    def doBan(self, banned_user: BannedUser) -> int:
        return super().doBan(byref(banned_user))

    def getChannelIDFromPath(self, path):
        return super().getChannelIDFromPath(self._tt_value(path))

    def getChannelPath(self, channel_id: int) -> str:
        return from_tt_char(super().getChannelPath(channel_id))

    # ============ Feature switches (/abt) ============
    # name -> (description, config key holding the default). "pm" has no key of
    # its own: it adds or removes PRIVATE in the *_INTERCEPT_TYPES lists.
    _FEATURES = (
        ("login", "Login/logout spam detection", "ABUSE_LOGIN_ENABLED"),
        ("join", "Channel join/leave spam detection", "ABUSE_JOIN_ENABLED"),
        ("spam", "Message spam detection", "ANTISPAM_MESSAGE_ENABLED"),
        ("badwords", "Badword filter for messages", "BADWORDS_ENABLED"),
        ("profile", "Badword check of nicknames and status", "BADWORDS_PROFILE_CHECK_ENABLED"),
        ("pm", "Check private messages between users", None),
        ("ai", "AI decides on ambiguous badwords (e.g. anjing as a dog)", "AI_ENABLED"),
        ("aichat", "AI answers channel messages that start with @ai", "AI_CHAT_ENABLED"),
    )
    # Switches that need the Cloudflare credentials in config.json
    _AI_FEATURES = ("ai", "aichat")
    _DEFAULT_BW_TYPES = ["PRIVATE", "CHANNEL", "BROADCAST"]

    def _config_types(self, key: str, default: List[str]) -> set:
        try:
            vals = getattr(config, key, default) or []
            return {str(v).strip().upper() for v in vals if str(v).strip()}
        except Exception:
            return set(default)

    def _feature_default(self, name: str) -> bool:
        """The feature's state according to config.json."""
        if name == "pm":
            return "PRIVATE" in (
                self._config_types("BADWORDS_INTERCEPT_TYPES", self._DEFAULT_BW_TYPES)
                | self._config_types("ANTISPAM_INTERCEPT_TYPES", [])
            )
        if name == "profile":
            # In config.json the profile check also depends on BADWORDS_ENABLED
            return _config_bool(
                getattr(config, "BADWORDS_ENABLED", True), True
            ) and _config_bool(getattr(config, "BADWORDS_PROFILE_CHECK_ENABLED", True), True)
        key = {n: k for n, _desc, k in self._FEATURES}.get(name)
        return _config_bool(getattr(config, key, True), True) if key else True

    def _feature_on(self, name: str) -> bool:
        """Current state: an admin's /abt switch if set, else config.json."""
        if name in self._feature_overrides:
            return self._feature_overrides[name]
        return self._feature_default(name)

    def _with_pm_switch(self, types: set) -> set:
        if "pm" in self._feature_overrides:
            types = set(types)
            if self._feature_overrides["pm"]:
                types.add("PRIVATE")
            else:
                types.discard("PRIVATE")
        return types

    def _bw_enabled(self) -> bool:
        return self._feature_on("badwords")

    def _bw_profile_enabled(self) -> bool:
        return self._feature_on("profile")

    def _bw_types(self):
        return self._with_pm_switch(
            self._config_types("BADWORDS_INTERCEPT_TYPES", self._DEFAULT_BW_TYPES)
        )

    def _spam_enabled(self) -> bool:
        return self._feature_on("spam")

    def _spam_types(self):
        return self._with_pm_switch(self._config_types("ANTISPAM_INTERCEPT_TYPES", []))

    def _reconnect_delay_seconds(self) -> float:
        try:
            raw = getattr(config, "RECONNECT_RETRY_DELAY_SEC", 5) or 5
            delay = float(raw)
        except Exception:
            delay = 5.0
        return max(0.0, delay)

    def _reconnect_max_attempts(self) -> int:
        try:
            raw = getattr(config, "RECONNECT_MAX_ATTEMPTS", 0) or 0
            attempts = int(raw)
        except Exception:
            attempts = 0
        return max(0, attempts)

    # Internal helpers
    def _now(self) -> float:
        return time.time()

    def _join_login_channel(self):
        try:
            path = getattr(config, "LOGIN_CHANNEL_PATH", "") or ""
            password = getattr(config, "LOGIN_CHANNEL_PASSWORD", "") or ""
            if not path or path == "/":
                target_id = self.getRootChannelID()
            else:
                cid = self.getChannelIDFromPath(path)
                target_id = cid if (cid and cid > 0) else None
            if target_id is not None:
                self.doJoinChannelByID(target_id, password)
        except Exception:
            pass

    def _get_username(self, user_id: int) -> str:
        try:
            u = self.getUser(user_id)
            return from_tt_char(u.szUsername)
        except Exception:
            return ""

    def _normalize_channel_target_to_path(self, target: str) -> str:
        norm = (target or "").replace("\\", "/").strip()
        if "/" in norm:
            return norm.strip("/")
        base = getattr(config, "CREATE_CHANNEL_PARENT_PATH", "") or ""
        if base:
            return f"{base.strip('/')}/{norm}"
        return norm

    def _parse_badword_csv(self, csv_values: str) -> List[str]:
        if not csv_values:
            return []
        return [
            part.strip().lower()
            for part in csv_values.replace(",", " ").split()
            if part.strip()
        ]

    def _valid_tx_interval(self, ms: int) -> int:
        allowed = {
            20,
            40,
            60,
            80,
            100,
            120,
            140,
            160,
            180,
            200,
            220,
            240,
            260,
            280,
            300,
            320,
            340,
            360,
            380,
            400,
            420,
            440,
            460,
            480,
            500,
        }
        return ms if ms in allowed else 20

    def _valid_frame_size(self, ms: int) -> int:
        allowed = {0, 2, 5, 10, 20, 40, 60, 80, 100, 120}
        return ms if ms in allowed else 0

    # Helpers
    def send_pm(self, to_user_id: int, message: str):
        from_uid = self.getMyUserID() or 0
        # Use SDK helper to split long messages safely (UTF-16 aware)
        parts = buildTextMessage(
            content=str(message or ""),
            nMsgType=TextMsgType.MSGTYPE_USER,
            nToUserID=to_user_id,
            nFromUserID=from_uid,
        )
        for m in parts:
            try:
                self.doTextMessage(m)
            except Exception:
                # Try best-effort fallback: truncate if something goes wrong
                try:
                    tm = TextMessage()
                    tm.nMsgType = TextMsgType.MSGTYPE_USER
                    tm.nFromUserID = from_uid
                    tm.nToUserID = to_user_id
                    assign_tt_char_array((tm, "szMessage"), str(message)[:250])
                    self.doTextMessage(tm)
                except Exception:
                    pass

    def _sanitize_fullname(self, fullname: str) -> str:
        # Disallow delimiter and control characters; normalize whitespace
        s = (fullname or "").replace("|", " ")
        s = s.replace("\r", " ").replace("\n", " ")
        s = " ".join(s.split())  # collapse whitespace
        if not s:
            raise ValueError("Full name must not be empty")
        # Enforce 30 chars application limit and TT_STRLEN-1 safety
        maxlen = min(30, int(TT_STRLEN) - 1)
        if len(s) > maxlen:
            s = s[:maxlen]
        return s

    def _validate_username(self, username: str) -> str:
        s = (username or "").strip()
        if not s:
            raise ValueError("Username must not be empty")
        if any(ch in s for ch in ["|", " ", "\t", "\r", "\n"]):
            raise ValueError("Username must not contain spaces or the '|' character")
        maxlen = min(12, int(TT_STRLEN) - 1)
        if len(s) > maxlen:
            raise ValueError(f"Username is too long (max {maxlen} characters)")
        return s

    def _validate_password(self, password: str) -> str:
        s = (password or "").strip()
        if not s:
            raise ValueError("Password must not be empty")
        if any(ch in s for ch in ["|", " ", "\t", "\r", "\n"]):
            raise ValueError("Password must not contain spaces or the '|' character")
        # Length policy: 8-30
        if len(s) < 8:
            raise ValueError("Password is too short (minimum 8 characters)")
        if len(s) > 30:
            raise ValueError("Password is too long (max 30 characters)")
        # Complexity: upper, lower, symbol (non-alnum)
        if not any(ch.islower() for ch in s):
            raise ValueError("Password must contain a lowercase letter")
        if not any(ch.isupper() for ch in s):
            raise ValueError("Password must contain an uppercase letter")
        if not any(not ch.isalnum() for ch in s):
            raise ValueError("Password must contain a symbol")
        return s

    # Connection lifecycle
    def onConnectSuccess(self):
        self._connected = True
        self._reconnect_attempts = 0
        self._reconnect_scheduled = False
        self._start_login()
        logger.info(
            "Connected to server; issuing login request as %s", config.ADMIN_USERNAME
        )

    # Upper bound on the replay window in case the login's completion is missed
    _LOGIN_SYNC_MAX_SEC = 30.0

    def _start_login(self) -> int:
        cmdid = self.doLogin(
            config.BOT_NICKNAME,
            config.ADMIN_USERNAME,
            config.ADMIN_PASSWORD,
            config.CLIENT_NAME,
        )
        self._login_cmdid = cmdid if cmdid and cmdid > 0 else 0
        self._login_sync_until = self._now() + self._LOGIN_SYNC_MAX_SEC
        return cmdid

    def onCmdProcessing(self, cmdId: int, complete: bool):
        # The server sends all online users inside our login command; once it
        # completes, later login/join events are real user actions
        if complete and cmdId and cmdId == self._login_cmdid:
            self._login_cmdid = 0
            self._login_sync_until = 0.0
            logger.debug("Initial user list received; login/join tracking active")

    def runEventLoop(self, nWaitMSec: int = -1):
        """Override to attempt capturing nToUserID before the SDK processes the event.

        Unfortunately TeamTalk SDK always delivers ``textmessage.nToUserID = 0``
        for received PM events (both direct PMs and intercepts), so this field
        cannot reliably distinguish the two cases.  The override is kept as a
        hook for future SDK versions that may populate this field.
        """
        super().runEventLoop(nWaitMSec)

    def _clear_pending_states(self):
        """Clean up all wizard sessions, pending commands, and temp buffers upon connection changes to avoid leaks."""
        self._pending_cmd.clear()
        self._confirmations.clear()
        self._pending_create_owner.clear()
        self._pending_delete_path.clear()
        self._channel_wizards.clear()
        self._registration_wizards.clear()
        self._badword_menus.clear()
        self._abuse_menus.clear()
        self._badword_list_snapshots.clear()
        self._abuse_status_snapshots.clear()
        self._whitelist_snapshots.clear()
        self._username_checks.clear()
        self._username_check_cmd_by_user.clear()
        self._list_bans_buffer.clear()
        self._list_users_buffer.clear()
        self._subscription_cache.clear()
        self._text_fragments.clear()
        self._prompt_activity.clear()
        self._session_login_ts.clear()
        self._recent_logouts.clear()
        self._recent_messages.clear()
        self._bot_user_id = 0
        logger.info("Cleared all pending states and wizard sessions.")

    def disconnect(self):
        self._clear_pending_states()
        try:
            return super().disconnect()
        except Exception:
            pass

    def onConnectFailed(self):
        self._connected = False
        self._clear_pending_states()
        self._queue_reconnect()
        logger.warning("Connection attempt failed; retry scheduled")

    def onConnectionLost(self):
        self._connected = False
        self._logged_in = False
        self._clear_pending_states()
        self._queue_reconnect()
        logger.warning("Connection lost; reconnect scheduled")

    def onCmdMyselfLoggedIn(self, userid, useraccount):
        self._logged_in = True
        self._bot_user_id = int(userid or 0)
        self._subscription_cache.clear()
        self._status_mode = None
        self._status_msg = None
        # Auto-join the configured channel path (or root if unset)
        self._join_login_channel()
        # Set default status
        status_msg = getattr(config, "BOT_DEFAULT_STATUS_MESSAGE", "") or ""
        status_mode = int(getattr(config, "BOT_DEFAULT_STATUS_MODE", 0) or 0)
        if status_msg:
            safe_call(self.doChangeStatus, status_mode, status_msg)
        # Subscribe text messages from all users so we can see messages across channels
        safe_call(self._subscribe_text_from_all)
        logger.info("Logged in as %s (user ID %s)", config.ADMIN_USERNAME, userid)

    def onCmdMyselfLoggedOut(self):
        self._logged_in = False
        self._subscription_cache.clear()
        self._status_mode = None
        self._status_msg = None
        # Try to re-login on same connection
        safe_call(self._start_login)
        logger.info("Logged out from server; attempting re-login")

    def onCmdMyselfKickedFromChannel(self, channelid: int, user: User):
        # Attempt to re-join default login channel
        self._join_login_channel()

    def _queue_reconnect(self, immediate=False):
        if self._connected:
            return
        if self._reconnect_scheduled:
            return
        delay = 0.0 if immediate else self._reconnect_delay_seconds()
        self._reconnect_scheduled = True
        when = self._now() + delay
        self._schedule_action(when, self._attempt_reconnect)
        logger.info("Reconnect attempt scheduled in %.1f seconds", delay)

    def _attempt_reconnect(self):
        self._reconnect_scheduled = False
        if self._connected:
            logger.debug("Reconnect attempt skipped; connection already restored")
            return
        max_attempts = self._reconnect_max_attempts()
        if max_attempts and self._reconnect_attempts >= max_attempts:
            logger.error(
                "Reconnect aborted after reaching max attempts (%s)", max_attempts
            )
            self._shutdown_after_reconnect_failure()
            return
        self._reconnect_attempts += 1
        attempt_label = (
            f"{self._reconnect_attempts}/{max_attempts}"
            if max_attempts
            else str(self._reconnect_attempts)
        )
        logger.info("Attempting reconnect (%s)", attempt_label)
        try:
            super().disconnect()
        except Exception:
            pass
        use_encryption = _config_bool(
            getattr(config, "SERVER_ENCRYPTION_ENABLED", False)
        )
        try:
            ok = self.connect(
                config.SERVER_HOST,
                config.TCP_PORT,
                config.UDP_PORT,
                0,
                0,
                use_encryption,
            )
        except Exception:
            ok = False
            logger.exception(
                "Reconnect attempt %s raised an exception", self._reconnect_attempts
            )
        if not ok:
            if max_attempts and self._reconnect_attempts >= max_attempts:
                logger.error(
                    "Reconnect attempt %s failed; giving up after reaching limit",
                    self._reconnect_attempts,
                )
                self._shutdown_after_reconnect_failure()
                return
            delay = self._reconnect_delay_seconds()
            logger.warning(
                "Reconnect attempt %s failed to start; retrying in %.1f seconds",
                self._reconnect_attempts,
                delay,
            )
            self._queue_reconnect()
        else:
            logger.debug("Reconnect attempt %s initiated", self._reconnect_attempts)

    def _shutdown_after_reconnect_failure(self):
        logger.critical("Reconnect attempts exhausted; shutting down bot process")
        try:
            super().disconnect()
        except Exception:
            pass
        raise SystemExit(1)

    # Command handling via private messages
    def onCmdUserLoggedIn(self, user: User):
        try:
            self._subscription_cache.pop(user.nUserID, None)
        except Exception:
            pass
        if not self._in_login_sync():
            self._session_login_ts[user.nUserID] = self._now()
        safe_call(self._handle_abuse_login, user)
        if self._bw_profile_enabled():
            safe_call(moderation_utils.check_user_profile_badwords, self, user)
        # ensure we receive this user's text messages regardless of channel
        safe_call(self._subscribe_text_from_user, user.nUserID)
        logger.debug("Subscribed to user %s (login event)", user.nUserID)

    def onCmdUserJoinedChannel(self, user: User):
        safe_call(self._handle_abuse_join, user)
        if self._bw_profile_enabled():
            safe_call(moderation_utils.check_user_profile_badwords, self, user)
        safe_call(self._subscribe_text_from_user, user.nUserID)
        logger.debug("Subscribed to user %s (join event)", user.nUserID)
        self._refresh_channel_activity_by_user(user)

    def onCmdUserLoggedOut(self, user: User):
        try:
            self._subscription_cache.pop(user.nUserID, None)
            for key in [k for k in self._text_fragments if k[1] == user.nUserID]:
                self._text_fragments.pop(key, None)
            # Drop any prompt the user left open
            self._confirmations.pop(user.nUserID, None)
            self._channel_wizards.pop(user.nUserID, None)
            self._badword_menus.pop(user.nUserID, None)
            self._abuse_menus.pop(user.nUserID, None)
            self._badword_list_snapshots.pop(user.nUserID, None)
            self._abuse_status_snapshots.pop(user.nUserID, None)
            self._whitelist_snapshots.pop(user.nUserID, None)
            if user.nUserID in self._registration_wizards:
                self._cancel_registration_wizard(user.nUserID, notify=False)
            self._prompt_activity.pop(user.nUserID, None)
            # Remember the logout so an immediate re-login counts as a reconnect
            self._session_login_ts.pop(user.nUserID, None)
            person = self._person_key(user)
            if person:
                now = self._now()
                self._recent_logouts[person] = now
                for key in [
                    k
                    for k, ts in self._recent_logouts.items()
                    if now - ts > self._RECONNECT_WINDOW_SEC
                ]:
                    self._recent_logouts.pop(key, None)
        except Exception:
            pass

    def onCmdUserUpdate(self, user: User):
        if self._bw_profile_enabled():
            safe_call(moderation_utils.check_user_profile_badwords, self, user)
        try:
            myid = self.getMyUserID() or 0
        except Exception:
            myid = 0
        if myid and user.nUserID == myid:
            try:
                self._status_mode = user.nStatusMode
                self._status_msg = from_tt_char(user.szStatusMsg).strip()
            except Exception:
                pass
        safe_call(self._subscribe_text_from_user, user.nUserID)
        logger.debug("Subscribed to user %s (update event)", user.nUserID)

    # Fragments buffered per message before it is processed anyway, so a client
    # that never sends the final part still counts towards anti-spam.
    _MAX_TEXT_FRAGMENTS = 10
    _TEXT_FRAGMENT_TTL_SEC = 30.0

    def _assemble_text_message(self, textmessage: TextMessage) -> Optional[str]:
        """Join multi-part messages; return None while more fragments are due.

        The SDK splits long messages into several events flagged with ``bMore``.
        Handling each fragment on its own made one pasted text count as several
        messages for anti-spam and fed each part to wizards separately.
        """
        part = from_tt_char(textmessage.szMessage)
        key = (
            int(textmessage.nMsgType),
            int(textmessage.nFromUserID),
            int(textmessage.nToUserID),
            int(textmessage.nChannelID),
        )
        now = self._now()
        entry = self._text_fragments.get(key)
        if entry and now - entry["ts"] > self._TEXT_FRAGMENT_TTL_SEC:
            entry = None  # leftovers from a message that was never finished
        if getattr(textmessage, "bMore", False):
            if entry is None:
                entry = {"parts": [], "ts": now}
                self._text_fragments[key] = entry
            entry["parts"].append(part)
            entry["ts"] = now
            if len(entry["parts"]) < self._MAX_TEXT_FRAGMENTS:
                return None
            self._text_fragments.pop(key, None)
            return "".join(entry["parts"])
        self._text_fragments.pop(key, None)
        if entry is None:
            return part
        return "".join(entry["parts"]) + part

    def onCmdUserTextMessage(self, textmessage: TextMessage):
        try:
            message_text = self._assemble_text_message(textmessage)
            if message_text is None:
                return
            from_uid = textmessage.nFromUserID
            self._current_message = self._remember_message(textmessage, message_text)
            self._current_ai_question = None
            if (
                textmessage.nMsgType == TextMsgType.MSGTYPE_CHANNEL
                and from_uid != (self.getMyUserID() or 0)
                and self._ai_chat_active()
            ):
                question = self._ai_chat_question(message_text)
                if question is not None:
                    # The badword check below may hold the answer back
                    self._current_ai_question = {
                        "userid": from_uid,
                        "channel": int(textmessage.nChannelID),
                        "question": question,
                        "held": False,
                        "is_pm": False,
                    }
            # Filter bad words in channel / broadcast messages if configured
            if textmessage.nMsgType != TextMsgType.MSGTYPE_USER:
                if self._bw_enabled():
                    types = self._bw_types()
                    if textmessage.nMsgType == TextMsgType.MSGTYPE_CHANNEL and (
                        "CHANNEL" in types
                    ):
                        moderation_utils.check_text_badwords(
                            self, textmessage, message_text
                        )
                    elif textmessage.nMsgType == TextMsgType.MSGTYPE_BROADCAST and (
                        "BROADCAST" in types
                    ):
                        moderation_utils.check_text_badwords(
                            self, textmessage, message_text
                        )
                
                # Check antispam for channel / broadcast
                if self._spam_enabled():
                    spam_types = self._spam_types()
                    if textmessage.nMsgType == TextMsgType.MSGTYPE_CHANNEL and (
                        "CHANNEL" in spam_types
                    ):
                        self._handle_abuse_message(from_uid, self._get_user_ip(from_uid))
                    elif textmessage.nMsgType == TextMsgType.MSGTYPE_BROADCAST and (
                        "BROADCAST" in spam_types
                    ):
                        self._handle_abuse_message(from_uid, self._get_user_ip(from_uid))
                ask = self._current_ai_question
                if ask and not ask["held"]:
                    self._handle_ai_question(
                        ask["userid"],
                        ask["channel"],
                        ask["question"],
                        is_pm=bool(ask.get("is_pm", False)),
                    )
                return
            from_user = textmessage.nFromUserID
            content = message_text.strip()
            if from_user in self._prompt_activity:
                self._prompt_activity[from_user] = self._now()
            # Handle pending confirmations first
            pending = self._confirmations.get(from_user)
            if pending:
                # expire after _CONFIRMATION_TIMEOUT_SEC
                if self._now() - pending.get("ts", 0) > self._CONFIRMATION_TIMEOUT_SEC:
                    self._confirmations.pop(from_user, None)
                else:
                    low = content.lower()
                    if low in ("y", "ya", "yes"):
                        kind = pending.get("kind")
                        data = pending.get("data", {})
                        self._confirmations.pop(from_user, None)
                        if kind == "delete_channel":
                            self._perform_delete_channel(from_user, data["path"])  # type: ignore[index]
                            return
                        if kind == "delete_user":
                            self._perform_delete_user(from_user, data["username"], bool(data.get("kick_self")))  # type: ignore[attr-defined]
                            return
                    elif low in ("n", "no", "tidak"):
                        desc = pending.get("desc", "action")
                        self._confirmations.pop(from_user, None)
                        self.send_pm(from_user, f"Cancelled: {desc}")
                        logger.debug(
                            "Confirmation cancelled for user %s (%s)", from_user, desc
                        )
                        return
                    else:
                        self.send_pm(
                            from_user,
                            "Confirmation not recognized. Reply with 'y' for yes or 'n' for no.",
                        )
                        logger.debug(
                            "User %s provided unknown confirmation response: %s",
                            from_user,
                            content,
                        )
                        return
            # Check if user is in an active wizard — route input regardless of slash prefix
            if from_user in self._registration_wizards:
                registration_wizard.handle_response(self, from_user, content)
                return
            if from_user in self._channel_wizards:
                channel_wizard.handle_response(self, from_user, content)
                return
            # Menus return False when another /command closed them
            if from_user in self._badword_menus:
                if badword_menu.handle_response(self, from_user, content):
                    return
            if from_user in self._abuse_menus:
                if abuse_menu.handle_response(self, from_user, content):
                    return
            # Check if this PM is an @ai question (e.g. "@ai apa itu fotosintesis?" or "/ai ...")
            ai_pm_question = None
            if self._ai_chat_active() and from_user != (self.getMyUserID() or 0):
                if content.lower().startswith("/ai ") or content.lower() == "/ai":
                    ai_pm_question = content[3:].lstrip(" :,\t\n").strip()
                else:
                    ai_pm_question = self._ai_chat_question(content)

            if ai_pm_question is not None and self._ai_chat_active() and from_user != (self.getMyUserID() or 0):
                u = safe_call(self.getUser, from_user)
                user_chan = u.nChannelID if (u is not None and hasattr(u, "nChannelID")) else 1
                self._current_ai_question = {
                    "userid": from_user,
                    "channel": user_chan,
                    "question": ai_pm_question,
                    "held": False,
                    "is_pm": True,
                }
                if (
                    self._bw_enabled()
                    and ("PRIVATE" in self._bw_types())
                    and not self._is_badword_admin_command(from_user, content)
                ):
                    moderation_utils.check_text_badwords(self, textmessage, message_text)

                if self._spam_enabled():
                    spam_types = self._spam_types()
                    if "PRIVATE" in spam_types:
                        self._handle_abuse_message(from_uid, self._get_user_ip(from_uid))

                ask = self._current_ai_question
                if ask and not ask["held"]:
                    self._handle_ai_question(ask["userid"], ask["channel"], ask["question"], is_pm=True)
                return

            # Routing heuristic for private messages.
            #
            # The TeamTalk SDK always delivers textmessage.nToUserID = 0 for received
            # PM events — both for PMs sent directly to this bot and for PMs between
            # other users that arrive because the bot has SUBSCRIBE_INTERCEPT_USER_MSG.
            # It is therefore impossible to determine the intended recipient from the
            # message data alone.
            #
            # Rules applied:
            #   1. Always run the badword filter for PRIVATE messages when enabled.
            #   2. Always run the antispam filter for PRIVATE messages when enabled.
            #   3. For non-slash messages: stop processing (they are likely casual chat).
            #   4. For slash messages: attempt command parsing.
            if (
                self._bw_enabled()
                and ("PRIVATE" in self._bw_types())
                and not self._is_badword_admin_command(from_user, content)
            ):
                moderation_utils.check_text_badwords(self, textmessage, message_text)
                
            if self._spam_enabled():
                spam_types = self._spam_types()
                if "PRIVATE" in spam_types:
                    self._handle_abuse_message(from_uid, self._get_user_ip(from_uid))
                    
            if not content.startswith("/"):
                return
            parsed = parse_private_command(content)
            if not parsed:
                self.send_pm(
                    from_user,
                    "Command not recognized. Send /help for the command list.",
                )
                logger.debug(
                    "User %s sent unknown command text: %s", from_user, content
                )
                return
            action, args = parsed
            logger.debug("Processing action '%s' from user %s", action, from_user)
            if action == "register_user":
                self._handle_register_user(from_user, args.get("username", ""))
            elif action == "create_channel":
                channel_wizard.start_wizard(self, from_user, args["name"])
            elif action == "delete_channel":
                self._handle_delete_channel(
                    from_user, args["target"], bool(args.get("force"))
                )
            elif action == "delete_user":
                self._handle_delete_user(
                    from_user, args["username"], bool(args.get("force"))
                )
            elif action == "list_channels":
                self._handle_list_channels(from_user, args.get("path", ""))
            elif action == "owner_check":
                self._handle_owner_check(from_user, args["target"])
            elif action == "set_owner":
                self._handle_set_owner(from_user, args["target"], args["username"])
            elif action == "transfer_owner":
                self._handle_transfer_owner(from_user, args["target"], args["username"])
            elif action == "badword_list":
                self._handle_badword_list(from_user, args.get("query", ""))
            elif action == "badword_menu":
                badword_menu.start(self, from_user)
            elif action == "badword_test":
                self._handle_badword_test(from_user, args["text"])
            elif action == "abuse_menu":
                abuse_menu.start(self, from_user)
            elif action == "abuse_status":
                self._handle_abuse_status(from_user)
            elif action == "abuse_forgive":
                self._handle_abuse_forgive(from_user, args["targets"])
            elif action == "abuse_whitelist":
                self._handle_abuse_whitelist(from_user, args.get("args", ""))
            elif action == "feature_toggle":
                self._handle_feature_toggle(from_user, args.get("args", ""))
            elif action == "temp_ban":
                self._handle_temp_ban(
                    from_user, args["names"], args["minutes"], args.get("reason", "")
                )
            elif action == "badword_add":
                self._handle_badword_add(from_user, args["words"])
            elif action == "badword_delete":
                self._handle_badword_delete(from_user, args["words"])
            elif action == "version_info":
                self._handle_version_info(from_user)
            elif action == "help":
                self._handle_help(from_user, args.get("topic", ""))
            elif action == "change_status":
                self._handle_change_status(from_user, args["status"])
            elif action == "kick_users":
                self._handle_kick_users(
                    from_user, args["names"], args.get("reason", "")
                )
            elif action == "ban_users":
                self._handle_ban_users(from_user, args["names"], args.get("reason", ""))
            elif action == "unban":
                self._handle_unban(from_user, args["values"])
            elif action == "list_bans":
                self._handle_list_bans(from_user)
            elif action == "list_users":
                self._handle_list_users(from_user)
        except Exception as e:
            # Report errors to the sender without exposing the traceback
            safe_call(self.send_pm, textmessage.nFromUserID, f"An error occurred: {e}")
        finally:
            self._current_message = None
            self._current_ai_question = None
            # The message may have opened or closed a prompt
            safe_call(self._sync_prompt_intercepts)

    # Server command results
    def onCmdError(self, cmdId: int, errmsg):
        info = self._pending_cmd.pop(cmdId, None)
        # Cancel owner bookkeeping, if any
        self._pending_create_owner.pop(cmdId, None)
        # Cancel any pending owner removal bookkeeping
        self._pending_delete_path.pop(cmdId, None)
        # list bans failure cleanup
        if getattr(self, "_list_bans_cmdid", None) == cmdId:
            self._list_bans_cmdid = None
            self._list_bans_requester = None
            self._list_bans_buffer = []
        if getattr(self, "_list_users_cmdid", None) == cmdId:
            self._list_users_cmdid = None
            self._list_users_requester = None
            self._list_users_buffer = []
        if isinstance(info, dict):
            requester_id = info.get("requester")
            desc = info.get("desc", "")
            logger.error(
                "Command failed (cmdId=%s, desc=%s, error_no=%s, requester=%s)",
                cmdId,
                desc,
                errmsg.nErrorNo,
                requester_id,
            )
            if info.get("notify") and requester_id:
                message = from_tt_char(errmsg.szErrorMsg)
                safe_call(
                    self.send_pm,
                    requester_id,
                    f"Failed: {desc} | {errmsg.nErrorNo}: {message}",
                )
        username_check = self._username_checks.pop(cmdId, None)
        if username_check:
            requester = username_check.get("requester")
            if (
                requester is not None
                and self._username_check_cmd_by_user.get(requester) == cmdId
            ):
                self._username_check_cmd_by_user.pop(requester, None)
                registration_wizard.handle_username_check_error(self, requester)

    def onCmdSuccess(self, cmdId: int):
        info = self._pending_cmd.pop(cmdId, None)
        if isinstance(info, dict):
            requester_id = info.get("requester")
            desc = info.get("desc", "")
            meta = info.get("meta") or {}
            suppress_success = bool(meta.get("suppress_default_success"))
            if info.get("notify") and requester_id and not suppress_success:
                safe_call(self.send_pm, requester_id, f"Success: {desc}")
            if suppress_success and requester_id:
                self._send_custom_success_message(requester_id, meta)
            reg_ip = meta.get("registration_ip")
            if reg_ip:
                self._registration_record_ip(reg_ip)
            deleted_username = meta.get("deleted_username")
            kick_after = bool(meta.get("kick_after_delete"))
            if deleted_username and kick_after:
                reason = "Account deleted"
                kicked = self._kick_user_by_username(deleted_username, reason)
                if not kicked:
                    logger.info(
                        "Deleted user '%s' not currently online; no kick issued",
                        deleted_username,
                    )
            logger.debug(
                "Command succeeded (cmdId=%s, desc=%s, requester=%s)",
                cmdId,
                desc,
                requester_id,
            )
        # Record owner for newly created channel
        chinfo = self._pending_create_owner.pop(cmdId, None)
        if chinfo:
            try:
                created = chinfo.get("created_at", self._now())
                cache_store.set_owner(
                    chinfo["path"],
                    chinfo["owner"],  # type: ignore[index]
                    created_at=created,
                    last_active_at=created,
                )
                self._schedule_channel_expiry(chinfo["path"])
            except Exception:
                pass
        # Remove owner info from cache when a channel is deleted
        dpath = self._pending_delete_path.pop(cmdId, None)
        if dpath:
            try:
                cache_store.delete_owner(dpath)
            except Exception:
                pass
            self._channel_expiry.pop(self._normalize_channel_path(dpath), None)
        # Flush list bans results when complete
        if getattr(self, "_list_bans_cmdid", None) == cmdId:
            req = self._list_bans_requester
            lines = self._list_bans_buffer or ["(empty)"]
            if req is not None:
                self._send_chunked_lines(req, lines)
            self._list_bans_cmdid = None
            self._list_bans_requester = None
            self._list_bans_buffer = []
        if getattr(self, "_list_users_cmdid", None) == cmdId:
            req = self._list_users_requester
            lines = self._list_users_buffer or ["(empty)"]
            if req is not None:
                self._send_chunked_lines(req, lines)
            self._list_users_cmdid = None
            self._list_users_requester = None
            self._list_users_buffer = []
        username_check = self._username_checks.pop(cmdId, None)
        if username_check:
            requester = username_check.get("requester")
            if (
                requester is not None
                and self._username_check_cmd_by_user.get(requester) == cmdId
            ):
                self._username_check_cmd_by_user.pop(requester, None)
                available = not username_check.get("found", False)
                registration_wizard.handle_username_check_result(
                    self, requester, available
                )

    def onBannedUser(self, banneduser):
        # Collect during list bans
        if getattr(self, "_list_bans_requester", None) is not None:
            try:
                ip = from_tt_char(banneduser.szIPAddress)
                user = from_tt_char(banneduser.szUsername)
                nick = from_tt_char(banneduser.szNickname)
                path = from_tt_char(banneduser.szChannelPath)
                when = from_tt_char(banneduser.szBanTime)
                types = int(banneduser.uBanTypes)
                tparts = []
                if types & BanType.BANTYPE_IPADDR:
                    tparts.append("IP")
                if types & BanType.BANTYPE_USERNAME:
                    tparts.append("USER")
                if types & BanType.BANTYPE_CHANNEL:
                    tparts.append("CHAN")
                line = f"[{'+'.join(tparts)}] user={user or '-'} ip={ip or '-'} nick={nick or '-'} path={path or '-'} time={when or '-'}"
                self._list_bans_buffer.append(line)
            except Exception:
                pass

    def onUserAccount(self, useraccount: UserAccount):
        try:
            super().onUserAccount(useraccount)
        except AttributeError:
            pass
        username = ""
        try:
            username = from_tt_char(useraccount.szUsername).strip()
        except Exception:
            username = ""
        if getattr(self, "_list_users_requester", None) is not None:
            try:
                role = (
                    "ADMIN"
                    if (useraccount.uUserType & UserType.USERTYPE_ADMIN)
                    else "DEFAULT"
                )
                last_login = from_tt_char(useraccount.szLastLoginTime).strip() or "-"
                init_channel = from_tt_char(useraccount.szInitChannel).strip()
                note = from_tt_char(useraccount.szNote).strip()
                parts = [
                    username or "(tanpa username)",
                    f"role: {role}",
                    f"last login: {last_login}",
                ]
                if init_channel:
                    parts.append(f"init: {init_channel}")
                if note:
                    parts.append(f"note: {note}")
                self._list_users_buffer.append(" | ".join(parts))
            except Exception:
                pass
        if self._username_checks:
            uname_lower = (username or "").strip().lower()
            if uname_lower:
                for info in self._username_checks.values():
                    if info and info.get("username") == uname_lower:
                        info["found"] = True

    # Actions
    def _get_user_ip(self, user_id: int) -> str:
        try:
            u = self.getUser(user_id)
            return from_tt_char(u.szIPAddress)
        except Exception:
            return ""

    def _send_chunked_lines(self, to_user: int, lines, header: str = ""):
        max_len = max(1, int(TT_STRLEN) - 4)
        buffer = header.strip()

        def flush():
            nonlocal buffer
            if buffer:
                self.send_pm(to_user, buffer)
                buffer = ""

        for raw_line in lines:
            line = str(raw_line or "")
            if not line:
                line = "-"
            candidate = f"{buffer}\n{line}" if buffer else line
            if len(candidate) >= max_len:
                flush()
                while len(line) >= max_len:
                    self.send_pm(to_user, line[: max_len - 1])
                    line = line[max_len - 1 :]
                buffer = line
            else:
                buffer = candidate

        flush()

    def _ban_target_is_ip(self) -> bool:
        # Any BAN_TARGET other than USERNAME (e.g. IPADDR, IPADDRESS) bans by IP;
        # ban and unban paths must share this check to stay consistent.
        mode = str(getattr(config, "BAN_TARGET", "USERNAME") or "").strip().upper()
        return mode != "USERNAME"

    def _abuse_key(self, user_id: int, ip: str) -> str:
        ip = str(ip or "").strip()
        return ip if ip else f"user:{user_id}"

    def _get_warning_message(self, kind: str, stage: int) -> str:
        try:
            if stage <= 0:
                return ""
            mapping = {
                "login": getattr(config, "ABUSE_LOGIN_WARNINGS", []),
                "join": getattr(config, "ABUSE_JOIN_WARNINGS", []),
                "badword": getattr(config, "BADWORDS_WARNINGS", []),
                "message": getattr(config, "ABUSE_MESSAGE_WARNINGS", []),
            }
            msgs = mapping.get(kind, []) or []
            idx = stage - 1
            if idx >= len(msgs):
                return ""
            msg = str(msgs[idx] or "")
            if "{duration}" in msg:
                try:
                    msg = msg.format(duration=self._temp_ban_minutes)
                except Exception:
                    pass
            return msg
        except Exception:
            return ""

    def _registration_allowed_usernames(self):
        allowed = set()
        try:
            values = getattr(config, "REGISTRATION_ALLOWED_USERNAMES", []) or []
        except Exception:
            values = []
        for name in values:
            try:
                key = str(name or "").strip().lower()
            except Exception:
                continue
            if key:
                allowed.add(key)
        return allowed

    def _registration_user_allowed(self, user: User) -> bool:
        allowed = self._registration_allowed_usernames()
        if not allowed:
            return True
        try:
            uname = from_tt_char(user.szUsername).strip().lower()
        except Exception:
            uname = ""
        return uname in allowed

    def _registration_window_seconds(self) -> float:
        try:
            minutes = int(getattr(config, "REGISTRATION_IP_WINDOW_MINUTES", 60) or 60)
        except Exception:
            minutes = 60
        return max(1, minutes) * 60.0

    def _registration_ip_limit(self) -> int:
        try:
            limit = int(getattr(config, "REGISTRATION_IP_LIMIT", 0) or 0)
        except Exception:
            limit = 0
        return max(0, limit)

    def _registration_cleanup(self):
        changed = False
        now = self._now()
        try:
            for ip, payload in list(self._registration_history.items()):
                if not isinstance(payload, dict):
                    self._registration_history.pop(ip, None)
                    changed = True
                    continue
                try:
                    expiry = float(payload.get("expiry", 0) or 0)
                except Exception:
                    expiry = 0.0
                if not expiry or now >= expiry:
                    self._registration_history.pop(ip, None)
                    changed = True
        except Exception:
            pass
        if changed:
            self._save_registration_history()

    def _registration_can_use_ip(self, ip: str) -> bool:
        if not ip:
            return True
        limit = self._registration_ip_limit()
        if limit <= 0:
            return True
        self._registration_cleanup()
        payload = self._registration_history.get(ip)
        if not isinstance(payload, dict):
            self._registration_history.pop(ip, None)
            self._save_registration_history()
            return True
        now = self._now()
        expiry = float(payload.get("expiry", 0) or 0)
        if not expiry or now >= expiry:
            self._registration_history.pop(ip, None)
            self._save_registration_history()
            return True
        count = int(payload.get("count", 0) or 0)
        return count < limit

    def _registration_record_ip(self, ip: str):
        if not ip:
            return
        self._registration_cleanup()
        limit = self._registration_ip_limit()
        if limit <= 0:
            return
        window = self._registration_window_seconds()
        now = self._now()
        payload = self._registration_history.get(ip)
        if isinstance(payload, dict):
            expiry = float(payload.get("expiry", 0) or 0)
            if expiry and now < expiry:
                count = int(payload.get("count", 0) or 0) + 1
                expiry_ts = expiry
            else:
                count = 1
                expiry_ts = now + window
        else:
            count = 1
            expiry_ts = now + window
        if limit > 0:
            count = min(count, limit)
        self._registration_history[ip] = {"count": count, "expiry": expiry_ts}
        self._save_registration_history()
        if expiry_ts > now:
            self._schedule_action(expiry_ts, self._expire_registration_ip, ip)

    def _registration_limit_message(self) -> str:
        try:
            msg = str(getattr(config, "REGISTRATION_IP_LIMIT_MESSAGE", "") or "")
            if "{limit}" in msg or "{window}" in msg:
                msg = msg.format(
                    limit=self._registration_ip_limit(),
                    window=int(self._registration_window_seconds() // 60),
                )
            return msg
        except Exception:
            return "Registration attempts from your IP have reached the limit. Please try again later."

    def _channel_ttl_seconds(self) -> float:
        policy = getattr(config, "CHANNEL_INACTIVITY_TIMEOUT", {}) or {}
        try:
            value = float(policy.get("value", 0) or 0)
        except Exception:
            value = 0.0
        if value <= 0:
            return 0.0
        unit = str(policy.get("unit", "days") or "").strip().lower()
        multipliers = {
            "minute": 60,
            "minutes": 60,
            "hour": 3600,
            "hours": 3600,
            "day": 86400,
            "days": 86400,
            "week": 604800,
            "weeks": 604800,
            "month": 2592000,
            "months": 2592000,
        }
        factor = multipliers.get(unit, 86400)
        return max(0.0, value * factor)

    def _channel_warning_delay(self) -> float:
        try:
            delay = float(getattr(config, "CHANNEL_DELETION_WARNING_SECONDS", 30) or 30)
        except Exception:
            delay = 30.0
        return max(0.0, delay)

    def _initialize_channel_expiry(self):
        ttl = self._channel_ttl_seconds()
        if ttl <= 0:
            return
        try:
            for path, record in cache_store.list_all_records():
                if record.get("owner"):
                    self._schedule_channel_expiry(path, record)
        except Exception:
            logger.exception("Failed to initialize channel expiry schedule")

    def _normalize_channel_path(self, path: str) -> str:
        return (path or "").replace("\\", "/").strip("/")

    def _schedule_channel_expiry(
        self, path: str, record: Optional[Dict[str, Any]] = None
    ):
        ttl = self._channel_ttl_seconds()
        if ttl <= 0:
            return
        norm = self._normalize_channel_path(path)
        if not norm:
            return
        info = record or cache_store.get_channel_info(norm)
        if not info:
            return
        last_active = info.get("last_active_at") or info.get("created_at")
        if not last_active:
            last_active = self._now()
        expiry = float(last_active) + ttl
        self._channel_expiry[norm] = expiry
        self._channel_expiry_warning.pop(norm, None)
        self._schedule_action(expiry, self._maybe_expire_channel, norm)
        logger.debug("Channel expiry scheduled (path=%s, expires_at=%s)", norm, expiry)

    def _maybe_expire_channel(self, path: str):
        ttl = self._channel_ttl_seconds()
        if ttl <= 0:
            return
        norm = self._normalize_channel_path(path)
        info = cache_store.get_channel_info(norm)
        if not info or not info.get("owner"):
            self._channel_expiry.pop(norm, None)
            return
        last_active = info.get("last_active_at") or info.get("created_at") or 0
        expiry_expected = self._channel_expiry.get(norm)
        actual_expiry = float(last_active) + ttl
        now = self._now()
        channel_id = self.getChannelIDFromPath(norm)
        if actual_expiry > now + 1:
            if not expiry_expected or abs(expiry_expected - actual_expiry) > 1:
                self._schedule_channel_expiry(norm, info)
            return
        owner_username = str(info.get("owner", "")).strip().lower()
        members = self._get_channel_users(channel_id) if channel_id else []
        owner_present = False
        others_present = False
        for member in members:
            try:
                uname = from_tt_char(member.szUsername).strip().lower()
            except Exception:
                continue
            if uname and uname == owner_username:
                owner_present = True
            else:
                others_present = True
        if owner_present:
            cache_store.touch_channel(norm, now)
            self._schedule_channel_expiry(norm)
            logger.debug("Channel expiry skipped; owner online (channel=%s)", norm)
            return
        warning_delay = self._channel_warning_delay()
        if others_present and warning_delay > 0:
            if not self._channel_expiry_warning.get(norm):
                self._channel_expiry_warning[norm] = True
                self._announce_channel_removal(norm, channel_id, warning_delay)
                self._schedule_action(
                    now + warning_delay, self._finalize_channel_expiry, norm
                )
                logger.info(
                    "Channel %s warned for inactivity; deletion in %s seconds",
                    norm,
                    warning_delay,
                )
            else:
                self._schedule_action(
                    now + warning_delay, self._finalize_channel_expiry, norm
                )
            return
        channel_id = self.getChannelIDFromPath(norm)
        if not channel_id:
            cache_store.delete_owner(norm)
            self._channel_expiry.pop(norm, None)
            logger.info("Channel %s missing on server; removed from cache", norm)
            return
        desc = f"auto-delete channel '{norm}' (inactive)"
        cmdid = self.doRemoveChannel(channel_id)
        if cmdid <= 0:
            logger.warning(
                "Failed to dispatch auto-delete for channel %s; rescheduling", norm
            )
            self._schedule_channel_expiry(norm)
            return
        self._track_pending_cmd(cmdid, 0, desc, notify=False)
        self._pending_delete_path[cmdid] = norm
        self._channel_expiry_warning.pop(norm, None)
        logger.info("Auto-delete dispatched for inactive channel %s", norm)

    def _finalize_channel_expiry(self, path: str):
        ttl = self._channel_ttl_seconds()
        if ttl <= 0:
            return
        norm = self._normalize_channel_path(path)
        info = cache_store.get_channel_info(norm)
        if not info or not info.get("owner"):
            self._channel_expiry_warning.pop(norm, None)
            return
        channel_id = self.getChannelIDFromPath(norm)
        if not channel_id:
            cache_store.delete_owner(norm)
            self._channel_expiry.pop(norm, None)
            self._channel_expiry_warning.pop(norm, None)
            return
        owner_username = str(info.get("owner", "")).strip().lower()
        members = self._get_channel_users(channel_id)
        owner_present = False
        for member in members:
            try:
                uname = from_tt_char(member.szUsername).strip().lower()
            except Exception:
                continue
            if uname and uname == owner_username:
                owner_present = True
                break
        if owner_present:
            cache_store.touch_channel(norm, self._now())
            self._schedule_channel_expiry(norm)
            self._channel_expiry_warning.pop(norm, None)
            logger.info("Channel %s spared; owner returned before deletion", norm)
            return
        desc = f"auto-delete channel '{norm}' (inactive)"
        cmdid = self.doRemoveChannel(channel_id)
        if cmdid <= 0:
            logger.warning("Auto-delete finalization failed for %s; retrying", norm)
            self._schedule_action(self._now() + 30, self._finalize_channel_expiry, norm)
            return
        self._track_pending_cmd(cmdid, 0, desc, notify=False)
        self._pending_delete_path[cmdid] = norm
        self._channel_expiry_warning.pop(norm, None)
        logger.info("Auto-delete finalized for inactive channel %s", norm)

    def _refresh_channel_activity(self, path: str):
        norm = self._normalize_channel_path(path)
        if not norm:
            return
        cache_store.touch_channel(norm, self._now())
        self._schedule_channel_expiry(norm)
        self._channel_expiry_warning.pop(norm, None)

    def _refresh_channel_activity_by_user(self, user: User):
        try:
            username = from_tt_char(user.szUsername).strip()
            channel_id = user.nChannelID
            if not username or not channel_id:
                return
            path = self.getChannelPath(channel_id)
            norm = self._normalize_channel_path(str(path))
        except Exception:
            return
        info = cache_store.get_channel_info(norm)
        if not info:
            return
        owner = str(info.get("owner", "")).strip().lower()
        if owner and owner == username.strip().lower():
            cache_store.touch_channel(norm, self._now())
            self._schedule_channel_expiry(norm)
            logger.debug(
                "Channel activity refreshed by owner (user=%s, channel=%s)",
                username,
                norm,
            )
            self._channel_expiry_warning.pop(norm, None)

    def _get_channel_users(self, channel_id: int) -> List[User]:
        if not channel_id:
            return []
        try:
            users = self.getServerUsers()
        except Exception:
            return []
        members = []
        for user in users:
            try:
                if user.nChannelID == channel_id:
                    members.append(user)
            except Exception:
                continue
        return members

    def _announce_channel_removal(
        self, path: str, channel_id: int, seconds_left: float
    ):
        try:
            my_id = self.getMyUserID() or 0
            origin_channel = self.getMyChannelID() or 0
        except Exception:
            return
        if not my_id or not channel_id:
            return
        try:
            self.doMoveUser(my_id, channel_id)
        except Exception:
            logger.exception("Failed to move bot to channel %s for warning", path)
            return
        self._schedule_action(
            self._now() + 1.0,
            self._send_channel_warning_message,
            path,
            channel_id,
            seconds_left,
            origin_channel,
        )

    def _send_channel_warning_message(
        self, path: str, channel_id: int, seconds_left: float, origin_channel: int
    ):
        message = (
            f"[Auto Cleanup] Channel '{path}' has been inactive and will be deleted in "
            f"{int(seconds_left)} seconds unless the owner joins."
        )
        try:
            my_id = self.getMyUserID() or 0
        except Exception:
            my_id = 0
        try:
            parts = buildTextMessage(
                message,
                TextMsgType.MSGTYPE_CHANNEL,
                nChannelID=channel_id,
                nFromUserID=my_id,
            )
            for part in parts:
                self.doTextMessage(part)
            logger.info("Sent channel deletion warning to %s", path)
        except Exception:
            logger.exception("Failed to send deletion warning to channel %s", path)
        self._schedule_action(
            self._now() + 1.0, self._return_to_channel, origin_channel
        )

    def _return_to_channel(self, channel_id: int):
        try:
            my_id = self.getMyUserID() or 0
        except Exception:
            return
        if not my_id:
            return
        if channel_id and channel_id != (self.getMyChannelID() or 0):
            try:
                self.doMoveUser(my_id, channel_id)
                return
            except Exception:
                logger.warning(
                    "Failed to move bot back to original channel %s", channel_id
                )
        self._join_login_channel()

    def _begin_username_check(self, requester_id: int, username: str) -> bool:
        try:
            cmdid = self.doListUserAccounts(0, 1000)
        except Exception:
            cmdid = 0
        if cmdid <= 0:
            return False
        prev = self._username_check_cmd_by_user.pop(requester_id, None)
        if prev:
            info = self._username_checks.get(prev)
            if info:
                info["requester"] = None
        self._username_checks[cmdid] = {
            "requester": requester_id,
            "username": (username or "").strip().lower(),
            "found": False,
        }
        self._username_check_cmd_by_user[requester_id] = cmdid
        return True

    def _complete_registration_wizard(self, requester_id: int):
        session = self._registration_wizards.get(requester_id)
        if not session:
            return
        username = session.get("username") or ""
        password = session.get("password") or ""
        fullname = session.get("fullname") or ""
        if not (username and password and fullname):
            self.send_pm(
                requester_id,
                "Registration details incomplete. Send /cancel to restart.",
            )
            logger.warning(
                "Registration wizard missing fields (requester=%s)", requester_id
            )
            session["step"] = "ask_username"
            return
        requester = safe_call(self.getUser, requester_id)
        if requester and not self._registration_user_allowed(requester):
            msg = str(
                getattr(
                    config,
                    "REGISTRATION_NOT_ALLOWED_MESSAGE",
                    "You already have a registered account.",
                )
                or "You already have a registered account."
            )
            self.send_pm(requester_id, msg)
            logger.info(
                "Registration wizard aborted by policy (requester=%s)", requester_id
            )
            self._cancel_registration_wizard(requester_id, notify=False)
            return
        ip = session.get("ip") or self._get_user_ip(requester_id)
        if not self._registration_can_use_ip(ip):
            msg = self._registration_limit_message()
            if msg:
                self.send_pm(requester_id, msg)
            logger.info(
                "Registration wizard blocked due to IP limit at submission (requester=%s, ip=%s)",
                requester_id,
                ip,
            )
            self._cancel_registration_wizard(requester_id, notify=False)
            return

        account = UserAccount()
        assign_tt_char_array((account, "szUsername"), username)
        assign_tt_char_array((account, "szPassword"), password)
        account.uUserType = UserType.USERTYPE_DEFAULT
        account.uUserRights = _resolve_user_rights(config.DEFAULT_USER_RIGHTS)
        assign_tt_char_array((account, "szNote"), fullname)
        assign_tt_char_array((account, "szInitChannel"), "")
        account.nAudioCodecBpsLimit = 0

        try:
            cmdid = self.doNewUserAccount(account)
        except Exception:
            cmdid = 0
        if cmdid <= 0:
            self.send_pm(
                requester_id,
                "Failed to send user registration command to the server. Please try again later.",
            )
            session["step"] = "ask_username"
            logger.error(
                "Registration wizard command dispatch failed (requester=%s, username=%s)",
                requester_id,
                username,
            )
            return
        meta = {"registration_ip": ip} if ip else {}
        meta.update(
            {
                "kind": "register_user",
                "username": username,
                "suppress_default_success": True,
            }
        )
        self._track_pending_cmd(
            cmdid, requester_id, f"register user '{username}'", meta=meta
        )
        logger.info(
            "Registration wizard dispatched command (requester=%s, username=%s, ip=%s)",
            requester_id,
            username,
            ip,
        )
        self._registration_wizards.pop(requester_id, None)

    def _cancel_registration_wizard(self, requester_id: int, notify: bool = True):
        session = self._registration_wizards.pop(requester_id, None)
        if session and notify:
            self.send_pm(requester_id, "Registration cancelled.")
            logger.info(
                "Registration wizard cancelled by user (requester=%s)", requester_id
            )
        cmdid = self._username_check_cmd_by_user.pop(requester_id, None)
        if cmdid:
            info = self._username_checks.get(cmdid)
            if info:
                info["requester"] = None

    def _save_registration_history(self):
        try:
            registration_throttle.write(self._registration_history)
        except Exception:
            pass

    def _expire_registration_ip(self, ip: str):
        if not ip:
            return
        payload = self._registration_history.get(ip)
        if not isinstance(payload, dict):
            return
        try:
            expiry = float(payload.get("expiry", 0) or 0)
        except Exception:
            expiry = 0.0
        now = self._now()
        if expiry and now < expiry:
            # Reschedule if triggered earlier than planned
            self._schedule_action(expiry, self._expire_registration_ip, ip)
            return
        self._registration_history.pop(ip, None)
        self._save_registration_history()

    def _channel_creation_blocklist(self):
        try:
            values = getattr(config, "CHANNEL_CREATION_BLOCKED_USERNAMES", []) or []
        except Exception:
            values = []
        result = set()
        for name in values:
            try:
                key = str(name or "").strip().lower()
            except Exception:
                continue
            if key:
                result.add(key)
        return result

    def _channel_creation_max_per_user(self) -> int:
        try:
            return max(0, int(getattr(config, "CHANNEL_CREATION_MAX_PER_USER", 0) or 0))
        except Exception:
            return 0

    def _channel_creation_blocked_message(self) -> str:
        try:
            return str(
                getattr(
                    config,
                    "CHANNEL_CREATION_BLOCKED_MESSAGE",
                    "You are not allowed to create channels.",
                )
                or "You are not allowed to create channels."
            )
        except Exception:
            return "You are not allowed to create channels."

    def _channel_creation_limit_message(self) -> str:
        try:
            msg = str(getattr(config, "CHANNEL_CREATION_LIMIT_MESSAGE", "") or "")
            if "{limit}" in msg:
                msg = msg.format(limit=self._channel_creation_max_per_user())
            if msg:
                return msg
        except Exception:
            pass
        return "Channel creation limit reached."

    def _channel_creation_owned_count(self, username: str) -> int:
        uname = str(username or "").strip()
        if not uname:
            return 0
        try:
            return cache_store.count_channels_by_owner(uname)
        except Exception:
            return 0

    def _channel_creation_pending_count(self, username: str) -> int:
        uname = str(username or "").strip().lower()
        if not uname:
            return 0
        count = 0
        try:
            for info in self._pending_create_owner.values():
                owner = str(info.get("owner", "")).strip().lower()
                if owner == uname:
                    count += 1
        except Exception:
            pass
        return count

    def _can_user_create_channel(self, requester_id: int) -> bool:
        username = self._get_username(requester_id) or ""
        uname_lower = username.strip().lower()
        if not uname_lower:
            self.send_pm(
                requester_id,
                "Your username could not be determined. Cannot create a channel.",
            )
            return False
        blocklist = self._channel_creation_blocklist()
        if blocklist and uname_lower in blocklist:
            self.send_pm(requester_id, self._channel_creation_blocked_message())
            return False
        if self._is_shared_account(uname_lower) and not self._is_admin(requester_id):
            # Owned by "murid", a channel would belong to every student
            self.send_pm(requester_id, self._SHARED_NO_CHANNELS)
            return False
        max_channels = self._channel_creation_max_per_user()
        if max_channels > 0:
            owned = self._channel_creation_owned_count(username)
            pending = self._channel_creation_pending_count(username)
            if (owned + pending) >= max_channels:
                self.send_pm(requester_id, self._channel_creation_limit_message())
                return False
        return True

    def _track_pending_cmd(
        self, cmdid: int, requester: int, desc: str, notify: bool = True, meta=None
    ):
        try:
            if cmdid <= 0:
                return
            self._pending_cmd[cmdid] = {
                "requester": requester,
                "desc": str(desc or ""),
                "notify": bool(notify),
                "meta": dict(meta or {}),
            }
            logger.debug(
                "Tracking pending command (cmdId=%s, desc=%s, requester=%s, notify=%s)",
                cmdid,
                desc,
                requester,
                notify,
            )
        except Exception:
            logger.exception(
                "Failed to track pending command (cmdId=%s, desc=%s)", cmdid, desc
            )

    def _execute_manual_kick(
        self,
        user_id: int,
        requester_id: int,
        nickname: str,
        reason: str,
        notify_admin: bool,
    ):
        try:
            if user_id == (self.getMyUserID() or 0):
                logger.debug("Skipping kick request for self (user=%s)", user_id)
                return
            cmdid = self.doKickUser(user_id, 0)
            if cmdid > 0:
                short_name = nickname or str(user_id)
                desc = f"kick user '{short_name}'"
                reason_text = str(reason or "").strip()
                if reason_text:
                    concise = (
                        reason_text
                        if len(reason_text) <= 60
                        else f"{reason_text[:57]}..."
                    )
                    desc += f" (reason: {concise})"
                self._track_pending_cmd(
                    cmdid,
                    requester_id if notify_admin else user_id,
                    desc,
                    notify=notify_admin,
                )
                logger.info(
                    "Kick requested for user=%s by requester=%s", user_id, requester_id
                )
        except Exception:
            logger.exception("Failed to execute manual kick for user %s", user_id)

    def _execute_manual_ban(
        self,
        user_id: int,
        requester_id: int,
        ban_type: int,
        label: str,
        nickname: str,
        reason: str,
    ):
        try:
            cmdid = self.doBanUserEx(user_id, ban_type)
            if cmdid > 0:
                tag = label or str(user_id)
                desc = f"ban user '{tag}'"
                reason_text = str(reason or "").strip()
                if reason_text:
                    concise = (
                        reason_text
                        if len(reason_text) <= 60
                        else f"{reason_text[:57]}..."
                    )
                    desc += f" (reason: {concise})"
                self._track_pending_cmd(cmdid, requester_id, desc)
                # Ensure banned user is disconnected shortly after ban applies
                self._schedule_action(
                    self._now() + 0.5,
                    self._execute_manual_kick,
                    user_id,
                    requester_id,
                    nickname,
                    "",
                    False,
                )
                logger.info(
                    "Ban requested for user=%s by requester=%s (label=%s)",
                    user_id,
                    requester_id,
                    label,
                )
        except Exception:
            logger.exception("Failed to execute manual ban for user %s", user_id)

    def _kick_user_for_abuse(self, userid: int, reason: str):
        try:
            if userid == (self.getMyUserID() or 0):
                logger.debug("Skipping abuse kick for self (user=%s)", userid)
                return
            cmdid = self.doKickUser(userid, 0)
            if cmdid > 0:
                self._track_pending_cmd(
                    cmdid, userid, f"kick user '{userid}' ({reason})", notify=False
                )
                logger.info("Scheduled abuse kick for user=%s (%s)", userid, reason)
        except Exception:
            logger.exception("Failed to schedule abuse kick for user %s", userid)

    def _apply_temp_ban(
        self,
        userid: int,
        ip: str,
        username: str,
        minutes: int,
        reason: str,
        key: str,
        kind: str,
        who: str = "",
        by: str = "",
        requester: int = 0,
    ) -> str:
        """Ban by IP or username (per BAN_TARGET) and schedule the automatic lift.

        Returns the banned label, or '' when no ban could be sent.
        """
        ip = str(ip or "").strip()
        username = str(username or "").strip()
        use_ip = self._ban_target_is_ip()
        # Fall back to the other ban type when the preferred label is unknown
        if use_ip and not ip and username:
            use_ip = False
        elif not use_ip and not username and ip:
            use_ip = True
        label = ip if use_ip else username
        if not label:
            return ""
        # Ban by IP/username instead of doBanUserEx(userid): the stage-2 kick
        # often disconnects the user first, and a user-ID ban then fails with
        # CMDERR_USER_NOT_FOUND, leaving the offender unbanned.
        if use_ip:
            mode = "IPADDR"
            cmdid = self.doBanIPAddress(label, 0)
        else:
            mode = "USERNAME"
            bu = BannedUser()
            assign_tt_char_array((bu, "szUsername"), label)
            bu.uBanTypes = BanType.BANTYPE_USERNAME
            cmdid = self.doBan(bu)
        if cmdid <= 0:
            return ""
        self._track_pending_cmd(
            cmdid,
            requester or userid,
            f"temp ban '{label}' ({reason})",
            notify=bool(requester),
        )
        until = self._now() + max(1, int(minutes)) * 60
        self._temp_bans[f"{mode}:{label}"] = {
            "mode": mode,
            "label": label,
            "until": until,
            "reason": reason,
            "key": key,
            "kind": kind,
            "who": who or username or label,
            "username": username,
            "by": by,
        }
        self._save_temp_bans()
        self._schedule_action(until, self._lift_temp_ban, mode, label, key, kind)
        return label

    def _issue_temp_ban(
        self,
        userid: int,
        ip: str,
        username: str,
        reason: str,
        key: str,
        kind: str,
        who: str = "",
    ):
        try:
            label = self._apply_temp_ban(
                userid, ip, username, self._temp_ban_minutes, reason, key, kind, who=who
            )
            if not label:
                return
            # Ensure user is removed immediately after ban is applied (with slight delay to flush warning)
            self._schedule_abuse_action(
                key, 3.0, self._kick_user_for_abuse, userid, reason
            )
            logger.info(
                "Temporary ban issued (user=%s, label=%s, duration=%s min)",
                userid,
                label,
                self._temp_ban_minutes,
            )
        except Exception:
            logger.exception("Failed to issue temporary ban for user %s", userid)

    def _schedule_abuse_action(self, key: str, delay: float, fn, *args):
        """Schedule an automatic kick/ban that /abf can still cancel."""
        now = self._now()
        self._schedule_action(now + delay, self._run_abuse_action, key, now, fn, *args)

    def _run_abuse_action(self, key: str, created_at: float, fn, *args):
        if self._forgiven.get(key, -1.0) >= created_at:
            logger.info(
                "Skipped %s for %s: forgiven by an admin", getattr(fn, "__name__", fn), key
            )
            return
        fn(*args)

    def _handle_abuse_stage(
        self, kind: str, stage: int, userid: int, ip: str, reason: str, key: str
    ):
        if stage <= 0:
            return
        msg = self._get_warning_message(kind, stage)
        if msg:
            safe_call(self.send_pm, userid, msg)
            logger.info(
                "%s abuse stage %s triggered for user=%s (ip=%s, key=%s)",
                kind,
                stage,
                userid,
                ip,
                key,
            )
        if stage == 2:
            self._schedule_abuse_action(
                key, 3.0, self._kick_user_for_abuse, userid, reason
            )
        elif stage == 3:
            # Resolve names now; the user may be gone when the ban runs
            username = self._get_username(userid)
            who = self._abuse_subjects.get((kind, key), {}).get("nick", "")
            self._schedule_abuse_action(
                key,
                3.0,
                self._issue_temp_ban,
                userid,
                ip,
                username,
                reason,
                key,
                kind,
                who,
            )

    def _lift_temp_ban(
        self, mode: str, label: str, key: str, kind: str, force: bool = False
    ):
        mode = "IPADDR" if str(mode or "").upper() in ("IPADDR", "IPADDRESS") else "USERNAME"
        entry_key = f"{mode}:{label}"
        entry = self._temp_bans.get(entry_key)
        if entry is None:
            # Already lifted by /abf or /ubn
            safe_call(self._abuse.reset, kind, key)
            return
        if not force and entry.get("until", 0) > self._now() + 1:
            return  # extended by a newer ban, whose own lift is scheduled
        if not self._logged_in:
            # Unbanning needs the admin session; try again shortly
            self._schedule_action(
                self._now() + 10, self._lift_temp_ban, mode, label, key, kind, force
            )
            return
        try:
            if mode == "IPADDR":
                cmdid = self.doUnBanUser(label, 0)
                desc = f"auto unban IP '{label}'"
            else:
                bu = BannedUser()
                assign_tt_char_array((bu, "szUsername"), label)
                bu.uBanTypes = BanType.BANTYPE_USERNAME
                assign_tt_char_array((bu, "szIPAddress"), "")
                assign_tt_char_array((bu, "szChannelPath"), "")
                assign_tt_char_array((bu, "szNickname"), "")
                assign_tt_char_array((bu, "szOwner"), "")
                cmdid = self.doUnbanUserEx(bu)
                desc = f"auto unban user '{label}'"
        except Exception:
            logger.exception("Failed to auto-unban %s (mode=%s)", label, mode)
            cmdid = 0
        if cmdid <= 0:
            self._schedule_action(
                self._now() + 30, self._lift_temp_ban, mode, label, key, kind, force
            )
            return
        self._track_pending_cmd(cmdid, 0, desc, notify=False)
        logger.info("Temp ban lifted for %s", label)
        self._temp_bans.pop(entry_key, None)
        self._save_temp_bans()
        safe_call(self._abuse.reset, kind, key)

    def _save_temp_bans(self):
        try:
            temp_ban_store.write(self._temp_bans)
        except Exception:
            logger.exception("Failed to save temp bans")

    def _forget_temp_ban(self, label: str):
        """Drop temp bans on ``label`` after an admin lifted them by hand."""
        target = str(label or "").strip().lower()
        stale = [k for k, e in self._temp_bans.items() if str(e.get("label", "")).lower() == target]
        for key in stale:
            self._temp_bans.pop(key, None)
        if stale:
            self._save_temp_bans()

    def _record_abuse(
        self, kind: str, key: str, user_id: int, ip: str, user: Optional[User] = None
    ) -> int:
        """Record an abuse event and return the new stage (0 if none).

        Users whose username or IP is whitelisted are never recorded.
        """
        if user is None:
            user = safe_call(self.getUser, user_id)
        try:
            username = from_tt_char(user.szUsername).strip() if user is not None else ""
            nick = from_tt_char(user.szNickname).strip() if user is not None else ""
        except Exception:
            username = nick = ""
        if self._whitelist.covers(username, ip):
            return 0
        self._abuse_subjects[(kind, key)] = {
            "username": username,
            "nick": nick or username or f"user {user_id}",
            "ip": str(ip or ""),
        }
        return self._abuse.record(kind, key)

    def _person_abuse_key(self, user: User) -> str:
        """Abuse key for one person (username + IP), falling back to the IP."""
        person = self._person_key(user)
        if person:
            return f"{person[0]}@{person[1]}"
        return self._abuse_key(user.nUserID, from_tt_char(user.szIPAddress))

    def _ai_active(self) -> bool:
        return self._feature_on("ai") and self._ai.configured

    def _ambiguous_words(self) -> set:
        values = getattr(config, "AI_AMBIGUOUS_WORDS", []) or []
        return {str(v).strip().lower() for v in values if str(v).strip()}

    # Context for the AI: messages this old or newer, and a short wait so a
    # follow-up ("anjing" ... "lucu banget, baru lahir") is included too
    _AI_CONTEXT_WINDOW_SEC = 120.0
    _AI_CONTEXT_WAIT_SEC = 8.0
    _AI_CONTEXT_AFTER = 2

    def _ai_context_size(self) -> int:
        try:
            return max(0, int(getattr(config, "AI_CONTEXT_MESSAGES", 4) or 0))
        except Exception:
            return 0

    def _remember_message(self, textmessage, text: str) -> Optional[Tuple[tuple, int]]:
        """Keep a message in memory as AI context; returns (conversation, seq).

        Channel messages are grouped per channel (everyone in it); private
        messages per sender, since the SDK does not say who they were for.
        """
        if not self._ai_active() or self._ai_context_size() <= 0:
            return None
        if textmessage.nMsgType == TextMsgType.MSGTYPE_CHANNEL:
            key = ("channel", int(textmessage.nChannelID))
        elif textmessage.nMsgType == TextMsgType.MSGTYPE_USER:
            key = ("pm", int(textmessage.nFromUserID))
        else:
            return None
        uid = int(textmessage.nFromUserID)
        stripped = (text or "").strip()
        # Never keep commands or answers to the bot's prompts (e.g. passwords)
        if (
            not stripped
            or stripped.startswith("/")
            or uid == (self.getMyUserID() or 0)
            or uid in self._users_in_prompt()
        ):
            return None
        self._message_seq += 1
        self._recent_messages.setdefault(key, deque(maxlen=12)).append(
            (self._message_seq, self._now(), uid, stripped)
        )
        return key, self._message_seq

    def _conversation_around(self, current: Tuple[tuple, int], userid: int):
        """(before, after) messages around ``current`` as (speaker, text) pairs."""
        key, seq = current
        items = list(self._recent_messages.get(key, ()))
        flagged_ts = next((ts for s, ts, _uid, _text in items if s == seq), self._now())

        def speaker(uid):
            return SAME if uid == userid else OTHER

        before = [
            (speaker(uid), text)
            for s, ts, uid, text in items
            if s < seq and flagged_ts - ts <= self._AI_CONTEXT_WINDOW_SEC
        ][-self._ai_context_size():]
        after = [(speaker(uid), text) for s, _ts, uid, text in items if s > seq]
        return before, after[: self._AI_CONTEXT_AFTER]

    def handle_badword_text(self, userid: int, ip: str, content: str, entries: List[str]):
        """Count a badword in a message, asking the AI first when every match is ambiguous.

        If the AI cannot be asked or does not answer, the message is not
        counted: better to miss one curse than warn a student for talking
        about their dog.
        """
        ambiguous = self._ambiguous_words()
        # An "@ai" question with a badword is not answered; while the AI checks
        # an ambiguous one the answer waits for its verdict
        ask = self._current_ai_question
        if ask:
            ask["held"] = True
        if self._ai_active() and all(entry in ambiguous for entry in entries):
            context = {
                "kind": "violation",
                "userid": userid,
                "ip": ip,
                "entries": list(entries),
                "question": ask,
            }
            current = self._current_message
            # Questions are checked at once, so the answer is not delayed
            if current and not ask:
                self._schedule_action(
                    self._now() + self._AI_CONTEXT_WAIT_SEC,
                    self._submit_ai_review,
                    content,
                    list(entries),
                    context,
                    current,
                )
            else:
                self._submit_ai_review(content, list(entries), context, current)
            return
        self.handle_badword_violation(userid, ip, "text")

    def _submit_ai_review(self, content: str, entries: List[str], context: dict, current):
        before, after = (
            self._conversation_around(current, context["userid"]) if current else ([], [])
        )
        userid = context["userid"]
        if self._ai.submit(
            content, entries, context, before, after, bool(context.get("question"))
        ):
            logger.info(
                "AI review requested (user=%s, words=%s, context: %s before, %s after)",
                userid,
                entries,
                len(before),
                len(after),
            )
        else:
            logger.warning("AI review queue full; not counted (user=%s, words=%s)", userid, entries)
            self._answer_held_question(context.get("question"))

    def _answer_held_question(self, ask: Optional[dict]):
        """Answer an "@ai" question whose badword was not counted after all."""
        if ask and self._ai_chat_active():
            self._handle_ai_question(
                ask["userid"],
                ask["channel"],
                ask["question"],
                is_pm=bool(ask.get("is_pm", False)),
            )

    # ============ "@ai <question>" in a channel ============
    _AI_CHAT_HISTORY_SEC = 600.0  # earlier questions older than this are forgotten
    _AI_CHAT_REFUSAL = "Maaf, aku tidak bisa menjawab itu."

    def _ai_chat_active(self) -> bool:
        return self._feature_on("aichat") and self._ai_chat.configured

    def _ai_chat_question(self, text: str) -> Optional[str]:
        """The question if ``text`` starts with the @ai prefix, else None.

        The prefix must be followed by a space, ':' or ',' (or nothing), so
        "@aisyah halo" is not a question for the AI.
        """
        prefix = str(getattr(config, "AI_CHAT_PREFIX", "@ai") or "@ai").strip().lower()
        stripped = (text or "").strip()
        if not stripped.lower().startswith(prefix):
            return None
        rest = stripped[len(prefix):]
        if rest and rest[0] not in " :,\t\n":
            return None
        return rest.lstrip(" :,\t\n").strip()

    def _is_ai_chat_blocked_user(self, userid: int) -> bool:
        """Whether this user's account is blocked from using @ai in channels."""
        username = (self._get_username(userid) or "").strip().lower()
        blocked = {
            str(u).strip().lower()
            for u in getattr(config, "AI_CHAT_BLOCKED_USERS", ["tamu", "hadirin"]) or []
            if str(u).strip()
        }
        if username and username in blocked:
            return True
        if not username:
            try:
                user = self.getUser(userid)
                nick = from_tt_char(user.szNickname).strip().lower() if user is not None else ""
                if nick in blocked:
                    return True
            except Exception:
                pass
        return False

    def _handle_ai_question(self, userid: int, channel_id: int, question: str, is_pm: bool = False):
        if self._is_ai_chat_blocked_user(userid):
            msg = getattr(
                config,
                "AI_CHAT_BLOCKED_MESSAGE",
                "Fitur @ai tidak tersedia untuk akun tamu atau hadirin.",
            )
            safe_call(self.send_pm, userid, msg)
            logger.info(
                "AI chat refused: user %s (%s) is blocked from using AI chat",
                userid,
                self._get_username(userid),
            )
            return
        if not question:
            prefix = str(getattr(config, "AI_CHAT_PREFIX", "@ai") or "@ai").strip()
            prompt_msg = f"Tulis pertanyaan setelah {prefix}, contoh: {prefix} apa itu fotosintesis?"
            if is_pm:
                safe_call(self.send_pm, userid, prompt_msg)
            else:
                self._send_channel_text(channel_id, prompt_msg)
            return
        now = self._now()
        cooldown = float(getattr(config, "AI_CHAT_COOLDOWN_SEC", 20) or 0)
        waited = now - self._ai_chat_last.get(userid, 0.0)
        if waited < cooldown:
            # Told privately, so the channel is not filled with notices
            safe_call(
                self.send_pm,
                userid,
                f"Tunggu {int(cooldown - waited) + 1} detik lagi sebelum bertanya ke AI.",
            )
            return
        today = datetime.date.fromtimestamp(now).isoformat()
        if today != self._ai_chat_day:
            self._ai_chat_day, self._ai_chat_count = today, 0
        if self._ai_chat_count >= int(getattr(config, "AI_CHAT_DAILY_LIMIT", 200) or 0):
            safe_call(self.send_pm, userid, "Batas pertanyaan ke AI untuk hari ini sudah habis.")
            return

        hist_key = f"pm_{userid}" if is_pm else channel_id
        history = [
            (q, a)
            for ts, q, a in self._ai_chat_history.get(hist_key, ())
            if now - ts <= self._AI_CHAT_HISTORY_SEC
        ]
        user = safe_call(self.getUser, userid)
        nickname = from_tt_char(user.szNickname).strip() if user is not None else ""
        chan_path = self.getChannelPath(channel_id) or str(channel_id)

        # Snapshot of online users and channels for AI context
        online_users = []
        for u in safe_call(self.getServerUsers, default=[]) or []:
            u_nick = from_tt_char(u.szNickname).strip()
            if u_nick:
                u_chan = self.getChannelPath(u.nChannelID) or "Root"
                online_users.append(f"{u_nick} (di channel {u_chan})")

        channels_list = []
        for ch in safe_call(self.getServerChannels, default=[]) or []:
            c_name = self.getChannelPath(ch.nChannelID) or from_tt_char(ch.szName).strip()
            if c_name:
                channels_list.append(c_name)

        context = {
            "userid": userid,
            "channel": channel_id,
            "channel_name": chan_path,
            "nickname": nickname,
            "question": question,
            "is_admin": self._is_admin(userid),
            "is_pm": is_pm,
            "online_users": online_users[:40],
            "channels": channels_list[:40],
        }
        if not self._ai_chat.ask(question, history, context):
            safe_call(self.send_pm, userid, "AI sedang sibuk, coba lagi sebentar lagi.")
            return
        self._ai_chat_last[userid] = now
        self._ai_chat_count += 1
        logger.info(
            "AI chat question (user=%s, channel=%s, is_pm=%s, %s chars, %s earlier turns)",
            userid,
            channel_id,
            is_pm,
            len(question),
            len(history),
        )

    def _process_ai_chat_results(self):
        """Post finished AI answers in the channel or via PM."""
        for context, answer in self._ai_chat.drain():
            channel_id = context["channel"]
            userid = context["userid"]
            is_pm = bool(context.get("is_pm", False))
            if answer is None:
                text = "Maaf, AI tidak bisa menjawab sekarang. Coba lagi nanti."
            else:
                # Safety net: no clear badwords from the AI (ambiguous ones like
                # "anjing" in an answer about dogs are fine)
                ambiguous = self._ambiguous_words()
                if any(e not in ambiguous for e in self._badwords.matching_entries(answer)):
                    logger.warning("AI chat answer contained a badword; replaced (channel=%s, is_pm=%s)", channel_id, is_pm)
                    answer = self._AI_CHAT_REFUSAL
                size = max(0, int(getattr(config, "AI_CHAT_HISTORY", 3) or 0))
                hist_key = f"pm_{userid}" if is_pm else channel_id
                history = self._ai_chat_history.setdefault(hist_key, deque(maxlen=max(1, size)))
                if size:
                    history.append((self._now(), context["question"], answer))
                who = f" untuk {context['nickname']}" if (context.get("nickname") and not is_pm) else ""
                disclaimer = str(getattr(config, "AI_CHAT_DISCLAIMER", "") or "").strip()
                text = f"[AI]{who}: {answer}"
                if disclaimer:
                    text += f"\n{disclaimer}"
            if is_pm:
                safe_call(self.send_pm, userid, text)
            else:
                self._send_channel_text(channel_id, text)

    def _send_channel_text(self, channel_id: int, text: str):
        """Post a channel message; as an admin the bot need not be in the channel."""
        try:
            my_id = self.getMyUserID() or 0
            for part in buildTextMessage(
                text, TextMsgType.MSGTYPE_CHANNEL, nChannelID=channel_id, nFromUserID=my_id
            ):
                cmdid = self.doTextMessage(part)
                self._track_pending_cmd(
                    cmdid, 0, f"message to channel {channel_id}", notify=False
                )
        except Exception:
            logger.exception("Failed to send a message to channel %s", channel_id)

    def _find_users_by_nickname_fuzzy(self, name: str, context: Any = None) -> List[User]:
        clean_name = str(name or "").strip().lower()
        # If user refers to self: "aku", "saya", "me", etc.
        if clean_name in ("aku", "saya", "gue", "gw", "me", "diriku", "aku sendiri", "saya sendiri"):
            if isinstance(context, dict) and context.get("userid"):
                try:
                    u = self.getUser(int(context["userid"]))
                    if u is not None:
                        return [u]
                except Exception:
                    pass
        if not clean_name:
            return []

        users = self.getServerUsers()
        # 1. Exact match on nickname or username
        exact = [
            u for u in users
            if from_tt_char(u.szNickname).strip().lower() == clean_name
            or from_tt_char(u.szUsername).strip().lower() == clean_name
        ]
        if exact:
            return exact

        # 2. Match without honorifics/prefixes like "kak ", "bang ", "mas ", "pak ", "bu ", "@"
        prefix_pattern = r"^@\s*|^(kak|bang|mas|pak|bu|om|tante|bro|sis|ustadz)\s+"
        without_prefix = re.sub(prefix_pattern, "", clean_name).strip()
        stem = without_prefix or clean_name
        if stem:
            stem_match = [
                u for u in users
                if re.sub(prefix_pattern, "", from_tt_char(u.szNickname).strip().lower()).strip() == stem
                or from_tt_char(u.szUsername).strip().lower() == stem
            ]
            if stem_match:
                return stem_match

        # 3. Token match (e.g. "fian" matches "Kak Fian" or "Fian Pratama")
        if stem:
            token_match = []
            for u in users:
                nick = from_tt_char(u.szNickname).strip().lower()
                tokens = set(re.findall(r"\w+", nick))
                if stem in tokens or clean_name in tokens:
                    token_match.append(u)
            if token_match:
                return token_match

        # 4. Substring match
        partial = [
            u for u in users
            if (stem and stem in from_tt_char(u.szNickname).strip().lower())
            or (stem and from_tt_char(u.szNickname).strip().lower() in stem)
            or (stem and stem in from_tt_char(u.szUsername).strip().lower())
        ]
        if partial:
            return partial
        return []

    def _find_multiple_users(self, raw_input: str, context: Any = None) -> List[User]:
        clean = str(raw_input or "").strip()
        if not clean:
            return []

        myid = self.getMyUserID() or 0
        if clean.lower() in ("all", "semua", "semua user", "semua orang"):
            cid = context.get("channel") if isinstance(context, dict) else None
            if cid:
                return [u for u in self.getServerUsers() if u.nChannelID == int(cid) and u.nUserID != myid]
            return [u for u in self.getServerUsers() if u.nUserID != myid]

        parts = re.split(r",|\s+dan\s+|\s+and\s+", clean, flags=re.IGNORECASE)
        found: List[User] = []
        seen_ids = set()
        for p in parts:
            p_str = p.strip()
            if not p_str:
                continue
            matched = self._find_users_by_nickname_fuzzy(p_str, context)
            for u in matched:
                if u.nUserID not in seen_ids:
                    seen_ids.add(u.nUserID)
                    found.append(u)
        return found

    def _find_channel_id(self, target: str, context: Any = None) -> Optional[int]:
        target_str = str(target or "").strip()
        target_lower = target_str.lower().strip("/")

        # If user says "sini", "channel ini", "room ini", "di sini", or empty
        if not target_str or target_lower in ("sini", "di sini", "channel ini", "room ini", "current", "saat ini", "ini"):
            if isinstance(context, dict) and context.get("channel"):
                return int(context["channel"])

        # Try direct path or normalized path
        for p in (target_str, self._normalize_channel_target_to_path(target_str)):
            try:
                cid = self.getChannelIDFromPath(p)
                if cid and cid > 0:
                    return cid
            except Exception:
                pass

        chans = self.getServerChannels()

        # 1. Exact match with original query (e.g. "Ruang Ekskul" or "Lobi")
        for ch in chans:
            name = from_tt_char(ch.szName).lower().strip()
            path = (self.getChannelPath(ch.nChannelID) or "").lower().strip("/")
            if target_lower in (name, path) or path.endswith(f"/{target_lower}"):
                return ch.nChannelID

        # Clean target: strip leading prepositions and channel terms
        # e.g. "ke channel ekskul" -> "ekskul", "ke room musik" -> "musik"
        clean_chan = re.sub(r"^(ke|di|pada|menuju)?\s*(channel|room|ruang|saluran)?\s*", "", target_lower).strip()
        clean_chan = clean_chan.strip("/")

        # 2. Exact match with clean name or path
        if clean_chan and clean_chan != target_lower:
            for ch in chans:
                name = from_tt_char(ch.szName).lower().strip()
                path = (self.getChannelPath(ch.nChannelID) or "").lower().strip("/")
                if clean_chan in (name, path) or path.endswith(f"/{clean_chan}"):
                    return ch.nChannelID

        # 3. Substring match (e.g. "ekskul" matches "Ruang Ekskul" or "Ekskul Musik")
        search_term = clean_chan or target_lower
        if search_term:
            for ch in chans:
                name = from_tt_char(ch.szName).lower().strip()
                path = (self.getChannelPath(ch.nChannelID) or "").lower().strip("/")
                if search_term in name or search_term in path:
                    return ch.nChannelID

        # 4. Token match
        if search_term:
            tokens_query = set(re.findall(r"\w+", search_term))
            if tokens_query:
                for ch in chans:
                    name = from_tt_char(ch.szName).lower().strip()
                    tokens_name = set(re.findall(r"\w+", name))
                    if tokens_query.issubset(tokens_name):
                        return ch.nChannelID

        return None

    def _execute_ai_tool(self, tool_name: str, tool_args: dict, context: Any) -> Dict[str, Any]:
        requester_id = context.get("userid", 0) if isinstance(context, dict) else 0
        is_admin = self._is_admin(requester_id)

        if tool_name == "kick_user":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh kick orang.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            reason = str(tool_args.get("reason") or "").strip()
            targets = self._find_multiple_users(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            myid = self.getMyUserID() or 0
            kicked = []
            for u in targets:
                if u.nUserID == myid:
                    continue
                nick = from_tt_char(u.szNickname)
                if reason:
                    safe_call(self.send_pm, u.nUserID, reason)
                kicked.append(nick)
                self._schedule_action(
                    self._now() + 0.1,
                    self._execute_manual_kick,
                    u.nUserID,
                    requester_id,
                    nick,
                    reason,
                    True,
                )
            if not kicked:
                return {"status": "error", "message": "Tidak bisa menendang bot sendiri."}
            return {
                "status": "success",
                "message": f"User {', '.join(kicked)} berhasil ditendang dari server.",
            }

        elif tool_name == "ban_user":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh ban orang.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            reason = str(tool_args.get("reason") or "").strip()
            targets = self._find_multiple_users(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            mode = str(getattr(config, "BAN_TARGET", "USERNAME")).upper()
            ban_type = BanType.BANTYPE_USERNAME if mode == "USERNAME" else BanType.BANTYPE_IPADDR
            myid = self.getMyUserID() or 0
            banned = []
            for u in targets:
                if u.nUserID == myid:
                    continue
                who = from_tt_char(u.szUsername) if mode == "USERNAME" else from_tt_char(u.szIPAddress)
                nick = from_tt_char(u.szNickname)
                if reason:
                    safe_call(self.send_pm, u.nUserID, reason)
                banned.append(nick)
                self._schedule_action(
                    self._now() + 0.1,
                    self._execute_manual_ban,
                    u.nUserID,
                    requester_id,
                    int(ban_type),
                    who,
                    nick,
                    reason,
                    True,
                )
            if not banned:
                return {"status": "error", "message": "Tidak bisa memblokir bot sendiri."}
            return {
                "status": "success",
                "message": f"User {', '.join(banned)} berhasil diblokir dari server.",
            }

        elif tool_name == "move_user":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh mindahin orang.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            channel_target = str(tool_args.get("channel") or "").strip()
            targets = self._find_multiple_users(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            moved = []
            for u in targets:
                self.doMoveUser(u.nUserID, cid)
                moved.append(from_tt_char(u.szNickname))
            chan_name = self.getChannelPath(cid) or str(cid)
            return {
                "status": "success",
                "message": f"User {', '.join(moved)} berhasil dipindahkan ke channel {chan_name}.",
            }

        elif tool_name == "move_channel_users":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh mindahin orang.",
                }
            from_channel = str(tool_args.get("from_channel") or "").strip()
            to_channel = str(tool_args.get("to_channel") or "").strip()
            from_cid = self._find_channel_id(from_channel, context)
            if not from_cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel asal '{from_channel}' tidak ditemukan di server.",
                }
            to_cid = self._find_channel_id(to_channel, context)
            if not to_cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel tujuan '{to_channel}' tidak ditemukan di server.",
                }
            if from_cid == to_cid:
                return {
                    "status": "error",
                    "message": "Channel asal dan channel tujuan tidak boleh sama.",
                }
            myid = self.getMyUserID() or 0
            targets = [u for u in self.getServerUsers() if u.nChannelID == from_cid and u.nUserID != myid]
            from_path = self.getChannelPath(from_cid) or from_channel
            to_path = self.getChannelPath(to_cid) or to_channel
            if not targets:
                return {
                    "status": "success",
                    "message": f"Tidak ada pengguna di channel {from_path} untuk dipindahkan ke {to_path}.",
                }
            moved = []
            for u in targets:
                self.doMoveUser(u.nUserID, to_cid)
                moved.append(from_tt_char(u.szNickname))
            return {
                "status": "success",
                "message": f"Berhasil memindahkan {len(moved)} user ({', '.join(moved)}) dari channel {from_path} ke {to_path}.",
            }

        elif tool_name == "list_online_users":
            users = self.getServerUsers()
            online = []
            for u in users:
                nick = from_tt_char(u.szNickname)
                if nick:
                    chan_path = self.getChannelPath(u.nChannelID) or "Root"
                    online.append(f"{nick} (di channel {chan_path})")
            return {
                "status": "success",
                "users": online,
                "total": len(online),
                "message": f"Ada {len(online)} pengguna yang sedang online: {', '.join(online)}." if online else "Tidak ada pengguna lain yang online.",
            }

        elif tool_name == "list_channels":
            chans = self.getServerChannels()
            skip_ids = set() if is_admin else self._hidden_channel_ids(chans)
            names = []
            for ch in chans:
                if ch.nChannelID not in skip_ids:
                    path = self.getChannelPath(ch.nChannelID) or from_tt_char(ch.szName)
                    if path:
                        names.append(path)
            return {
                "status": "success",
                "channels": names[:25],
                "total": len(names),
                "message": f"Ada {len(names)} channel di server: {', '.join(names[:20])}.",
            }

        elif tool_name == "change_bot_status":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh ganti status bot.",
                }
            status = str(tool_args.get("status") or "").strip()
            if not status:
                return {"status": "error", "message": "Pesan status tidak boleh kosong."}
            self.doChangeStatus(0, status)
            return {
                "status": "success",
                "message": f"Status bot berhasil diubah menjadi: '{status}'.",
            }

        elif tool_name == "temp_ban_user":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh ban sementara.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            try:
                minutes = max(1, int(tool_args.get("minutes") or 5))
            except Exception:
                minutes = 5
            reason = str(tool_args.get("reason") or "").strip()
            targets = self._find_users_by_nickname_fuzzy(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            myid = self.getMyUserID() or 0
            banned = []
            delay = 1.0 if reason else 0.05
            for u in targets:
                if u.nUserID == myid:
                    continue
                nick = from_tt_char(u.szNickname)
                if reason:
                    safe_call(self.send_pm, u.nUserID, f"Kamu di-ban sementara {minutes} menit: {reason}")
                banned.append(nick)
                self._schedule_action(
                    self._now() + delay,
                    self._execute_manual_temp_ban,
                    u.nUserID,
                    from_tt_char(u.szIPAddress),
                    from_tt_char(u.szUsername),
                    nick,
                    int(minutes),
                    reason,
                    requester_id,
                )
            if not banned:
                return {"status": "error", "message": "Tidak bisa memblokir bot sendiri."}
            return {
                "status": "success",
                "message": f"User {', '.join(banned)} berhasil di-ban sementara selama {minutes} menit.",
            }

        elif tool_name == "unban_user":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh unban orang.",
                }
            target = str(tool_args.get("target") or "").strip()
            if not target:
                return {"status": "error", "message": "Target username atau IP tidak boleh kosong."}
            by_ip = self._ban_target_is_ip()
            descs = []
            if by_ip:
                cmdid = self.doUnBanUser(target, 0)
                if cmdid > 0:
                    self._track_pending_cmd(cmdid, requester_id, f"unban IP '{target}'")
                    descs.append(target)
            else:
                bu = BannedUser()
                assign_tt_char_array((bu, "szUsername"), target)
                bu.uBanTypes = BanType.BANTYPE_USERNAME
                assign_tt_char_array((bu, "szIPAddress"), "")
                assign_tt_char_array((bu, "szChannelPath"), "")
                assign_tt_char_array((bu, "szNickname"), "")
                assign_tt_char_array((bu, "szOwner"), "")
                cmdid = self.doUnbanUserEx(bu)
                if cmdid > 0:
                    self._track_pending_cmd(cmdid, requester_id, f"unban username '{target}'")
                    descs.append(target)
            self._forget_temp_ban(target)
            if descs:
                return {
                    "status": "success",
                    "message": f"Unban untuk '{target}' berhasil diproses ke server.",
                }
            return {
                "status": "error",
                "message": f"Gagal memproses unban untuk '{target}'.",
            }

        elif tool_name == "toggle_moderation_feature":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh ngatur fitur moderasi.",
                }
            feat_raw = str(tool_args.get("feature") or "").strip().lower()

            # Determine enabled status robustly: parse strings ("false", "off"), numbers, and user question context
            q_text = str(context.get("question") or "") if isinstance(context, dict) else ""
            q_lower = q_text.lower()
            wants_off = any(w in q_lower for w in ("matiin", "matikan", "nonaktifkan", "turn off", "disable", "non aktif", "non-aktif", "matikanlah", " off"))
            wants_on = any(w in q_lower for w in ("hidupin", "hidupkan", "aktifkan", "nyalain", "nyalakan", "turn on", "enable", " on ", "hidupkanlah"))

            raw_val = tool_args.get("enabled")
            if raw_val is None:
                for k in ("action", "state", "status", "mode", "toggle"):
                    if k in tool_args:
                        raw_val = tool_args[k]
                        break

            if wants_off and not wants_on:
                enabled = False
            elif wants_on and not wants_off:
                enabled = True
            elif raw_val is not None:
                if isinstance(raw_val, bool):
                    enabled = raw_val
                elif isinstance(raw_val, (int, float)):
                    enabled = (raw_val != 0)
                elif isinstance(raw_val, str):
                    v = raw_val.strip().lower()
                    if v in ("false", "0", "off", "mati", "matikan", "matiin", "nonaktif", "nonaktifkan", "disable", "disabled", "no", "tidak"):
                        enabled = False
                    elif v in ("true", "1", "on", "hidup", "hidupkan", "hidupin", "aktif", "aktifkan", "nyala", "nyalakan", "enable", "enabled", "yes", "ya"):
                        enabled = True
                    else:
                        enabled = True
                else:
                    enabled = bool(raw_val)
            else:
                enabled = True

            action_str = "diaktifkan" if enabled else "dinonaktifkan"
            if feat_raw in ("all", "moderation", "moderasi", "semua", "semua moderasi", "semua fitur"):
                target_features = ["login", "join", "spam", "badwords", "profile", "pm"]
                for f in target_features:
                    self._set_feature(f, enabled)
                return {
                    "status": "success",
                    "message": f"Semua fitur moderasi (login, join, spam, badwords, profile, pm) berhasil {action_str}.",
                }

            mapping = {
                "word_filter": "badwords",
                "word filter": "badwords",
                "filter kata": "badwords",
                "filter_kata": "badwords",
                "filter": "badwords",
                "badword": "badwords",
                "badwords": "badwords",
                "kata kasar": "badwords",
                "antispam": "spam",
                "anti spam": "spam",
                "anti_spam": "spam",
                "spam": "spam",
                "spam protection": "spam",
                "proteksi spam": "spam",
                "perlindungan spam": "spam",
                "login": "login",
                "join": "join",
                "profile": "profile",
                "profil": "profile",
                "pm": "pm",
                "private message": "pm",
                "pesan pribadi": "pm",
                "ai": "ai",
                "ai review": "ai",
                "review ai": "ai",
                "aichat": "aichat",
                "ai_chat": "aichat",
                "chat_ai": "aichat",
                "chat ai": "aichat",
            }
            target_feat = mapping.get(feat_raw, feat_raw)
            known_features = {name for name, _desc, _key in self._FEATURES}
            if target_feat not in known_features:
                return {
                    "status": "error",
                    "error": "FEATURE_NOT_FOUND",
                    "message": f"Fitur '{feat_raw}' tidak dikenal. Pilihan fitur: word filter (badwords), spam, login, join, profile, pm, ai, aichat, atau all.",
                }

            if target_feat in self._AI_FEATURES and enabled and not self._ai.configured:
                return {
                    "status": "error",
                    "error": "AI_NOT_CONFIGURED",
                    "message": "Fitur AI belum dikonfigurasi di config.json.",
                }

            notes = self._set_feature(target_feat, enabled)
            desc = dict((n, d) for n, d, _ in self._FEATURES).get(target_feat, target_feat)
            msg = f"Fitur {desc} ({target_feat}) berhasil {action_str}."
            if notes:
                msg += " " + " ".join(notes)
            return {
                "status": "success",
                "message": msg,
            }

        elif tool_name == "get_moderation_status":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh lihat status moderasi.",
                }
            lines = []
            for name, desc, _ in self._FEATURES:
                state = "ON" if self._feature_on(name) else "OFF"
                lines.append(f"{name}: {state}")
            return {
                "status": "success",
                "message": f"Status moderasi server: {', '.join(lines)}.",
            }

        elif tool_name == "forgive_user":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh memaafkan user.",
                }
            target = str(tool_args.get("target") or "").strip().lower()
            if not target:
                return {"status": "error", "message": "Target user atau IP harus diisi."}
            now = self._now()
            if target in ("all", "semua"):
                cleared_warnings = 0
                for kind, key, _stage, _left in self._abuse.active_stages():
                    self._abuse.reset(kind, key)
                    self._forgiven[key] = now
                    cleared_warnings += 1
                cleared_bans = len(self._temp_bans)
                for entry in list(self._temp_bans.values()):
                    if entry.get("key"):
                        self._forgiven[entry["key"]] = now
                    self._lift_temp_ban(entry["mode"], entry["label"], entry.get("key", ""), entry.get("kind", ""), True)
                return {
                    "status": "success",
                    "message": f"Semua sanksi berhasil dimaafkan ({cleared_warnings} peringatan direset, {cleared_bans} temp ban dicabut).",
                }

            items = self._find_abuse_items(target)
            if not items:
                users = self._find_users_by_nickname_fuzzy(target, context)
                for u in users:
                    items.extend(self._find_abuse_items(from_tt_char(u.szNickname)))
                    items.extend(self._find_abuse_items(from_tt_char(u.szUsername)))
                    items.extend(self._find_abuse_items(from_tt_char(u.szIPAddress)))
            items = list(dict.fromkeys(items))
            if not items:
                return {
                    "status": "error",
                    "message": f"Tidak ada peringatan atau temp ban aktif untuk '{target}'.",
                }
            done = []
            for item in items:
                if item[0] == "warning":
                    _, kind, key = item
                    self._abuse.reset(kind, key)
                    self._forgiven[key] = now
                    done.append(f"{self._abuse_subject_label(kind, key)} (peringatan {kind} dihapus)")
                else:
                    _, mode, label = item
                    entry = self._temp_bans.get(f"{mode}:{label}")
                    if entry:
                        if entry.get("key"):
                            self._forgiven[entry["key"]] = now
                        self._lift_temp_ban(mode, label, entry.get("key", ""), entry.get("kind", ""), True)
                        done.append(f"{entry.get('who') or label} (temp ban dicabut)")
            return {
                "status": "success",
                "message": f"Berhasil dimaafkan: {'; '.join(done)}.",
            }

        elif tool_name == "add_badword":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh nambah kata kasar.",
                }
            raw_words = str(tool_args.get("words") or "").strip()
            words = self._parse_badword_csv(raw_words)
            if not words:
                return {"status": "error", "message": "Kata yang ingin ditambahkan tidak boleh kosong."}
            errors = [err for err in map(self._badwords.pattern_error, words) if err]
            added = self._badwords.add_words(words)
            valid = {w for w in words if not self._badwords.pattern_error(w)}
            already = sorted(valid - set(added))
            res_parts = []
            if added:
                res_parts.append(f"Kata berhasil ditambahkan: {', '.join(added)}")
            if already:
                res_parts.append(f"Sudah ada di daftar: {', '.join(already)}")
            if errors:
                res_parts.append(f"Gagal ditambahkan: {', '.join(errors)}")
            return {
                "status": "success" if added else "error",
                "message": ". ".join(res_parts) or "Tidak ada kata yang ditambahkan.",
            }

        elif tool_name == "delete_badword":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh hapus kata kasar.",
                }
            raw_words = str(tool_args.get("words") or "").strip()
            words = self._parse_badword_csv(raw_words)
            if not words:
                return {"status": "error", "message": "Kata yang ingin dihapus tidak boleh kosong."}
            removed = self._badwords.remove_words(words) if words else []
            missing = sorted(set(words) - set(removed))
            res_parts = []
            if removed:
                res_parts.append(f"Kata berhasil dihapus dari word filter: {', '.join(removed)}")
            if missing:
                res_parts.append(f"Tidak ditemukan di daftar: {', '.join(missing)}")
            return {
                "status": "success" if removed else "error",
                "message": ". ".join(res_parts) or "Tidak ada kata yang dihapus.",
            }

        elif tool_name == "list_badwords":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh lihat daftar kata terlarang.",
                }
            query = str(tool_args.get("query") or "").strip().lower()
            all_words = self._badwords.list_words()
            words = [w for w in all_words if query in w] if query else all_words
            if not words:
                return {
                    "status": "success",
                    "total": 0,
                    "message": f"Tidak ada kata terlarang yang cocok dengan '{query}'." if query else "Daftar kata terlarang kosong.",
                }
            sample = words[:15]
            more = f" (dan {len(words) - 15} lainnya)" if len(words) > 15 else ""
            return {
                "status": "success",
                "total": len(words),
                "message": f"Ditemukan {len(words)} kata terlarang: {', '.join(sample)}{more}.",
            }

        elif tool_name == "list_bans":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh lihat daftar ban.",
                }
            now = self._now()
            bans = sorted(self._temp_bans.values(), key=lambda e: e.get("until", 0))
            if not bans:
                return {
                    "status": "success",
                    "message": "Tidak ada pengguna yang sedang terkena ban sementara saat ini.",
                }
            items = []
            for entry in bans:
                who = entry.get("who") or entry.get("label")
                left = self._fmt_duration(entry.get("until", 0) - now)
                reason = entry.get("reason") or "tidak ada alasan"
                items.append(f"{who} (sisa {left}, alasan: {reason})")
            return {
                "status": "success",
                "message": f"Daftar ban sementara: {', '.join(items)}.",
            }

        elif tool_name == "find_user":
            nickname = str(tool_args.get("nickname") or "").strip()
            if not nickname:
                return {"status": "error", "message": "Nama pengguna harus diisi."}
            targets = self._find_users_by_nickname_fuzzy(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan atau sedang tidak online di server.",
                }
            infos = []
            for u in targets:
                nick = from_tt_char(u.szNickname)
                cid = u.nChannelID
                cpath = self.getChannelPath(cid) or "Root"
                status_msg = from_tt_char(u.szStatusMsg).strip()
                status_str = f", status: '{status_msg}'" if status_msg else ""
                infos.append(f"{nick} ada di channel {cpath}{status_str}")
            return {
                "status": "success",
                "message": f"Ditemukan: {'; '.join(infos)}.",
            }

        elif tool_name == "check_channel_owner":
            channel_target = str(tool_args.get("channel") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan.",
                }
            path = self.getChannelPath(cid) or str(cid)
            owner = cache_store.get_owner(path)
            if not owner:
                return {
                    "status": "success",
                    "message": f"Channel {path} tidak memiliki pemilik yang tercatat.",
                }
            return {
                "status": "success",
                "message": f"Pemilik channel {path} adalah {owner}.",
            }

        elif tool_name == "change_bot_nickname":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh ganti nama bot.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            if not nickname:
                return {"status": "error", "message": "Nama baru bot tidak boleh kosong."}
            self.doChangeNickname(nickname)
            return {
                "status": "success",
                "message": f"Nama bot berhasil diubah menjadi: '{nickname}'.",
            }

        elif tool_name == "join_channel":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh mindahin bot ke channel lain.",
                }
            channel_target = str(tool_args.get("channel") or "").strip()
            password = str(tool_args.get("password") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            self.doJoinChannelByID(cid, password)
            cpath = self.getChannelPath(cid) or channel_target
            return {
                "status": "success",
                "message": f"Bot berhasil bergabung ke channel {cpath}.",
            }

        elif tool_name == "leave_channel":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh nyuruh bot keluar channel.",
                }
            root_id = self.getRootChannelID() if hasattr(self, "getRootChannelID") else 1
            self.doJoinChannelByID(root_id or 1, "")
            return {
                "status": "success",
                "message": "Bot sudah kembali ke channel utama (root/lobi).",
            }

        elif tool_name == "set_channel_operator":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh ngatur status operator channel.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            channel_target = str(tool_args.get("channel") or "").strip()
            raw_op = tool_args.get("is_operator")
            q_text = str(context.get("question") or "").lower() if isinstance(context, dict) else ""
            if any(w in q_text for w in ("cabut", "hapus", "revoke", "bukan operator", "jangan jadi operator")):
                is_op = False
            elif any(w in q_text for w in ("jadiin", "jadikan", "beri", "grant", "tambah")):
                is_op = True
            elif raw_op is not None:
                if isinstance(raw_op, str) and raw_op.strip().lower() in ("false", "0", "off", "cabut", "revoke"):
                    is_op = False
                else:
                    is_op = bool(raw_op)
            else:
                is_op = True
            if not nickname:
                nickname = "aku"
            targets = self._find_users_by_nickname_fuzzy(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            u = targets[0]
            cid = self._find_channel_id(channel_target, context) if channel_target else None
            if not cid and isinstance(context, dict) and context.get("channel"):
                cid = int(context["channel"])
            if not cid:
                cid = u.nChannelID
            self.doChannelOpEx(u.nUserID, cid, "", is_op)
            nick = from_tt_char(u.szNickname)
            cpath = self.getChannelPath(cid) or f"channel {cid}"
            status_str = "dijadikan operator" if is_op else "dicabut status operatornya"
            return {
                "status": "success",
                "message": f"User {nick} berhasil {status_str} di {cpath}.",
            }

        elif tool_name == "get_channel_info":
            channel_target = str(tool_args.get("channel") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            cpath = self.getChannelPath(cid) or channel_target
            try:
                ch = self.getChannel(cid)
                topic = from_tt_char(ch.szTopic).strip() if ch and hasattr(ch, "szTopic") else ""
                max_u = ch.nMaxUsers if ch and hasattr(ch, "nMaxUsers") else 0
                has_pass = bool(ch.uChannelType & 1) if ch and hasattr(ch, "uChannelType") else False
            except Exception:
                topic, max_u, has_pass = "", 0, False
            info_parts = [f"Channel: {cpath}"]
            if topic:
                info_parts.append(f"Topik: {topic}")
            if max_u > 0:
                info_parts.append(f"Kapasitas: {max_u} pengguna")
            info_parts.append("Dilindungi password" if has_pass else "Tanpa password")
            return {
                "status": "success",
                "message": f"Info {', '.join(info_parts)}.",
            }

        elif tool_name == "get_user_info":
            nickname = str(tool_args.get("nickname") or "").strip()
            if not nickname:
                nickname = "aku"
            targets = self._find_users_by_nickname_fuzzy(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            u = targets[0]
            nick = from_tt_char(u.szNickname)
            uname = from_tt_char(u.szUsername)
            ip = from_tt_char(u.szIPAddress)
            cpath = self.getChannelPath(u.nChannelID) or "Root"
            status_msg = from_tt_char(u.szStatusMsg).strip()
            u_is_admin = bool(u.uUserType & UserType.USERTYPE_ADMIN) if hasattr(u, "uUserType") else False
            role = "Admin" if u_is_admin else "User biasa"
            details = [f"Nama: {nick}", f"Role: {role}", f"Di channel: {cpath}"]
            if status_msg:
                details.append(f"Status: '{status_msg}'")
            if is_admin:
                details.append(f"Username: '{uname}'")
                details.append(f"IP: {ip}")
            return {
                "status": "success",
                "message": f"Data pengguna: {', '.join(details)}.",
            }

        elif tool_name in ("send_channel_message", "channel_message"):
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh nyuruh bot kirim pesan channel.",
                }
            msg = str(tool_args.get("message") or "").strip()
            if not msg:
                return {"status": "error", "message": "Pesan channel tidak boleh kosong."}
            channel_target = str(tool_args.get("channel") or "").strip()
            cid = self._find_channel_id(channel_target, context) if channel_target else None
            if not cid and isinstance(context, dict) and context.get("channel"):
                cid = int(context["channel"])
            if not cid:
                cid = self.getRootChannelID() or 1
            self._send_channel_text(cid, msg)
            cpath = self.getChannelPath(cid) or f"channel {cid}"
            return {
                "status": "success",
                "message": f"Pesan berhasil dikirim ke {cpath}: '{msg}'.",
            }

        elif tool_name == "broadcast_message":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh kirim broadcast.",
                }
            msg = str(tool_args.get("message") or "").strip()
            if not msg:
                return {"status": "error", "message": "Pesan broadcast tidak boleh kosong."}

            # Safety redirect: if user actually requested a channel message or specified a channel,
            # send to channel instead of broadcasting to the entire server
            q_text = str(context.get("question") or "").lower() if isinstance(context, dict) else ""
            channel_in_args = str(tool_args.get("channel") or "").strip()
            is_channel_msg_intent = (
                bool(channel_in_args)
                or "channel message" in q_text
                or "pesan channel" in q_text
                or "pesan ke channel" in q_text
                or "chat ke channel" in q_text
                or "chat channel" in q_text
                or ("ke channel" in q_text and not any(b in q_text for b in ("broadcast", "siaran", "seluruh server", "semua channel")))
            )
            if is_channel_msg_intent:
                cid = self._find_channel_id(channel_in_args, context) if channel_in_args else None
                if not cid and isinstance(context, dict) and context.get("channel"):
                    cid = int(context["channel"])
                if not cid:
                    cid = self.getRootChannelID() or 1
                self._send_channel_text(cid, msg)
                cpath = self.getChannelPath(cid) or f"channel {cid}"
                return {
                    "status": "success",
                    "message": f"Pesan berhasil dikirim ke {cpath}: '{msg}'.",
                }

            my_id = self.getMyUserID() or 0
            parts = buildTextMessage(content=msg, nMsgType=TextMsgType.MSGTYPE_BROADCAST, nFromUserID=my_id)
            for part in parts:
                self.doTextMessage(part)
            return {
                "status": "success",
                "message": f"Pesan siaran berhasil dikirim ke seluruh server: '{msg}'.",
            }

        elif tool_name in ("send_private_message", "pm_user"):
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh nyuruh bot kirim pesan pribadi.",
                }
            nickname = str(tool_args.get("nickname") or "").strip()
            msg = str(tool_args.get("message") or "").strip()
            if not msg:
                return {"status": "error", "message": "Pesan pribadi tidak boleh kosong."}
            targets = self._find_multiple_users(nickname, context)
            if not targets:
                return {
                    "status": "error",
                    "error": "USER_NOT_FOUND",
                    "message": f"User '{nickname}' tidak ditemukan di server.",
                }
            sent_to = []
            for u in targets:
                safe_call(self.send_pm, u.nUserID, msg)
                sent_to.append(from_tt_char(u.szNickname))
            return {
                "status": "success",
                "message": f"Pesan pribadi berhasil dikirim ke {', '.join(sent_to)}: '{msg}'.",
            }

        elif tool_name == "create_channel":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh buat channel baru.",
                }
            name = str(tool_args.get("name") or "").strip()
            if not name:
                return {"status": "error", "message": "Nama channel tidak boleh kosong."}
            parent_channel = str(tool_args.get("parent_channel") or "").strip()
            topic = str(tool_args.get("topic") or "").strip()
            password = str(tool_args.get("password") or "").strip()
            try:
                max_users = int(tool_args.get("max_users") or 0)
            except Exception:
                max_users = 0

            parent_id = 0
            if parent_channel:
                parent_id = self._find_channel_id(parent_channel, context) or 0
            if parent_id <= 0:
                parent_id = self.getRootChannelID() if hasattr(self, "getRootChannelID") else 1
                if not parent_id or parent_id <= 0:
                    parent_id = 1

            chan = Channel()
            chan.nParentID = parent_id
            assign_tt_char_array((chan, "szName"), name)
            assign_tt_char_array((chan, "szTopic"), topic)
            assign_tt_char_array((chan, "szPassword"), password)
            chan.bPassword = bool(password)
            assign_tt_char_array((chan, "szOpPassword"), "")

            try:
                channel_cfg = channel_wizard._channel_defaults()
                audio_cfg = channel_wizard._audio_defaults()
            except Exception:
                channel_cfg = {}
                audio_cfg = {}

            chan.nMaxUsers = max_users if max_users > 0 else int(channel_cfg.get("max_users", 50))
            chan.nDiskQuota = int(channel_cfg.get("disk_quota_mb", 100)) * 1024 * 1024
            chan.nUserData = 0
            chan.uChannelType = ChannelType.CHANNEL_PERMANENT

            ac = AudioCodec()
            ac.nCodec = Codec.OPUS_CODEC
            oc = OpusCodec()
            oc.nApplication = OPUS_APPLICATION_VOIP
            oc.nSampleRate = int(audio_cfg.get("sample_rate", 48000))
            oc.nChannels = 1
            oc.nBitRate = int(audio_cfg.get("bitrate_kbps", 64)) * 1000
            oc.bVBR = True
            oc.bDTX = False
            oc.bFEC = False
            oc.bVBRConstraint = False
            oc.nTxIntervalMSec = 20
            oc.nFrameSizeMSec = 20
            oc.nComplexity = 10
            ac.u.opus = oc
            chan.audiocodec = ac
            chan.audiocfg.bEnableAGC = False
            chan.audiocfg.nGainLevel = 0

            cmdid = self.doMakeChannel(chan)
            if cmdid <= 0:
                return {
                    "status": "error",
                    "message": f"Gagal mengirim permintaan pembuatan channel '{name}' ke server.",
                }
            parent_path = self.getChannelPath(parent_id) or f"channel {parent_id}"
            return {
                "status": "success",
                "message": f"Channel '{name}' berhasil dibuat di {parent_path}.",
            }

        elif tool_name == "delete_channel":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh hapus channel.",
                }
            channel_target = str(tool_args.get("channel") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            root_id = self.getRootChannelID() if hasattr(self, "getRootChannelID") else 1
            if cid == root_id or cid == 1:
                return {
                    "status": "error",
                    "message": "Channel root tidak boleh dihapus.",
                }
            cpath = self.getChannelPath(cid) or channel_target
            cmdid = self.doRemoveChannel(cid)
            if cmdid <= 0:
                return {
                    "status": "error",
                    "message": f"Gagal mengirim perintah hapus channel '{cpath}' ke server.",
                }
            return {
                "status": "success",
                "message": f"Channel {cpath} berhasil dihapus dari server.",
            }

        elif tool_name == "list_channel_users":
            channel_target = str(tool_args.get("channel") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            cpath = self.getChannelPath(cid) or channel_target
            try:
                chan_users = self.getChannelUsers(cid) or []
            except Exception:
                chan_users = []
            if not chan_users:
                chan_users = [u for u in self.getServerUsers() if u.nChannelID == cid]
            nicks = []
            for u in chan_users:
                nick = from_tt_char(u.szNickname)
                if nick:
                    nicks.append(nick)
            if not nicks:
                return {
                    "status": "success",
                    "users": [],
                    "total": 0,
                    "message": f"Tidak ada pengguna di channel {cpath} saat ini.",
                }
            return {
                "status": "success",
                "users": nicks,
                "total": len(nicks),
                "message": f"Ada {len(nicks)} pengguna di channel {cpath}: {', '.join(nicks)}.",
            }

        elif tool_name == "list_channel_files":
            channel_target = str(tool_args.get("channel") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            cpath = self.getChannelPath(cid) or channel_target
            try:
                files = self.getChannelFiles(cid) or []
            except Exception:
                files = []
            file_names = []
            for f in files:
                name = from_tt_char(f.szFileName)
                size_kb = (f.nFileSize or 0) // 1024
                if name:
                    file_names.append(f"{name} ({size_kb} KB)")
            if not file_names:
                return {
                    "status": "success",
                    "files": [],
                    "total": 0,
                    "message": f"Tidak ada file yang diunggah di channel {cpath}.",
                }
            return {
                "status": "success",
                "files": file_names,
                "total": len(file_names),
                "message": f"Ditemukan {len(file_names)} file di channel {cpath}: {', '.join(file_names)}.",
            }

        elif tool_name == "get_server_properties":
            try:
                props = self.getServerProperties()
            except Exception:
                props = None
            if not props:
                return {
                    "status": "error",
                    "message": "Gagal mengambil properti server saat ini.",
                }
            sname = from_tt_char(props.szServerName) if hasattr(props, "szServerName") else "TeamTalk Server"
            motd = from_tt_char(props.szMOTD).strip() if hasattr(props, "szMOTD") else ""
            max_u = props.nMaxUsers if hasattr(props, "nMaxUsers") else 0
            ver = from_tt_char(props.szServerVersion) if hasattr(props, "szServerVersion") else ""
            parts = [f"Nama server: {sname}"]
            if motd:
                parts.append(f"MOTD: '{motd}'")
            if max_u > 0:
                parts.append(f"Kapasitas: {max_u} pengguna")
            if ver:
                parts.append(f"Versi server: {ver}")
            return {
                "status": "success",
                "message": f"Properti server: {', '.join(parts)}.",
            }

        elif tool_name == "kick_channel_users":
            if not is_admin:
                return {
                    "status": "error",
                    "error": "PERMISSION_DENIED",
                    "message": "Kamu bukan admin, jadi gak boleh kick orang.",
                }
            channel_target = str(tool_args.get("channel") or "").strip()
            reason = str(tool_args.get("reason") or "").strip()
            cid = self._find_channel_id(channel_target, context)
            if not cid:
                return {
                    "status": "error",
                    "error": "CHANNEL_NOT_FOUND",
                    "message": f"Channel '{channel_target}' tidak ditemukan di server.",
                }
            myid = self.getMyUserID() or 0
            targets = [u for u in self.getServerUsers() if u.nChannelID == cid and u.nUserID != myid]
            cpath = self.getChannelPath(cid) or channel_target
            if not targets:
                return {
                    "status": "success",
                    "message": f"Tidak ada pengguna lain di channel {cpath} untuk ditendang.",
                }
            kicked = []
            for u in targets:
                nick = from_tt_char(u.szNickname)
                if reason:
                    safe_call(self.send_pm, u.nUserID, reason)
                kicked.append(nick)
                self._schedule_action(
                    self._now() + 0.1,
                    self._execute_manual_kick,
                    u.nUserID,
                    requester_id,
                    nick,
                    reason,
                    True,
                )
            return {
                "status": "success",
                "message": f"Berhasil menendang {len(kicked)} user ({', '.join(kicked)}) dari channel {cpath}.",
            }

        elif tool_name == "get_bot_info":
            ver = getattr(config, "VERSION", "1.1.2")
            try:
                tt_ver = tt5.getVersion() if hasattr(tt5, "getVersion") else "5.x"
            except Exception:
                tt_ver = "5.x"
            return {
                "status": "success",
                "message": f"Aku bot TeamTalk untuk membantu teman-teman di server ini. Versi bot {ver}, TeamTalk SDK {tt_ver}. Fitur yang tersedia: moderasi otomatis, deteksi spam, filter kata kasar, manajemen channel, dan bantuan cerdas melalui @ai.",
            }

        else:
            return {
                "status": "error",
                "error": "UNKNOWN_TOOL",
                "message": f"Wah kayaknya belum ada deh function '{tool_name}' di bot ini.",
            }

    def _process_ai_tool_requests(self):
        """Execute pending AI tool requests on the main thread."""
        for req in self._ai_chat.drain_tool_requests():
            try:
                req.result = self._execute_ai_tool(req.name, req.args, req.context)
            except Exception as exc:
                logger.exception("AI tool execution error for %s: %s", req.name, exc)
                req.result = {"status": "error", "error": "EXECUTION_ERROR", "message": str(exc)}
            finally:
                req.event.set()

    def _process_ai_results(self):
        """Act on finished AI reviews (runs on the main thread)."""
        for context, verdict in self._ai.drain():
            entries = context.get("entries", [])
            if context.get("kind") == "test":
                answer = {
                    INSULT: "AI: insult, so it would be counted.",
                    OK: "AI: not an insult, so it would not be counted.",
                }.get(verdict, "AI did not answer, so it would not be counted.")
                safe_call(self.send_pm, context.get("requester"), answer)
                continue
            userid = context.get("userid")
            if verdict == INSULT:
                logger.info("AI review: insult (user=%s, words=%s)", userid, entries)
                self.handle_badword_violation(userid, context.get("ip", ""), "text")
                continue
            if verdict == OK:
                logger.info("AI review: not an insult; not counted (user=%s, words=%s)", userid, entries)
            else:
                logger.warning("AI review unavailable; not counted (user=%s, words=%s)", userid, entries)
            self._answer_held_question(context.get("question"))

    def handle_badword_violation(self, userid: int, ip: str, context: str):
        try:
            key = self._abuse_key(userid, ip)
            stage = self._record_abuse("badword", key, userid, ip)
            if stage:
                reason = f"badwords in {context}"
                self._handle_abuse_stage("badword", stage, userid, ip, reason, key)
                logger.warning(
                    "Badword violation (user=%s, ip=%s, context=%s, stage=%s)",
                    userid,
                    ip,
                    context,
                    stage,
                )
        except Exception:
            logger.exception("Failed to process badword violation for user %s", userid)

    # A client joins a channel by itself right after (re)connecting; joins this
    # soon after the session's login are part of logging in, not hopping.
    _JOIN_AFTER_LOGIN_GRACE_SEC = 10.0
    # A login within this long after a logout of the same username+IP (or while
    # that session is still online) looks like a reconnect after a dropped line.
    _RECONNECT_WINDOW_SEC = 3.0
    # Reconnects exempt from login abuse per person and period; beyond that they
    # count again, so a script cannot hide login spam behind fake reconnects.
    _RECONNECT_FREE_PER_PERIOD = 5
    _RECONNECT_PERIOD_SEC = 300.0
    _MODERATION_HOUSEKEEPING_SEC = 60.0

    def _moderation_housekeeping(self):
        """Forget bookkeeping for abuse records and reconnects that have expired."""
        now = self._now()
        try:
            tracked = self._abuse.tracked_keys()
            for key in [k for k in self._abuse_subjects if k not in tracked]:
                self._abuse_subjects.pop(key, None)
            # Pending kicks/bans run within seconds, so old marks are useless
            for key in [k for k, ts in self._forgiven.items() if now - ts > 60]:
                self._forgiven.pop(key, None)
            for person in [
                p
                for p, times in self._reconnect_history.items()
                if not times or now - max(times) > self._RECONNECT_PERIOD_SEC
            ]:
                self._reconnect_history.pop(person, None)
            # AI context older than the window is never used again
            for key, buf in list(self._recent_messages.items()):
                while buf and now - buf[0][1] > self._AI_CONTEXT_WINDOW_SEC + self._AI_CONTEXT_WAIT_SEC:
                    buf.popleft()
                if not buf:
                    self._recent_messages.pop(key, None)
        except Exception:
            logger.exception("Moderation housekeeping failed")
        finally:
            self._schedule_action(
                now + self._MODERATION_HOUSEKEEPING_SEC, self._moderation_housekeeping
            )

    def _in_login_sync(self) -> bool:
        """True while the server replays already-online users after our login."""
        return self._now() < self._login_sync_until

    def _is_shared_account(self, username: str) -> bool:
        values = getattr(config, "SHARED_ACCOUNTS", []) or []
        return str(username or "").strip().lower() in {str(v).strip().lower() for v in values}

    def _person_key(self, user: User) -> Optional[Tuple[str, str]]:
        """Identify a person across sessions by (username, IP).

        On a shared account (SHARED_ACCOUNTS, e.g. all students on "murid")
        the username says nothing about who it is, so the nickname is used
        as well; otherwise everyone on that account behind one IP would count
        as one person.
        """
        username = from_tt_char(user.szUsername).strip().lower()
        ip = from_tt_char(user.szIPAddress).strip()
        if not username or not ip:
            return None
        if self._is_shared_account(username):
            nickname = from_tt_char(user.szNickname).strip().lower()
            if nickname:
                return (f"{username}/{nickname}", ip)
        return (username, ip)

    def _is_reconnect(self, user: User) -> bool:
        """Whether this login looks like a reconnect after a dropped connection.

        When a connection drops, the server keeps the old session until it
        times out or the new login replaces it (the old one is logged out just
        before the new login is announced). So a reconnect arrives while a
        session with the same username and IP is still online, or right after
        it left; a deliberate logout/login leaves a gap. Only a limited number
        of reconnects per period are exempt.
        """
        person = self._person_key(user)
        if person is None:
            return False
        now = self._now()
        recent_logout = now - self._recent_logouts.get(person, 0.0) <= self._RECONNECT_WINDOW_SEC
        still_online = not recent_logout and any(
            other.nUserID != user.nUserID and self._person_key(other) == person
            for other in (safe_call(self.getServerUsers, default=None) or [])
        )
        if not (recent_logout or still_online):
            return False
        history = [
            ts
            for ts in self._reconnect_history.get(person, [])
            if now - ts <= self._RECONNECT_PERIOD_SEC
        ]
        exempt = len(history) < self._RECONNECT_FREE_PER_PERIOD
        if exempt:
            history.append(now)
        self._reconnect_history[person] = history
        return exempt

    def _handle_abuse_login(self, user: User):
        if not self._feature_on("login") or self._in_login_sync():
            return
        if self._is_reconnect(user):
            logger.info(
                "Login of user %s treated as reconnect (unstable connection); not counted",
                user.nUserID,
            )
            return
        ip = from_tt_char(user.szIPAddress)
        # Per person, so other people logging in from the same IP (a school
        # network) do not add to each other's count
        key = self._person_abuse_key(user)
        stage = self._record_abuse("login", key, user.nUserID, ip, user)
        if stage:
            self._handle_abuse_stage(
                "login", stage, user.nUserID, ip, "login abuse", key
            )
            logger.warning(
                "Login abuse detected (user=%s, ip=%s, stage=%s)",
                user.nUserID,
                ip,
                stage,
            )

    def _handle_abuse_join(self, user: User):
        if not self._feature_on("join") or self._in_login_sync():
            return
        login_ts = self._session_login_ts.get(user.nUserID)
        if login_ts is not None and self._now() - login_ts <= self._JOIN_AFTER_LOGIN_GRACE_SEC:
            return
        ip = from_tt_char(user.szIPAddress)
        # Count per person (username + IP) so a reconnect keeps the count while
        # other people behind the same IP (e.g. a school network) do not add to it
        key = self._person_abuse_key(user)
        stage = self._record_abuse("join", key, user.nUserID, ip, user)
        if stage:
            self._handle_abuse_stage("join", stage, user.nUserID, ip, "join abuse", key)
            logger.warning(
                "Join abuse detected (user=%s, ip=%s, stage=%s)",
                user.nUserID,
                ip,
                stage,
            )

    def _handle_abuse_message(self, userid: int, ip: str):
        if userid == (self.getMyUserID() or 0):
            return
        if getattr(config, "ANTISPAM_IGNORE_ADMINS", True):
            try:
                u = self.getUser(userid)
                if u.uUserType & UserType.USERTYPE_ADMIN:
                    return
            except Exception:
                pass
        try:
            key = self._abuse_key(userid, ip)
            stage = self._record_abuse("message", key, userid, ip)
            if stage:
                reason = "message spam"
                self._handle_abuse_stage("message", stage, userid, ip, reason, key)
                logger.warning(
                    "Message spam detected (user=%s, ip=%s, stage=%s)",
                    userid,
                    ip,
                    stage,
                )
        except Exception:
            logger.exception("Failed to process message spam violation for user %s", userid)

    def _schedule_action(self, when_ts: float, fn, *args, **kwargs):
        try:
            self._scheduled_seq += 1
            heapq.heappush(
                self._scheduled,
                (float(when_ts), self._scheduled_seq, fn, args, kwargs or {}),
            )
            logger.debug(
                "Scheduled action %s for %s (seq=%s)",
                getattr(fn, "__name__", repr(fn)),
                when_ts,
                self._scheduled_seq,
            )
        except Exception:
            logger.exception(
                "Failed to schedule action %s", getattr(fn, "__name__", repr(fn))
            )

    def process_scheduled(self):
        safe_call(self._process_ai_results)
        safe_call(self._process_ai_tool_requests)
        safe_call(self._process_ai_chat_results)
        try:
            now = self._now()
            while self._scheduled and self._scheduled[0][0] <= now:
                _ts, _seq, fn, args, kwargs = heapq.heappop(self._scheduled)
                try:
                    fn(*(args or ()), **(kwargs or {}))
                    logger.debug(
                        "Executed scheduled action %s (seq=%s)",
                        getattr(fn, "__name__", repr(fn)),
                        _seq,
                    )
                except Exception:
                    logger.exception(
                        "Scheduled action %s failed", getattr(fn, "__name__", repr(fn))
                    )
        except Exception:
            logger.exception("process_scheduled loop failed")

    # A wizard left idle this long is dropped, which also resumes interception
    # of the user's PMs. Confirmations keep their own 120 s limit.
    _PROMPT_IDLE_TIMEOUT_SEC = 180.0
    _PROMPT_CHECK_INTERVAL_SEC = 15.0
    _CONFIRMATION_TIMEOUT_SEC = 120.0

    def _users_in_prompt(self) -> set:
        return (
            set(self._confirmations)
            | set(self._channel_wizards)
            | set(self._registration_wizards)
            | set(self._badword_menus)
            | set(self._abuse_menus)
        )

    def _sync_prompt_intercepts(self):
        """Stop intercepting PMs of users who are answering a bot prompt.

        The TeamTalk client library never fills ``nToUserID`` (the server sends
        ``destuserid`` but the client reads ``userid``), so an intercepted PM to
        another user looks exactly like a PM to the bot. While a user answers a
        wizard or confirmation, interception of that user's PMs is paused, so
        every PM the bot receives from them really was sent to the bot and
        chat with other users cannot be taken as an answer. Interception
        resumes as soon as the prompt ends.
        """
        active = self._users_in_prompt()
        now = self._now()
        for user_id in active - set(self._prompt_activity):
            self._prompt_activity[user_id] = now
            self._subscribe_text_from_user(user_id)
            logger.debug("PM interception paused for user %s (prompt open)", user_id)
        for user_id in set(self._prompt_activity) - active:
            self._prompt_activity.pop(user_id, None)
            self._subscribe_text_from_user(user_id)
            logger.debug("PM interception resumed for user %s", user_id)

    def _prompt_housekeeping(self):
        """Expire abandoned prompts so PM interception does not stay paused."""
        now = self._now()
        try:
            for user_id, pending in list(self._confirmations.items()):
                if now - pending.get("ts", 0) > self._CONFIRMATION_TIMEOUT_SEC:
                    self._confirmations.pop(user_id, None)
                    desc = pending.get("desc", "action")
                    safe_call(self.send_pm, user_id, f"Confirmation timed out: {desc}")
            for user_id, last in list(self._prompt_activity.items()):
                if now - last <= self._PROMPT_IDLE_TIMEOUT_SEC:
                    continue
                if self._channel_wizards.pop(user_id, None) is not None:
                    safe_call(
                        self.send_pm,
                        user_id,
                        "[Create Channel] Timed out due to inactivity. Send /rc again to restart.",
                    )
                if user_id in self._registration_wizards:
                    self._cancel_registration_wizard(user_id, notify=False)
                    safe_call(
                        self.send_pm,
                        user_id,
                        "[Register] Timed out due to inactivity. Send /ru again to restart.",
                    )
                safe_call(
                    badword_menu.close,
                    self,
                    user_id,
                    "[Badwords] Menu closed due to inactivity. Send /bw to open it again.",
                )
                safe_call(
                    abuse_menu.close,
                    self,
                    user_id,
                    "[Auto-moderation] Menu closed due to inactivity. Send /ab to open it again.",
                )
                logger.info("Prompt of user %s expired after inactivity", user_id)
            self._sync_prompt_intercepts()
        except Exception:
            logger.exception("Prompt housekeeping failed")
        finally:
            self._schedule_action(
                now + self._PROMPT_CHECK_INTERVAL_SEC, self._prompt_housekeeping
            )

    def _subscribe_text_from_all(self):
        try:
            users = self.getServerUsers()
            for u in users:
                self._subscribe_text_from_user(u.nUserID)
            logger.debug("Subscribed to text messages from %s users", len(users))
        except Exception:
            logger.exception("Failed to subscribe to all users")

    def _subscribe_text_from_user(self, user_id: int):
        # Subscribe + Intercept to receive this user's messages regardless of channel
        try:
            if user_id == (self.getMyUserID() or 0):
                logger.debug(
                    "Skipping subscription update for bot user (user=%s)", user_id
                )
                return
            # Always allow direct PMs to bot (for commands)
            subs = Subscription.SUBSCRIBE_USER_MSG
            
            types = set()
            if self._bw_enabled():
                types.update(self._bw_types())
            if self._spam_enabled():
                spam_types = self._spam_types()
                types.update(spam_types)
                
            # Subscribe to additional types only if configured and feature enabled.
            # @ai questions arrive as channel messages from any channel.
            if "CHANNEL" in types or self._ai_chat_active():
                subs |= Subscription.SUBSCRIBE_CHANNEL_MSG
                subs |= Subscription.SUBSCRIBE_INTERCEPT_CHANNEL_MSG
            if "BROADCAST" in types:
                subs |= Subscription.SUBSCRIBE_BROADCAST_MSG
            # Not while the user answers a prompt (see _sync_prompt_intercepts)
            if "PRIVATE" in types and user_id not in self._prompt_activity:
                subs |= Subscription.SUBSCRIBE_INTERCEPT_USER_MSG
            mask = int(subs)
            cached = self._subscription_cache.get(user_id)
            if cached == mask:
                logger.debug("Subscription mask unchanged for user %s", user_id)
                return
            # doSubscribe only adds flags, so dropped flags need doUnsubscribe
            removed = (cached or 0) & ~mask
            if removed and self.doUnsubscribe(user_id, removed) <= 0:
                return
            added = mask & ~(cached or 0)
            if added and self.doSubscribe(user_id, added) <= 0:
                return
            self._subscription_cache[user_id] = mask
            logger.debug(
                "Updated subscription for user %s with mask %s", user_id, mask
            )
        except Exception:
            logger.exception("Failed to subscribe to user %s", user_id)

    def _handle_register_user(self, requester_id: int, username: str):
        if not self._logged_in:
            self.send_pm(
                requester_id,
                "The bot is not logged in as admin yet. Please try again later.",
            )
            logger.warning(
                "Register user denied; bot not logged in (requester=%s)", requester_id
            )
            return

        requester = safe_call(self.getUser, requester_id)
        if requester and not self._registration_user_allowed(requester):
            msg = str(
                getattr(
                    config,
                    "REGISTRATION_NOT_ALLOWED_MESSAGE",
                    "You already have a registered account.",
                )
                or "You already have a registered account."
            )
            self.send_pm(requester_id, msg)
            logger.info(
                "Register user rejected by allowlist (requester=%s)", requester_id
            )
            return

        ip = self._get_user_ip(requester_id)
        if not self._registration_can_use_ip(ip):
            msg = self._registration_limit_message()
            if msg:
                self.send_pm(requester_id, msg)
            logger.info(
                "Register user rejected due to IP limit (requester=%s, ip=%s)",
                requester_id,
                ip,
            )
            return

        self._cancel_registration_wizard(requester_id, notify=False)
        session = {
            "step": "ask_username",
            "username": None,
            "password": None,
            "fullname": None,
            "pending_username": None,
            "ip": ip,
            "policy_checked": self._now(),
        }
        self._registration_wizards[requester_id] = session
        registration_wizard.start(self, requester_id, username.strip())
        logger.debug("Registration wizard initialized (requester=%s)", requester_id)

    # ============ Channel Creation Wizard ============
    def _wizard_handle_response(self, requester_id: int, content: str):
        import channel_wizard

        channel_wizard.handle_response(self, requester_id, content)
        return

    def _finalize_channel_create(self, requester_id: int):
        import channel_wizard

        channel_wizard.finalize_channel_create(self, requester_id)
        return

    def _handle_delete_channel(self, requester_id: int, target: str, force: bool):
        if not self._logged_in:
            self.send_pm(
                requester_id,
                "The bot is not logged in as admin yet. Please try again later.",
            )
            logger.warning(
                "Delete channel denied; bot not logged in (requester=%s, target=%s)",
                requester_id,
                target,
            )
            return
        path = self._normalize_channel_target_to_path(target)
        # Check before asking, so only owners/admins can open this prompt
        if not self._check_delete_channel_allowed(requester_id, path):
            return
        if not force:
            self._confirmations[requester_id] = {
                "kind": "delete_channel",
                "data": {"path": path},
                "desc": f"delete channel '{path}'",
                "ts": self._now(),
            }
            self.send_pm(
                requester_id,
                f"Are you sure you want to delete channel '{path}'? Reply 'y' to confirm or 'n' to cancel.",
            )
            logger.debug(
                "Delete channel confirmation requested (requester=%s, path=%s)",
                requester_id,
                path,
            )
            return
        self._perform_delete_channel(requester_id, path)

    _SHARED_NO_CHANNELS = (
        "Shared accounts cannot create or manage channels via the bot, because everyone "
        "on the account would own them. Ask an admin."
    )

    def _check_delete_channel_allowed(self, requester_id: int, path: str) -> bool:
        # Authorization: admins can delete any channel; non-admins only their own (based on cache)
        if self._is_admin(requester_id):
            return True
        requester_username = self._get_username(requester_id)
        if self._is_shared_account(requester_username):
            self.send_pm(requester_id, self._SHARED_NO_CHANNELS)
            return False
        owner = cache_store.get_owner(path)
        if owner and owner == requester_username:
            return True
        self.send_pm(
            requester_id,
            "You are not the owner of this channel. Deletion denied.",
        )
        logger.warning(
            "Delete channel denied; requester=%s is not owner (path=%s, owner=%s)",
            requester_id,
            path,
            owner,
        )
        return False

    def _perform_delete_channel(self, requester_id: int, path: str):
        # Re-checked here: ownership may have changed while confirming
        if not self._check_delete_channel_allowed(requester_id, path):
            return

        cid = self.getChannelIDFromPath(path)
        if not cid or cid <= 0:
            self.send_pm(requester_id, f"Channel path not found: {path}")
            logger.error(
                "Delete channel failed; path not found (requester=%s, path=%s)",
                requester_id,
                path,
            )
            return
        cmdid = self.doRemoveChannel(cid)
        if cmdid <= 0:
            self.send_pm(requester_id, f"Failed to send delete-channel command: {path}")
            logger.error(
                "Delete channel command not dispatched (requester=%s, path=%s)",
                requester_id,
                path,
            )
            return
        meta = {
            "kind": "delete_channel",
            "channel_path": path,
            "suppress_default_success": True,
        }
        self._track_pending_cmd(
            cmdid, requester_id, f"delete channel '{path}'", meta=meta
        )
        self._pending_delete_path[cmdid] = path
        logger.info(
            "Delete channel command dispatched (requester=%s, path=%s)",
            requester_id,
            path,
        )

    def _handle_delete_user(self, requester_id: int, username: str, force: bool):
        if not self._logged_in:
            self.send_pm(
                requester_id,
                "The bot is not logged in as admin yet. Please try again later.",
            )
            logger.warning(
                "Delete user denied; bot not logged in (requester=%s, target=%s)",
                requester_id,
                username,
            )
            return
        username = (username or "").strip()
        if not username:
            self.send_pm(requester_id, "Username must not be empty.")
            logger.warning(
                "Delete user denied; empty username (requester=%s)", requester_id
            )
            return
        if self._is_shared_account(username):
            # Deleting it would lock out everyone who logs in with it
            self.send_pm(
                requester_id,
                f"'{username}' is a shared account (SHARED_ACCOUNTS) and cannot be deleted "
                "via the bot. An admin can still remove it in the TeamTalk client.",
            )
            logger.warning(
                "Delete of shared account refused (requester=%s, target=%s)",
                requester_id,
                username,
            )
            return
        allowed_usernames = self._registration_allowed_usernames()
        if allowed_usernames and username.lower() in allowed_usernames:
            self.send_pm(requester_id, "This account cannot be deleted via the bot.")
            logger.info(
                "Delete user rejected by blocklist (requester=%s, target=%s)",
                requester_id,
                username,
            )
            return
        # Authorization: only admins can delete arbitrary users; non-admins can only delete their own account
        requester_username = self._get_username(requester_id)
        requester_username_lower = (requester_username or "").lower()
        is_admin = self._is_admin(requester_id)
        if not is_admin and username.lower() != requester_username_lower:
            self.send_pm(
                requester_id,
                "You are not an admin. You can only delete your own account.",
            )
            logger.warning(
                "Delete user denied; requester not admin (requester=%s, target=%s)",
                requester_id,
                username,
            )
            return
        kick_self = (not is_admin) and (username.lower() == requester_username_lower)
        if not force:
            self._confirmations[requester_id] = {
                "kind": "delete_user",
                "data": {"username": username, "kick_self": kick_self},
                "desc": f"delete user '{username}'",
                "ts": self._now(),
            }
            self.send_pm(
                requester_id,
                f"Are you sure you want to delete user '{username}'? Reply 'y' to confirm or 'n' to cancel.",
            )
            logger.debug(
                "Delete user confirmation requested (requester=%s, target=%s)",
                requester_id,
                username,
            )
            return
        self._perform_delete_user(requester_id, username, kick_self)

    def _perform_delete_user(
        self, requester_id: int, username: str, kick_self: bool = False
    ):
        cmdid = self.doDeleteUserAccount(username)
        if cmdid <= 0:
            self.send_pm(
                requester_id, f"Failed to send delete-user command: {username}"
            )
            logger.error(
                "Delete user command not dispatched (requester=%s, target=%s)",
                requester_id,
                username,
            )
            return
        meta = {
            "deleted_username": username,
            "kick_after_delete": bool(kick_self),
            "kind": "delete_user",
            "suppress_default_success": True,
        }
        self._track_pending_cmd(
            cmdid, requester_id, f"delete user '{username}'", meta=meta
        )
        logger.info(
            "Delete user command dispatched (requester=%s, target=%s)",
            requester_id,
            username,
        )

    def _hidden_channel_ids(self, channels) -> set:
        """Return IDs of hidden channels and every channel nested below them."""
        parents = {ch.nChannelID: ch.nParentID for ch in channels}
        hidden = {
            ch.nChannelID
            for ch in channels
            if ch.uChannelType & ChannelType.CHANNEL_HIDDEN
        }
        result = set()
        for cid in parents:
            node, seen = cid, set()
            while node and node not in seen:
                if node in hidden:
                    result.add(cid)
                    break
                seen.add(node)
                node = parents.get(node, 0)
        return result

    def _handle_list_channels(self, requester_id: int, base_path: str):
        try:
            chans = self.getServerChannels()
            # The bot is an admin and sees hidden channels; regular users must not
            skip_ids = (
                set() if self._is_admin(requester_id) else self._hidden_channel_ids(chans)
            )
            lines = []
            base = (base_path or "").strip("/")
            for ch in chans:
                if ch.nChannelID in skip_ids:
                    continue
                path = self.getChannelPath(ch.nChannelID)
                # path value might be bytes or str depending on wrapper; ensure str
                full = str(path)
                if base:
                    if full == base or full.startswith(base + "/"):
                        lines.append(full)
                else:
                    lines.append(full)
            if not lines:
                self.send_pm(requester_id, "No matching channels found.")
                logger.debug(
                    "List channels returned no results (requester=%s, base=%s)",
                    requester_id,
                    base,
                )
                return
            lines = sorted(set(lines))
            msg = "\n".join(lines)
            self.send_pm(requester_id, msg)
            logger.debug(
                "List channels returned %s entries for requester=%s",
                len(lines),
                requester_id,
            )
        except Exception as e:
            self.send_pm(requester_id, f"Failed to retrieve channel list: {e}")
            logger.exception(
                "Failed to list channels for requester=%s (base=%s)",
                requester_id,
                base_path,
            )

    # Owner helpers and commands
    def _resolve_path(self, target: str) -> str:
        return self._normalize_channel_target_to_path(target)

    def _handle_owner_check(self, requester_id: int, target: str):
        path = self._resolve_path(target)
        owner = cache_store.get_owner(path)
        if not owner:
            self.send_pm(requester_id, f"No owner recorded for: {path}")
            logger.debug(
                "Owner check returned no record (requester=%s, path=%s)",
                requester_id,
                path,
            )
            return
        self.send_pm(requester_id, f"Channel owner for {path}: {owner}")
        logger.debug(
            "Owner check for %s -> %s (requester=%s)", path, owner, requester_id
        )

    def _handle_set_owner(self, requester_id: int, target: str, username: str):
        # Admin only
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can set channel owners.")
            return
        path = self._resolve_path(target)
        cid = self.getChannelIDFromPath(path)
        if not cid or cid <= 0:
            self.send_pm(requester_id, f"Channel path not found: {path}")
            return
        now = self._now()
        cache_store.set_owner(path, username, last_active_at=now)
        self._schedule_channel_expiry(path)
        self.send_pm(requester_id, f"Channel owner for {path} set to: {username}")
        logger.info(
            "Owner set (path=%s, owner=%s, requester=%s)", path, username, requester_id
        )

    def _handle_transfer_owner(self, requester_id: int, target: str, username: str):
        path = self._resolve_path(target)
        current = cache_store.get_owner(path)
        # Reuse admin check helper for clarity
        is_admin = self._is_admin(requester_id)
        requester_username = self._get_username(requester_id)
        if not is_admin and self._is_shared_account(requester_username):
            self.send_pm(requester_id, self._SHARED_NO_CHANNELS)
            return
        if not is_admin:
            if not current or current != requester_username:
                self.send_pm(
                    requester_id,
                    "Only the current owner or an admin may transfer ownership.",
                )
                return
        cid = self.getChannelIDFromPath(path)
        if not cid or cid <= 0:
            self.send_pm(requester_id, f"Channel path not found: {path}")
            return
        now = self._now()
        cache_store.set_owner(path, username, last_active_at=now)
        self._schedule_channel_expiry(path)
        self.send_pm(
            requester_id, f"Channel owner for {path} transferred to: {username}"
        )
        logger.info(
            "Owner transferred (path=%s, new_owner=%s, requester=%s)",
            path,
            username,
            requester_id,
        )

    _BADWORD_ADMIN_COMMANDS = ("/bw", "/bwl", "/bwa", "/bwd", "/bwt")
    _BADWORD_LIST_HINT = "Delete by number: /bwd 3, /bwd 3,5 or /bwd 3-5."

    def _is_badword_admin_command(self, user_id: int, content: str) -> bool:
        """Admin badword commands quote badwords on purpose; don't flag them."""
        parts = (content or "").split(maxsplit=1)
        if not parts or parts[0].lower() not in self._BADWORD_ADMIN_COMMANDS:
            return False
        return self._is_admin(user_id)

    def _handle_badword_list(
        self, requester_id: int, query: str = "", hint: Optional[str] = None
    ):
        """Send a numbered badword list, optionally narrowed by ``query``.

        The entries shown are remembered per admin so /bwd can take numbers
        that stay valid even if the list changes afterwards.
        """
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can view badwords.")
            return
        all_words = self._badwords.list_words()
        query = (query or "").strip().lower()
        words = [w for w in all_words if query in w] if query else all_words
        self._badword_list_snapshots[requester_id] = words
        if not words:
            self.send_pm(
                requester_id,
                f"No badwords match '{query}'." if query else "Badword list is empty.",
            )
            return
        if query:
            header = f"[Badwords matching '{query}'] {len(words)} of {len(all_words)}"
        else:
            header = f"[Badwords] {len(words)} entries"
        if not self._bw_enabled():
            header += " (message filter is OFF, see /abt)"
        lines = [f"{number}. {word}" for number, word in enumerate(words, start=1)]
        hint = self._BADWORD_LIST_HINT if hint is None else hint
        if hint:
            lines.append(hint)
        self._send_chunked_lines(requester_id, lines, header=header)
        logger.info(
            "Badword list sent to requester=%s (%s entries)", requester_id, len(words)
        )

    def _handle_badword_add(self, requester_id: int, csv_words: str):
        """Add comma- or space-separated ``csv_words`` to the badword list."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can add badwords.")
            return
        words = self._parse_badword_csv(csv_words)
        if not words:
            self.send_pm(requester_id, "Provide at least one word to add.")
            return
        errors = [err for err in map(self._badwords.pattern_error, words) if err]
        added = self._badwords.add_words(words)
        valid = {w for w in words if not self._badwords.pattern_error(w)}
        already = sorted(valid - set(added))
        lines = []
        if added:
            lines.append(f"Added badwords: {', '.join(added)}")
            logger.info("Badwords added by requester=%s: %s", requester_id, added)
        if already:
            lines.append(f"Already in the list: {', '.join(already)}")
        lines.extend(f"Not added: {err}" for err in errors)
        self.send_pm(requester_id, "\n".join(lines))

    def _resolve_list_numbers(
        self, snapshot: Optional[List[Any]], tokens: List[str], list_command: str
    ) -> Tuple[List[Any], List[str], List[str]]:
        """Split ``tokens`` into items picked by number and plain words.

        Numbers and ranges (``3``, ``3-5``) refer to ``snapshot``, the last
        numbered list shown to the admin. Returns ``(items, words, problems)``.
        """
        items: List[Any] = []
        words: List[str] = []
        problems: List[str] = []
        for token in tokens:
            match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
            if not match:
                words.append(token)
                continue
            if snapshot is None:
                problems.append(f"'{token}': send {list_command} first to see the numbers.")
                continue
            first = int(match.group(1))
            last = int(match.group(2) or first)
            if first > last:
                first, last = last, first
            items.extend(snapshot[max(first, 1) - 1 : min(last, len(snapshot))])
            if first < 1 or last > len(snapshot):
                problems.append(
                    f"'{token}': your last list only has {len(snapshot)} entries."
                )
        return items, words, problems

    def _resolve_badword_targets(
        self, requester_id: int, tokens: List[str]
    ) -> Tuple[List[str], List[str]]:
        """Turn list numbers/ranges into entries; return ``(words, problems)``.

        Numbers refer to the last list shown to this admin by /bwl or /bw.
        """
        items, words, problems = self._resolve_list_numbers(
            self._badword_list_snapshots.get(requester_id), tokens, "/bwl"
        )
        return items + words, problems

    def _handle_badword_delete(self, requester_id: int, csv_words: str):
        """Remove badwords given as words and/or numbers from the last list."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can delete badwords.")
            return
        tokens = self._parse_badword_csv(csv_words)
        if not tokens:
            self.send_pm(requester_id, "Provide at least one word or number to remove.")
            return
        words, problems = self._resolve_badword_targets(requester_id, tokens)
        removed = self._badwords.remove_words(words) if words else []
        missing = sorted(set(words) - set(removed))
        lines = []
        if removed:
            lines.append(f"Removed badwords: {', '.join(removed)}")
            logger.info("Badwords removed by requester=%s: %s", requester_id, removed)
        if missing:
            lines.append(f"Not in the list: {', '.join(missing)}")
        lines.extend(problems)
        self.send_pm(requester_id, "\n".join(lines))

    def _handle_badword_test(self, requester_id: int, text: str):
        """Tell an admin which entries would flag ``text``."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can test badwords.")
            return
        hits = self._badwords.matching_entries(text)
        if hits:
            message = f"Would be flagged by: {', '.join(hits)}"
            ambiguous = self._ambiguous_words()
            if self._ai_active() and all(hit in ambiguous for hit in hits):
                context = {"kind": "test", "requester": requester_id, "entries": hits}
                if self._ai.submit(text, hits, context):
                    message += "\nAmbiguous word, asking the AI..."
                else:
                    message += "\nAmbiguous word, but the AI is busy: it would not be counted."
            self.send_pm(requester_id, message)
        else:
            self.send_pm(requester_id, "Not flagged.")

    def _handle_version_info(self, requester_id: int):
        """Send runtime version details to the requester."""
        info = version.collect_version_info()
        bot_version = info["bot_version"]
        lines = [
            "[Bot Version]",
            f"Bot version: {bot_version}",
            f"OS: {info['os_name']} {info['os_version']} ({info['os_build']})",
            f"Platform: {info['platform']}",
            f"TeamTalk SDK version: {info['sdk_version']}",
        ]
        if self._is_admin(requester_id):
            lines.extend(
                [
                    f"Bindings file: {info['bindings_path']}",
                    f"Native library: {info['library_path']}",
                ]
            )
        message = "\n".join(lines)
        self.send_pm(requester_id, message)

    def _handle_change_status(self, requester_id: int, status: str):
        # Admin only
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can change the bot status.")
            logger.warning("Status change denied; requester=%s not admin", requester_id)
            return
        msg = (status or "").strip()
        if not msg:
            self.send_pm(requester_id, "Status message must not be empty.")
            logger.warning(
                "Status change denied; empty message (requester=%s)", requester_id
            )
            return
        # Truncate to TT_STRLEN-1
        try:
            maxlen = int(TT_STRLEN) - 1
        except Exception:
            maxlen = 511
        if len(msg) > maxlen:
            msg = msg[:maxlen]
        try:
            myid = self.getMyUserID() or 0
        except Exception:
            myid = 0
        if self._status_mode is not None:
            mode = self._status_mode
        else:
            user_obj = safe_call(self.getUser, myid)
            try:
                mode = user_obj.nStatusMode if user_obj else 0
            except Exception:
                mode = 0
        if isinstance(mode, str):
            try:
                mode = int(mode)
            except Exception:
                mode = 0
        if self._status_msg == msg and self._status_mode == mode:
            self.send_pm(requester_id, "The bot status already uses that message.")
            logger.debug(
                "Status change skipped; same message (requester=%s)", requester_id
            )
            return
        cmdid = self.doChangeStatus(int(mode or 0), msg)
        if cmdid <= 0:
            self.send_pm(requester_id, "Failed to send change-status command.")
            logger.error(
                "Status change command not dispatched (requester=%s)", requester_id
            )
            return
        self._track_pending_cmd(cmdid, requester_id, "change bot status")
        logger.info("Status change command dispatched (requester=%s)", requester_id)

    def _handle_help(self, requester_id: int, topic: str):
        text = get_topic_help(topic)
        # send_pm already handles splitting long messages safely
        self.send_pm(requester_id, text)

    # ============ Kick/Ban ============
    def _is_admin(self, user_id: int) -> bool:
        try:
            u = self.getUser(user_id)
            return bool(u.uUserType & UserType.USERTYPE_ADMIN)
        except Exception:
            return False

    def _find_users_by_nicknames(self, names_csv: str):
        wanted = [n.strip().lower() for n in (names_csv or "").split(",") if n.strip()]
        if not wanted:
            return []
        found = []
        users = self.getServerUsers()
        for u in users:
            nick = from_tt_char(u.szNickname).lower()
            if nick in wanted:
                found.append(u)
        return found

    def _handle_kick_users(self, requester_id: int, names_csv: str, reason: str = ""):
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can issue kicks.")
            logger.warning("Kick denied; requester=%s not admin", requester_id)
            return
        targets = self._find_users_by_nicknames(names_csv)
        if not targets:
            self.send_pm(requester_id, "No users match the provided nicknames.")
            logger.debug(
                "Kick targets not found (requester=%s, names=%s)",
                requester_id,
                names_csv,
            )
            return
        reason_msg = str(reason or "").strip()
        delay = 1.0 if reason_msg else 0.05
        descs = []
        myid = self.getMyUserID() or 0
        for u in targets:
            if u.nUserID == myid:
                # Protect from self-kick
                continue
            nickname = from_tt_char(u.szNickname)
            if reason_msg:
                safe_call(self.send_pm, u.nUserID, reason_msg)
            descs.append(nickname)
            self._schedule_action(
                self._now() + delay,
                self._execute_manual_kick,
                u.nUserID,
                requester_id,
                nickname,
                reason_msg,
                True,
            )
        if descs:
            self.send_pm(requester_id, f"Kick scheduled for: {', '.join(descs)}")
            logger.info(
                "Kick batch scheduled (requester=%s, targets=%s)", requester_id, descs
            )
        else:
            self.send_pm(requester_id, "No kicks were scheduled.")
            logger.debug(
                "Kick batch resulted in no actions (requester=%s)", requester_id
            )

    def _handle_ban_users(self, requester_id: int, names_csv: str, reason: str = ""):
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can issue bans.")
            logger.warning("Ban denied; requester=%s not admin", requester_id)
            return
        targets = self._find_users_by_nicknames(names_csv)
        if not targets:
            self.send_pm(requester_id, "No users match the provided nicknames.")
            logger.debug(
                "Ban targets not found (requester=%s, names=%s)",
                requester_id,
                names_csv,
            )
            return
        mode = str(getattr(config, "BAN_TARGET", "USERNAME")).upper()
        ban_type = (
            BanType.BANTYPE_USERNAME if mode == "USERNAME" else BanType.BANTYPE_IPADDR
        )
        reason_msg = str(reason or "").strip()
        delay = 1.0 if reason_msg else 0.05
        descs = []
        myid = self.getMyUserID() or 0
        for u in targets:
            if u.nUserID == myid:
                # Protect from self-ban
                continue
            who = (
                from_tt_char(u.szUsername)
                if mode == "USERNAME"
                else from_tt_char(u.szIPAddress)
            )
            nickname = from_tt_char(u.szNickname)
            if reason_msg:
                safe_call(self.send_pm, u.nUserID, reason_msg)
            descs.append(nickname)
            self._schedule_action(
                self._now() + delay,
                self._execute_manual_ban,
                u.nUserID,
                requester_id,
                int(ban_type),
                who,
                nickname,
                reason_msg,
            )
        if descs:
            self.send_pm(requester_id, f"Ban scheduled for: {', '.join(descs)}")
            logger.info(
                "Ban batch scheduled (requester=%s, targets=%s)", requester_id, descs
            )
        else:
            self.send_pm(requester_id, "No bans were scheduled.")
            logger.debug(
                "Ban batch resulted in no actions (requester=%s)", requester_id
            )

    def _kick_user_by_username(self, username: str, reason: str = "") -> bool:
        uname = (username or "").strip().lower()
        if not uname:
            return False
        try:
            users = self.getServerUsers()
        except Exception:
            logger.exception("Failed to enumerate users for post-delete kick")
            return False
        myid = self.getMyUserID() or 0
        for user in users:
            try:
                current = from_tt_char(user.szUsername).strip().lower()
            except Exception:
                continue
            if current == uname:
                nickname = from_tt_char(user.szNickname)
                self._execute_manual_kick(
                    user.nUserID, myid, nickname, reason, notify_admin=False
                )
                logger.info(
                    "Issued kick for deleted user '%s' (user_id=%s)",
                    username,
                    user.nUserID,
                )
                return True
        logger.debug("Deleted user '%s' not found online for kick", username)
        return False

    def _send_custom_success_message(self, requester_id: int, meta: Dict[str, Any]):
        kind = meta.get("kind")
        if not kind or not requester_id:
            return
        if kind == "create_channel":
            name = meta.get("channel_name", "")
            base = (
                f"Channel '{name}' created successfully."
                if name
                else "Channel created successfully."
            )
            ttl_note = meta.get("ttl_note")
            message = f"{base} {ttl_note}".strip() if ttl_note else base
        elif kind == "register_user":
            username = meta.get("username", "")
            message = (
                f"Registration completed for '{username}'."
                if username
                else "Registration completed."
            )
        elif kind == "delete_channel":
            path = meta.get("channel_path", "")
            message = f"Channel '{path}' deleted." if path else "Channel deleted."
        elif kind == "delete_user":
            username = meta.get("deleted_username", "")
            message = f"User '{username}' deleted." if username else "User deleted."
        else:
            return
        safe_call(self.send_pm, requester_id, message)

    # List bans aggregation state moved to __init__ to avoid class-level mutable defaults

    def _handle_list_bans(self, requester_id: int):
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can view the ban list.")
            logger.warning("List bans denied; requester=%s not admin", requester_id)
            return
        self._list_bans_requester = requester_id
        self._list_bans_buffer = []
        cmdid = self.doListBans(0, 0, 1000)
        if cmdid <= 0:
            self.send_pm(requester_id, "Failed to request ban list from the server.")
            self._list_bans_requester = None
            logger.error(
                "List bans command not dispatched (requester=%s)", requester_id
            )
            return
        self._list_bans_cmdid = cmdid
        logger.debug(
            "List bans command dispatched (requester=%s, cmdid=%s)", requester_id, cmdid
        )

    def _handle_list_users(self, requester_id: int):
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can view the user list.")
            logger.warning("List users denied; requester=%s not admin", requester_id)
            return
        self._list_users_requester = requester_id
        self._list_users_buffer = []
        cmdid = self.doListUserAccounts(0, 1000)
        if cmdid <= 0:
            self.send_pm(requester_id, "Failed to request user list from the server.")
            self._list_users_requester = None
            logger.error(
                "List users command not dispatched (requester=%s)", requester_id
            )
            return
        self._list_users_cmdid = cmdid
        logger.debug(
            "List users command dispatched (requester=%s, cmdid=%s)",
            requester_id,
            cmdid,
        )

    def _handle_unban(self, requester_id: int, values_csv: str):
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can issue unban commands.")
            logger.warning("Unban denied; requester=%s not admin", requester_id)
            return
        by_ip = self._ban_target_is_ip()
        vals = [v.strip() for v in (values_csv or "").split(",") if v.strip()]
        if not vals:
            self.send_pm(requester_id, "At least one value is required.")
            logger.debug("Unban request missing values (requester=%s)", requester_id)
            return
        descs = []
        for v in vals:
            if by_ip:
                cmdid = self.doUnBanUser(v, 0)
                if cmdid > 0:
                    self._track_pending_cmd(cmdid, requester_id, f"unban IP '{v}'")
                    descs.append(v)
            else:
                bu = BannedUser()
                assign_tt_char_array((bu, "szUsername"), v)
                bu.uBanTypes = BanType.BANTYPE_USERNAME
                assign_tt_char_array((bu, "szIPAddress"), "")
                assign_tt_char_array((bu, "szChannelPath"), "")
                assign_tt_char_array((bu, "szNickname"), "")
                assign_tt_char_array((bu, "szOwner"), "")
                cmdid = self.doUnbanUserEx(bu)
                if cmdid > 0:
                    self._track_pending_cmd(
                        cmdid, requester_id, f"unban username '{v}'"
                    )
                    descs.append(v)
        for v in descs:
            self._forget_temp_ban(v)
        if descs:
            self.send_pm(requester_id, f"Unban scheduled for: {', '.join(descs)}")
            logger.info(
                "Unban batch scheduled (requester=%s, values=%s)", requester_id, descs
            )
        else:
            self.send_pm(requester_id, "No unbans were scheduled.")
            logger.debug(
                "Unban batch resulted in no actions (requester=%s)", requester_id
            )

    # ============ Feature switch commands (/abt) ============
    # Abuse kinds whose pending warnings are dropped when a feature is switched off
    _FEATURE_KINDS = {
        "login": ("login",),
        "join": ("join",),
        "spam": ("message",),
        "badwords": ("badword",),
    }
    _FEATURE_HINT = "Switch with /abt <number or name> [on|off], e.g. /abt 2 off."
    _ON_WORDS = {"on", "1", "yes", "y", "true", "nyala", "hidup", "aktif"}
    _OFF_WORDS = {"off", "0", "no", "n", "false", "mati", "nonaktif"}

    def _set_feature(self, name: str, enabled: bool) -> List[str]:
        """Switch a feature on/off and return notes about side effects.

        Only switches that differ from config.json are stored.
        """
        if enabled == self._feature_default(name):
            self._feature_overrides.pop(name, None)
        else:
            self._feature_overrides[name] = enabled
        try:
            feature_toggles.write(self._feature_overrides)
        except Exception:
            logger.exception("Failed to save feature switches")
        notes = []
        if not enabled:
            # Switching off because of false positives should take effect now:
            # drop current strikes and cancel kicks/bans that are still pending
            now = self._now()
            cleared = 0
            for kind, key in self._abuse.tracked_keys():
                if kind in self._FEATURE_KINDS.get(name, ()):
                    self._abuse.reset(kind, key)
                    self._forgiven[key] = now
                    cleared += 1
            if cleared:
                notes.append(f"Cleared {cleared} pending warning record(s).")
        if name in ("spam", "badwords", "pm", "aichat"):
            # Which messages the bot intercepts depends on these switches
            safe_call(self._subscribe_text_from_all)
        if name == "ai" and not enabled:
            self._recent_messages.clear()  # context is only kept while the AI is on
        logger.info("Feature '%s' switched %s", name, "on" if enabled else "off")
        return notes

    def _handle_feature_toggle(
        self, requester_id: int, args: str, hint: Optional[str] = None
    ):
        """/abt lists the switches; /abt <number|name> [on|off] changes one."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can switch features.")
            return
        parts = str(args or "").split()
        names = [name for name, _desc, _key in self._FEATURES]
        descriptions = {name: desc for name, desc, _key in self._FEATURES}
        if not parts or parts[0].lower() in ("list", "ls", "lihat"):
            lines = []
            for number, name in enumerate(names, start=1):
                line = f"{number}. {name} - {descriptions[name]}: {'ON' if self._feature_on(name) else 'OFF'}"
                if name in self._AI_FEATURES and not self._ai.configured:
                    line += " (not configured in config.json)"
                elif name in self._feature_overrides:
                    line += f" (config.json: {'ON' if self._feature_default(name) else 'OFF'})"
                lines.append(line)
            hint = self._FEATURE_HINT if hint is None else hint
            if hint:
                lines.append(hint)
            self._send_chunked_lines(requester_id, lines, header="[Features]")
            return
        target = parts[0].lower()
        if target.isdigit() and 1 <= int(target) <= len(names):
            name = names[int(target) - 1]
        elif target in names:
            name = target
        else:
            self.send_pm(requester_id, f"Unknown feature '{parts[0]}'. Send /abt to see the list.")
            return
        current = self._feature_on(name)
        if len(parts) > 1:
            word = parts[1].lower()
            if word in self._ON_WORDS:
                enabled = True
            elif word in self._OFF_WORDS:
                enabled = False
            else:
                self.send_pm(requester_id, f"Use on or off, e.g. /abt {name} off.")
                return
        else:
            enabled = not current  # no state given: flip it
        if enabled == current:
            self.send_pm(
                requester_id, f"{descriptions[name]} is already {'ON' if enabled else 'OFF'}."
            )
            return
        if name in self._AI_FEATURES and enabled and not self._ai.configured:
            # Switched on without credentials, every ambiguous word would go uncounted
            self.send_pm(
                requester_id,
                "The AI is not configured: set AI_CLOUDFLARE_ACCOUNT_ID and "
                "AI_CLOUDFLARE_API_TOKEN in config.json and restart the bot first.",
            )
            return
        notes = self._set_feature(name, enabled)
        self.send_pm(
            requester_id,
            "\n".join([f"{descriptions[name]}: {'ON' if enabled else 'OFF'}"] + notes),
        )

    # ============ Auto-moderation admin tools (/abs, /abf, /abw, /tb) ============
    _ABUSE_STATUS_HINT = "Forgive with /abf <number>, e.g. /abf 1."
    _WHITELIST_HINT = "Add with /abw add <username or IP>; remove with /abw del <number>."

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        if seconds < 60:
            return f"{seconds}s"
        minutes = -(-seconds // 60)  # round up, so "1m" never means "already over"
        if minutes < 60:
            return f"{minutes}m"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"

    def _abuse_subject_label(self, kind: str, key: str) -> str:
        subject = self._abuse_subjects.get((kind, key))
        if not subject:
            return key
        name = subject.get("nick") or subject.get("username") or key
        return f"{name} ({subject['ip']})" if subject.get("ip") else name

    def _handle_abuse_status(self, requester_id: int, hint: Optional[str] = None):
        """Show escalated users and active temp bans as one numbered list."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can view auto-moderation status.")
            return
        now = self._now()
        items: List[tuple] = []
        lines: List[str] = []
        warnings = self._abuse.active_stages()
        bans = sorted(self._temp_bans.values(), key=lambda e: e.get("until", 0))
        if warnings:
            lines.append("Warnings:")
            for kind, key, stage, left in warnings:
                items.append(("warning", kind, key))
                lines.append(
                    f"{len(items)}. {self._abuse_subject_label(kind, key)}: "
                    f"{kind} stage {stage}, clears in {self._fmt_duration(left)}"
                )
        if bans:
            lines.append("Temp bans:")
            for entry in bans:
                items.append(("ban", entry["mode"], entry["label"]))
                target = "IP" if entry["mode"] == "IPADDR" else "user"
                source = f"by {entry['by']}" if entry.get("by") else "auto"
                lines.append(
                    f"{len(items)}. {entry.get('who') or entry['label']} "
                    f"[{target} {entry['label']}]: {entry.get('reason') or '-'}, {source}, "
                    f"{self._fmt_duration(entry['until'] - now)} left"
                )
        self._abuse_status_snapshots[requester_id] = items
        if not items:
            self.send_pm(requester_id, "No active warnings or temp bans.")
            return
        hint = self._ABUSE_STATUS_HINT if hint is None else hint
        if hint:
            lines.append(hint)
        self._send_chunked_lines(requester_id, lines, header="[Auto-moderation]")

    def _find_abuse_items(self, name: str) -> List[tuple]:
        """Warnings and temp bans belonging to a username, nickname or IP."""
        target = str(name or "").strip().lower()
        found: List[tuple] = []
        for kind, key, _stage, _left in self._abuse.active_stages():
            subject = self._abuse_subjects.get((kind, key), {})
            candidates = {
                key.lower(),
                subject.get("username", "").lower(),
                subject.get("nick", "").lower(),
                subject.get("ip", ""),
            } - {""}
            if (
                target in candidates
                or key.lower().startswith(target + "@")
                or key.endswith("@" + target)
            ):
                found.append(("warning", kind, key))
        for entry in self._temp_bans.values():
            candidates = {
                str(entry.get("label", "")).lower(),
                str(entry.get("username", "")).lower(),
                str(entry.get("who", "")).lower(),
            } - {""}
            if target in candidates:
                found.append(("ban", entry["mode"], entry["label"]))
        return found

    def _handle_abuse_forgive(self, requester_id: int, targets: str):
        """Clear strikes and lift temp bans picked by number, username or IP."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can forgive.")
            return
        tokens = [t for t in re.split(r"[,\s]+", str(targets or "").strip()) if t]
        if not tokens:
            self.send_pm(requester_id, "Provide at least one number, username or IP to forgive.")
            return
        items, names, problems = self._resolve_list_numbers(
            self._abuse_status_snapshots.get(requester_id), tokens, "/abs"
        )
        for name in names:
            found = self._find_abuse_items(name)
            if found:
                items.extend(found)
            else:
                problems.append(f"Nothing active for '{name}'.")
        now = self._now()
        done: List[str] = []
        for item in dict.fromkeys(items):  # de-duplicate, keep order
            if item[0] == "warning":
                _, kind, key = item
                self._abuse.reset(kind, key)
                self._forgiven[key] = now
                done.append(f"{self._abuse_subject_label(kind, key)} ({kind} warning cleared)")
                continue
            _, mode, label = item
            entry = self._temp_bans.get(f"{mode}:{label}")
            if entry is None:
                problems.append(f"The temp ban on {label} has already ended.")
                continue
            if entry.get("key"):
                self._forgiven[entry["key"]] = now
            self._lift_temp_ban(mode, label, entry.get("key", ""), entry.get("kind", ""), True)
            done.append(f"{entry.get('who') or label} (temp ban on {label} lifted)")
        lines = []
        if done:
            lines.append("Forgiven: " + "; ".join(done))
            logger.info("Abuse forgiven by requester=%s: %s", requester_id, done)
        lines.extend(problems)
        self.send_pm(requester_id, "\n".join(lines) or "Nothing to forgive.")

    def _handle_abuse_whitelist(
        self, requester_id: int, args: str, hint: Optional[str] = None
    ):
        """/abw [list] | /abw add <entries> | /abw del <numbers or entries>."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can manage the whitelist.")
            return
        parts = str(args or "").strip().split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        tokens = [t for t in re.split(r"[,\s]+", parts[1] if len(parts) > 1 else "") if t]
        hint = self._WHITELIST_HINT if hint is None else hint
        if action in ("list", "ls", "lihat"):
            entries = self._whitelist.list_entries()
            self._whitelist_snapshots[requester_id] = entries
            if not entries:
                self.send_pm(requester_id, "Whitelist is empty. " + hint if hint else "Whitelist is empty.")
                return
            lines = [f"{n}. {entry}" for n, entry in enumerate(entries, start=1)]
            if hint:
                lines.append(hint)
            self._send_chunked_lines(
                requester_id, lines, header=f"[Whitelist] {len(entries)} entries"
            )
            return
        if action in ("add", "tambah"):
            if not tokens:
                self.send_pm(requester_id, "Format: /abw add <username or IP>[,...]")
                return
            added = self._whitelist.add(tokens)
            already = sorted({t.lower() for t in tokens} - set(added))
            lines = []
            if added:
                lines.append(f"Whitelisted: {', '.join(added)}")
                logger.info("Whitelist add by requester=%s: %s", requester_id, added)
            if already:
                lines.append(f"Already whitelisted: {', '.join(already)}")
            self.send_pm(requester_id, "\n".join(lines))
            return
        if action in ("del", "delete", "remove", "rm", "hapus"):
            if not tokens:
                self.send_pm(requester_id, "Format: /abw del <number or entry>[,...]")
                return
            items, words, problems = self._resolve_list_numbers(
                self._whitelist_snapshots.get(requester_id), tokens, "/abw"
            )
            targets = list(items) + [w.lower() for w in words]
            removed = self._whitelist.remove(targets)
            missing = sorted(set(targets) - set(removed))
            lines = []
            if removed:
                lines.append(f"Removed from whitelist: {', '.join(removed)}")
                logger.info("Whitelist removal by requester=%s: %s", requester_id, removed)
            if missing:
                lines.append(f"Not in the whitelist: {', '.join(missing)}")
            lines.extend(problems)
            self.send_pm(requester_id, "\n".join(lines))
            return
        self.send_pm(
            requester_id,
            "Usage: /abw (list) | /abw add <username or IP> | /abw del <number or entry>",
        )

    def _handle_temp_ban(
        self, requester_id: int, names_csv: str, minutes: int, reason: str = ""
    ):
        """Ban users by nickname for ``minutes``; the bot lifts the ban itself."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can issue temporary bans.")
            return
        targets = self._find_users_by_nicknames(names_csv)
        if not targets:
            self.send_pm(requester_id, "No users match the provided nicknames.")
            return
        reason_msg = str(reason or "").strip()
        delay = 1.0 if reason_msg else 0.05
        by = self._get_username(requester_id) or str(requester_id)
        myid = self.getMyUserID() or 0
        descs = []
        for u in targets:
            if u.nUserID == myid:
                continue
            nickname = from_tt_char(u.szNickname)
            if reason_msg:
                safe_call(
                    self.send_pm,
                    u.nUserID,
                    f"You are temporarily banned for {minutes} minutes: {reason_msg}",
                )
            descs.append(nickname)
            self._schedule_action(
                self._now() + delay,
                self._execute_manual_temp_ban,
                u.nUserID,
                from_tt_char(u.szIPAddress),
                from_tt_char(u.szUsername),
                nickname,
                int(minutes),
                reason_msg,
                requester_id,
                by,
            )
        if descs:
            self.send_pm(
                requester_id, f"Temp ban ({minutes} min) scheduled for: {', '.join(descs)}"
            )
            logger.info(
                "Temp ban batch scheduled (requester=%s, targets=%s, minutes=%s)",
                requester_id,
                descs,
                minutes,
            )
        else:
            self.send_pm(requester_id, "No temp bans were scheduled.")

    def _execute_manual_temp_ban(
        self,
        user_id: int,
        ip: str,
        username: str,
        nickname: str,
        minutes: int,
        reason: str,
        requester_id: int,
        by: str,
    ):
        key = (
            f"{username.strip().lower()}@{ip.strip()}"
            if username and ip
            else self._abuse_key(user_id, ip)
        )
        label = self._apply_temp_ban(
            user_id,
            ip,
            username,
            minutes,
            reason or "manual temp ban",
            key,
            "manual",
            who=nickname,
            by=by,
            requester=requester_id,
        )
        if not label:
            safe_call(self.send_pm, requester_id, f"Failed to temp ban {nickname}.")
            return
        # Disconnect the user once the ban is in place
        self._schedule_action(
            self._now() + 0.5,
            self._execute_manual_kick,
            user_id,
            requester_id,
            nickname,
            "",
            False,
        )
