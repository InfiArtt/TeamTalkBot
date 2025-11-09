"""High-level wrapper around TeamTalk SDK providing bot behavior."""

from ctypes import byref
import logging
import heapq
from typing import Dict, List, Any, Optional
import time

from command_handler import parse_private_command
from help_texts import get_topic_help
from badwords import BadWordsFilter
from abuse_tracker import AbuseTracker
import moderation_utils
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
    buildTextMessage,
    BanType,
    BannedUser,
    Subscription,
    setLicense,
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
        )
        self._temp_ban_minutes = int(
            getattr(config, "ABUSE_TEMP_BAN_MINUTES", 30) or 30
        )
        self._subscription_cache: Dict[int, int] = {}
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

    def getChannelIDFromPath(self, path):
        return super().getChannelIDFromPath(self._tt_value(path))

    def getChannelPath(self, channel_id: int) -> str:
        return from_tt_char(super().getChannelPath(channel_id))

    # Badwords config helpers
    def _bw_enabled(self) -> bool:
        try:
            return bool(getattr(config, "BADWORDS_ENABLED", True))
        except Exception:
            return True

    def _bw_profile_enabled(self) -> bool:
        try:
            return self._bw_enabled() and bool(
                getattr(config, "BADWORDS_PROFILE_CHECK_ENABLED", True)
            )
        except Exception:
            return self._bw_enabled()

    def _bw_types(self):
        try:
            vals = (
                getattr(
                    config,
                    "BADWORDS_INTERCEPT_TYPES",
                    ["PRIVATE", "CHANNEL", "BROADCAST"],
                )
                or []
            )
            return {str(v).strip().upper() for v in vals if str(v).strip()}
        except Exception:
            return {"PRIVATE", "CHANNEL", "BROADCAST"}

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
        self.doLogin(
            config.BOT_NICKNAME,
            config.ADMIN_USERNAME,
            config.ADMIN_PASSWORD,
            config.CLIENT_NAME,
        )
        logger.info(
            "Connected to server; issuing login request as %s", config.ADMIN_USERNAME
        )

    def onConnectFailed(self):
        self._connected = False
        self._queue_reconnect()
        logger.warning("Connection attempt failed; retry scheduled")

    def onConnectionLost(self):
        self._connected = False
        self._logged_in = False
        self._subscription_cache.clear()
        self._queue_reconnect()
        logger.warning("Connection lost; reconnect scheduled")

    def onCmdMyselfLoggedIn(self, userid, useraccount):
        self._logged_in = True
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
        safe_call(
            self.doLogin,
            config.BOT_NICKNAME,
            config.ADMIN_USERNAME,
            config.ADMIN_PASSWORD,
            config.CLIENT_NAME,
        )
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

    def onCmdUserTextMessage(self, textmessage: TextMessage):
        try:
            if textmessage.nMsgType != TextMsgType.MSGTYPE_USER:
                # Filter bad words di pesan channel / broadcast sesuai konfigurasi
                if self._bw_enabled():
                    types = self._bw_types()
                    if textmessage.nMsgType == TextMsgType.MSGTYPE_CHANNEL and (
                        "CHANNEL" in types
                    ):
                        moderation_utils.check_text_badwords(self, textmessage)
                    elif textmessage.nMsgType == TextMsgType.MSGTYPE_BROADCAST and (
                        "BROADCAST" in types
                    ):
                        moderation_utils.check_text_badwords(self, textmessage)
                return
            from_user = textmessage.nFromUserID
            content = from_tt_char(textmessage.szMessage).strip()
            # Handle pending confirmations first
            pending = self._confirmations.get(from_user)
            if pending:
                # expire after 120s
                if self._now() - pending.get("ts", 0) > 120:
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
            parsed = parse_private_command(content)
            if not parsed:
                if from_user in self._registration_wizards:
                    registration_wizard.handle_response(self, from_user, content)
                    return
                # Check if user is in channel creation wizard
                if from_user in self._channel_wizards:
                    channel_wizard.handle_response(self, from_user, content)
                else:
                    # Terapkan filter badwords pada private message sesuai konfigurasi
                    if self._bw_enabled() and ("PRIVATE" in self._bw_types()):
                        moderation_utils.check_text_badwords(self, textmessage)
                    if content:
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
                self._handle_badword_list(from_user)
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

    def _issue_temp_ban(self, userid: int, ip: str, reason: str, key: str, kind: str):
        try:
            mode = str(getattr(config, "BAN_TARGET", "USERNAME")).upper()
            ban_type = (
                BanType.BANTYPE_USERNAME
                if mode == "USERNAME"
                else BanType.BANTYPE_IPADDR
            )
            label = ""
            if ban_type == BanType.BANTYPE_USERNAME:
                label = self._get_username(userid)
                if not label:
                    ban_type = BanType.BANTYPE_IPADDR
                    mode = "IPADDR"
                    label = ip
            else:
                label = ip
            if not label and ban_type == BanType.BANTYPE_IPADDR:
                fallback = self._get_username(userid)
                if fallback:
                    ban_type = BanType.BANTYPE_USERNAME
                    mode = "USERNAME"
                    label = fallback
            if not label:
                return
            cmdid = self.doBanUserEx(userid, ban_type)
            if cmdid <= 0:
                return
            self._track_pending_cmd(
                cmdid, userid, f"temp ban '{label}' ({reason})", notify=False
            )
            unban_at = self._now() + max(1, self._temp_ban_minutes) * 60
            self._schedule_action(unban_at, self._lift_temp_ban, mode, label, key, kind)
            # Ensure user is removed immediately after ban is applied (with slight delay to flush warning)
            self._schedule_action(
                self._now() + 3.0, self._kick_user_for_abuse, userid, reason
            )
            logger.info(
                "Temporary ban issued (user=%s, label=%s, mode=%s, duration=%s min)",
                userid,
                label,
                mode,
                self._temp_ban_minutes,
            )
        except Exception:
            logger.exception("Failed to issue temporary ban for user %s", userid)

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
            self._schedule_action(
                self._now() + 3.0, self._kick_user_for_abuse, userid, reason
            )
        elif stage == 3:
            self._schedule_action(
                self._now() + 3.0, self._issue_temp_ban, userid, ip, reason, key, kind
            )

    def _lift_temp_ban(self, mode: str, label: str, key: str, kind: str):
        try:
            mode = str(mode or "").upper()
            if mode == "IPADDR":
                cmdid = self.doUnBanUser(label, 0)
                if cmdid > 0:
                    self._track_pending_cmd(
                        cmdid, 0, f"auto unban IP '{label}'", notify=False
                    )
                    logger.info("Auto-unban requested for IP %s", label)
            else:
                bu = BannedUser()
                assign_tt_char_array((bu, "szUsername"), label)
                bu.uBanTypes = BanType.BANTYPE_USERNAME
                assign_tt_char_array((bu, "szIPAddress"), "")
                assign_tt_char_array((bu, "szChannelPath"), "")
                assign_tt_char_array((bu, "szNickname"), "")
                assign_tt_char_array((bu, "szOwner"), "")
                cmdid = self.doUnbanUserEx(bu)
                if cmdid > 0:
                    self._track_pending_cmd(
                        cmdid, 0, f"auto unban user '{label}'", notify=False
                    )
                    logger.info("Auto-unban requested for user %s", label)
        except Exception:
            logger.exception("Failed to auto-unban %s (mode=%s)", label, mode)
        finally:
            try:
                self._abuse.reset(kind, key)
            except Exception:
                pass

    def handle_badword_violation(self, userid: int, ip: str, context: str):
        try:
            key = self._abuse_key(userid, ip)
            stage = self._abuse.record("badword", key)
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

    def _handle_abuse_login(self, user: User):
        ip = from_tt_char(user.szIPAddress)
        key = self._abuse_key(user.nUserID, ip)
        stage = self._abuse.record("login", key)
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
        ip = from_tt_char(user.szIPAddress)
        key = self._abuse_key(user.nUserID, ip)
        stage = self._abuse.record("join", key)
        if stage:
            self._handle_abuse_stage("join", stage, user.nUserID, ip, "join abuse", key)
            logger.warning(
                "Join abuse detected (user=%s, ip=%s, stage=%s)",
                user.nUserID,
                ip,
                stage,
            )

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
            types = self._bw_types() if self._bw_enabled() else set()
            # Subscribe to additional types only if configured and feature enabled
            if "CHANNEL" in types:
                subs |= Subscription.SUBSCRIBE_CHANNEL_MSG
                subs |= Subscription.SUBSCRIBE_INTERCEPT_CHANNEL_MSG
            if "BROADCAST" in types:
                subs |= Subscription.SUBSCRIBE_BROADCAST_MSG
            if "PRIVATE" in types:
                subs |= Subscription.SUBSCRIBE_INTERCEPT_USER_MSG
            mask = int(subs)
            if self._subscription_cache.get(user_id) == mask:
                logger.debug("Subscription mask unchanged for user %s", user_id)
                return
            cmdid = self.doSubscribe(user_id, subs)
            if cmdid > 0:
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

    def _perform_delete_channel(self, requester_id: int, path: str):
        # Authorization: admins can delete any channel; non-admins only their own (based on cache)
        requester_username = self._get_username(requester_id)
        is_admin = self._is_admin(requester_id)
        if not is_admin:
            owner = cache_store.get_owner(path)
            if not owner or owner != requester_username:
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

    def _handle_list_channels(self, requester_id: int, base_path: str):
        try:
            chans = self.getServerChannels()
            lines = []
            base = (base_path or "").strip("/")
            for ch in chans:
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

    def _handle_badword_list(self, requester_id: int):
        """Send the full badword list to admins."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can view badwords.")
            return
        words = self._badwords.list_words()
        if not words:
            self.send_pm(requester_id, "Badword list is empty.")
            return
        self._send_chunked_lines(requester_id, words, header="[Badwords]")
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
        added = self._badwords.add_words(words)
        if added:
            self.send_pm(requester_id, f"Added badwords: {', '.join(added)}")
            logger.info("Badwords added by requester=%s: %s", requester_id, added)
        else:
            self.send_pm(requester_id, "No new badwords were added (already present).")

    def _handle_badword_delete(self, requester_id: int, csv_words: str):
        """Remove comma- or space-separated ``csv_words`` from the badword list."""
        if not self._is_admin(requester_id):
            self.send_pm(requester_id, "Only admins can delete badwords.")
            return
        words = self._parse_badword_csv(csv_words)
        if not words:
            self.send_pm(requester_id, "Provide at least one word to remove.")
            return
        removed = self._badwords.remove_words(words)
        if removed:
            self.send_pm(requester_id, f"Removed badwords: {', '.join(removed)}")
            logger.info("Badwords removed by requester=%s: %s", requester_id, removed)
        else:
            self.send_pm(requester_id, "No matching badwords were found.")

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
        mode = str(getattr(config, "BAN_TARGET", "USERNAME")).upper()
        vals = [v.strip() for v in (values_csv or "").split(",") if v.strip()]
        if not vals:
            self.send_pm(requester_id, "At least one value is required.")
            logger.debug("Unban request missing values (requester=%s)", requester_id)
            return
        descs = []
        for v in vals:
            if mode == "IPADDR":
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
