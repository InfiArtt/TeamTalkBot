"""Interactive workflow for creating TeamTalk channels."""

import logging
from typing import Any, Dict, List, Tuple, Optional

from TeamTalkPy.TeamTalk5 import (
    Channel,
    ChannelType,
    AudioCodec,
    OpusCodec,
    Codec,
    OPUS_APPLICATION_VOIP,
    OPUS_APPLICATION_AUDIO,
)
import config
from tt_compat import assign_tt_char_array


logger = logging.getLogger(__name__)

BITRATE_CHOICES_KBPS = [16, 32, 64, 128, 256, 320]
SAMPLE_RATE_CHOICES = [8000, 12000, 16000, 24000, 48000]
TX_INTERVAL_CHOICES = list(range(20, 501, 20))
FRAME_SIZE_CHOICES = list(range(20, 121, 20))
AUDIO_APPLICATION_CHOICES: List[Tuple[str, str, int]] = [
    ("voip", "VoIP", OPUS_APPLICATION_VOIP),
    ("music", "Music", OPUS_APPLICATION_AUDIO),
]
AUDIO_CHANNEL_CHOICES: List[Tuple[str, str, int]] = [
    ("mono", "Mono", 1),
    ("stereo", "Stereo", 2),
]

_FALLBACK_CHANNEL_DEFAULTS = {
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

_FALLBACK_AUDIO_DEFAULTS = {
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


def _config_bool(value, default=False):
    """Return a boolean interpretation of ``value`` with ``default`` fallback."""
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


def _coerce_int(value: Any, default: int, minimum: int = 0) -> int:
    """Cast ``value`` to int while enforcing ``minimum``."""
    try:
        result = int(value)
    except Exception:
        result = default
    if result < minimum:
        result = minimum
    return result


def _coerce_step(value: Any, *, default: int, valid_values: List[int]) -> int:
    """Coerce ``value`` to an int constrained to ``valid_values``."""
    try:
        candidate = int(value)
    except Exception:
        candidate = default
    if candidate not in valid_values:
        candidate = default
    return candidate


def _channel_defaults() -> Dict[str, Any]:
    """Return normalized channel-default settings from config."""
    raw = getattr(config, "CHANNEL_DEFAULTS", {}) or {}
    result = {}
    result["max_users"] = _coerce_int(
        raw.get("max_users"), _FALLBACK_CHANNEL_DEFAULTS["max_users"], 0
    )
    result["disk_quota_mb"] = _coerce_int(
        raw.get("disk_quota_mb"), _FALLBACK_CHANNEL_DEFAULTS["disk_quota_mb"], 0
    )
    for key in [
        "permanent",
        "hidden",
        "no_interruptions",
        "classroom",
        "operator_receive_only",
        "no_voice_activation",
        "no_recording",
    ]:
        result[key] = _config_bool(raw.get(key), _FALLBACK_CHANNEL_DEFAULTS[key])
    return result


def _audio_defaults() -> Dict[str, Any]:
    """Return normalized audio-default settings from config."""
    raw = getattr(config, "AUDIO_DEFAULTS", {}) or {}
    result: Dict[str, Any] = {}
    app = (
        str(raw.get("application", _FALLBACK_AUDIO_DEFAULTS["application"]) or "")
        .strip()
        .lower()
    )
    if app not in {"voip", "music"}:
        app = _FALLBACK_AUDIO_DEFAULTS["application"]
    result["application"] = app

    sample_rate = _coerce_step(
        raw.get("sample_rate"),
        default=_FALLBACK_AUDIO_DEFAULTS["sample_rate"],
        valid_values=SAMPLE_RATE_CHOICES,
    )
    result["sample_rate"] = sample_rate

    channels = (
        str(raw.get("channels", _FALLBACK_AUDIO_DEFAULTS["channels"]) or "")
        .strip()
        .lower()
    )
    if channels not in {"mono", "stereo"}:
        channels = _FALLBACK_AUDIO_DEFAULTS["channels"]
    result["channels"] = channels

    bitrate = _coerce_step(
        raw.get("bitrate_kbps"),
        default=_FALLBACK_AUDIO_DEFAULTS["bitrate_kbps"],
        valid_values=BITRATE_CHOICES_KBPS,
    )
    result["bitrate_kbps"] = bitrate

    result["variable_bitrate"] = _config_bool(
        raw.get("variable_bitrate"),
        _FALLBACK_AUDIO_DEFAULTS["variable_bitrate"],
    )
    result["ignore_silence"] = _config_bool(
        raw.get("ignore_silence"),
        _FALLBACK_AUDIO_DEFAULTS["ignore_silence"],
    )
    result["fixed_audio_volume"] = _config_bool(
        raw.get("fixed_audio_volume"),
        _FALLBACK_AUDIO_DEFAULTS["fixed_audio_volume"],
    )

    tx_interval = _coerce_step(
        raw.get("transmit_interval_ms"),
        default=_FALLBACK_AUDIO_DEFAULTS["transmit_interval_ms"],
        valid_values=TX_INTERVAL_CHOICES,
    )
    frame_size = _coerce_step(
        raw.get("frame_size_ms"),
        default=_FALLBACK_AUDIO_DEFAULTS["frame_size_ms"],
        valid_values=FRAME_SIZE_CHOICES,
    )
    result["transmit_interval_ms"] = tx_interval
    result["frame_size_ms"] = frame_size
    return result


def _channel_ttl_config():
    """Read the inactivity timeout definition from config."""
    cfg = getattr(config, "CHANNEL_INACTIVITY_TIMEOUT", {}) or {}
    try:
        value = float(cfg.get("value", 0) or 0)
    except Exception:
        value = 0.0
    unit = str(cfg.get("unit", "days") or "").strip().lower()
    return value, unit


def _channel_ttl_description() -> Optional[str]:
    """Provide a human-friendly TTL description when auto-cleanup is enabled."""
    value, unit = _channel_ttl_config()
    if value <= 0:
        return None
    unit_label = unit if unit else "days"
    singular = unit_label[:-1] if unit_label.endswith("s") else unit_label
    if value == 1:
        label = singular or "day"
    else:
        label = unit_label if unit_label.endswith("s") else f"{unit_label}s"
    return (
        f"Channel remains active for {value:g} {label}. "
        "Owner must enter the channel periodically to prevent auto-deletion."
    )


def _bool_choice_from_text(text: str) -> Any:
    """Interpret yes/no style responses."""
    normalized = (text or "").strip().lower()
    if normalized in {"y", "yes", "1", "true", "on"}:
        return True
    if normalized in {"n", "no", "0", "false", "off"}:
        return False
    return None


def _format_menu(options: List[str]) -> str:
    """Return a string menu representation for the provided ``options``."""
    return "  ".join(f"{idx}) {label}" for idx, label in enumerate(options, start=1))


def _parse_named_choice(text: str, choices: List[Tuple[str, str, Any]]) -> Any:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    if normalized.isdigit():
        idx = int(normalized)
        if 1 <= idx <= len(choices):
            return choices[idx - 1][0]
    for key, label, _ in choices:
        if normalized in {key, label.lower()}:
            return key
    return None


def _parse_numeric_choice(text: str, choices: List[int]) -> Any:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    if normalized.isdigit():
        idx = int(normalized)
        if 1 <= idx <= len(choices):
            return choices[idx - 1]
    try:
        value = int(normalized)
    except ValueError:
        return None
    return value if value in choices else None


def start_wizard(client, requester_id: int, name: str, password: str = ""):
    """Initialize a new creation wizard session for ``requester_id``."""
    if not client._logged_in:
        client.send_pm(
            requester_id,
            "The bot is not logged in as admin yet. Please try again later.",
        )
        logger.warning(
            "Channel wizard denied; bot not logged in (requester=%s, name=%s)",
            requester_id,
            name,
        )
        return
    if not client._can_user_create_channel(requester_id):
        logger.info(
            "Channel wizard denied by creation policy (requester=%s, name=%s)",
            requester_id,
            name,
        )
        return

    final_name = (name or "").strip()
    if not final_name:
        client.send_pm(requester_id, "Channel name must not be empty.")
        logger.warning(
            "Channel wizard denied; empty channel name (requester=%s)", requester_id
        )
        return
    if not final_name[0].isalnum():
        client.send_pm(
            requester_id,
            "Channel name must start with a letter or number (no leading symbols).",
        )
        logger.info(
            "Channel wizard denied; name starts with symbol (requester=%s, name=%s)",
            requester_id,
            name,
        )
        return
    if any(sep in final_name for sep in ("/", "\\")):
        client.send_pm(
            requester_id,
            "Channel path is fixed by the server configuration. Please provide the channel name only.",
        )
        logger.info(
            "Channel wizard denied; path separators in name (requester=%s, name=%s)",
            requester_id,
            name,
        )
        return

    parent_id = client.getRootChannelID()
    cfg_path = getattr(config, "CREATE_CHANNEL_PARENT_PATH", "") or ""
    if cfg_path:
        cid = client.getChannelIDFromPath(cfg_path)
        if cid and cid > 0:
            parent_id = cid
        else:
            client.send_pm(
                requester_id,
                f"Default parent path not found: {cfg_path}. Channel will be created in the root.",
            )
            logger.warning(
                "Channel wizard default parent missing (requester=%s, path=%s)",
                requester_id,
                cfg_path,
            )

    sess = {
        "step": "ask_channel_password",
        "parent_id": parent_id,
        "name": final_name,
        "password": password or "",
        "channel_props": _channel_defaults(),
        "audio_props": _audio_defaults(),
    }
    client._channel_wizards[requester_id] = sess
    client.send_pm(
        requester_id,
        "[Create Channel] Enter channel password (or '-' for none). Send /cancel to abort.",
    )
    logger.debug(
        "Channel wizard started (requester=%s, name=%s, parent=%s)",
        requester_id,
        final_name,
        parent_id,
    )


def handle_response(client, requester_id: int, content: str):
    """Process follow-up responses for an active wizard session."""
    text = (content or "").strip()
    if text.lower() in ("/cancel", "cancel"):
        client._channel_wizards.pop(requester_id, None)
        client.send_pm(requester_id, "Channel creation cancelled.")
        logger.info("Channel wizard cancelled (requester=%s)", requester_id)
        return

    sess = client._channel_wizards.get(requester_id)
    if not sess:
        return

    step = sess.get("step")
    if step == "ask_channel_password":
        sess["password"] = "" if text == "-" else text
        sess["step"] = "ask_topic"
        client.send_pm(
            requester_id, "[Create Channel] Enter a topic (or '-' for none)."
        )
        logger.debug(
            "Channel wizard channel password stored (requester=%s)", requester_id
        )
        return
    if step == "ask_topic":
        sess["topic"] = "" if text == "-" else text
        sess["step"] = "ask_op_password"
        client.send_pm(
            requester_id, "[Create Channel] Enter operator password (or '-' for none)."
        )
        logger.debug("Channel wizard topic set (requester=%s)", requester_id)
        return

    if step == "ask_op_password":
        sess["op_password"] = "" if text == "-" else text
        sess["step"] = "ask_channel_defaults"
        client.send_pm(
            requester_id, "[Create Channel] Use default channel properties? (y/n)"
        )
        logger.debug(
            "Channel wizard operator password stored (requester=%s)", requester_id
        )
        return

    if step == "ask_channel_defaults":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        if choice:
            sess["channel_props"] = _channel_defaults()
            sess["step"] = "ask_audio_defaults"
            client.send_pm(
                requester_id, "[Create Channel] Use default audio properties? (y/n)"
            )
            logger.debug(
                "Channel wizard using default channel properties (requester=%s)",
                requester_id,
            )
            return
        sess["channel_props"] = _channel_defaults()
        sess["step"] = "ask_channel_max_users"
        client.send_pm(
            requester_id, "[Create Channel] Max users? (0 = nobody can join)"
        )
        logger.debug(
            "Channel wizard channel defaults override requested (requester=%s)",
            requester_id,
        )
        return

    if step == "ask_channel_max_users":
        try:
            value = int(text)
        except ValueError:
            client.send_pm(requester_id, "Enter a valid number (0 or higher).")
            return
        if value < 0:
            client.send_pm(requester_id, "Max users cannot be negative.")
            return
        sess["channel_props"]["max_users"] = value
        sess["step"] = "ask_channel_no_interruptions"
        client.send_pm(
            requester_id,
            "[Create Channel] Enable no interruptions (solo transmit)? (y/n)",
        )
        logger.debug("Channel wizard max users=%s (requester=%s)", value, requester_id)
        return

    if step == "ask_channel_no_interruptions":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["channel_props"]["no_interruptions"] = choice
        sess["step"] = "ask_channel_classroom"
        client.send_pm(requester_id, "[Create Channel] Enable classroom mode? (y/n)")
        logger.debug(
            "Channel wizard no_interruptions=%s (requester=%s)", choice, requester_id
        )
        return

    if step == "ask_channel_classroom":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["channel_props"]["classroom"] = choice
        sess["step"] = "ask_channel_operator_recvonly"
        client.send_pm(
            requester_id, "[Create Channel] Enable operator receive only? (y/n)"
        )
        logger.debug("Channel wizard classroom=%s (requester=%s)", choice, requester_id)
        return

    if step == "ask_channel_operator_recvonly":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["channel_props"]["operator_receive_only"] = choice
        sess["step"] = "ask_channel_no_vox"
        client.send_pm(
            requester_id, "[Create Channel] Disable voice activation (no VOX)? (y/n)"
        )
        logger.debug(
            "Channel wizard operator_receive_only=%s (requester=%s)",
            choice,
            requester_id,
        )
        return

    if step == "ask_channel_no_vox":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["channel_props"]["no_voice_activation"] = choice
        sess["step"] = "ask_channel_no_recording"
        client.send_pm(requester_id, "[Create Channel] Disallow audio recording? (y/n)")
        logger.debug(
            "Channel wizard no_voice_activation=%s (requester=%s)", choice, requester_id
        )
        return

    if step == "ask_channel_no_recording":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["channel_props"]["no_recording"] = choice
        sess["step"] = "ask_audio_defaults"
        client.send_pm(
            requester_id, "[Create Channel] Use default audio properties? (y/n)"
        )
        logger.debug(
            "Channel wizard no_recording=%s (requester=%s)", choice, requester_id
        )
        return

    if step == "ask_audio_defaults":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        if choice:
            sess["audio_props"] = _audio_defaults()
            finalize_channel_create(client, requester_id)
            logger.debug(
                "Channel wizard using default audio properties (requester=%s)",
                requester_id,
            )
            return
        sess["audio_props"] = _audio_defaults()
        sess["step"] = "ask_audio_application"
        options = _format_menu([label for _, label, _ in AUDIO_APPLICATION_CHOICES])
        client.send_pm(
            requester_id,
            "[Create Channel] Choose audio application: " + options + " (send number).",
        )
        logger.debug(
            "Channel wizard audio defaults override requested (requester=%s)",
            requester_id,
        )
        return

    if step == "ask_audio_application":
        selection = _parse_named_choice(text, AUDIO_APPLICATION_CHOICES)
        if selection is None:
            client.send_pm(
                requester_id, "Select a valid option number (1-2) or type VoIP/Music."
            )
            return
        sess["audio_props"]["application"] = selection
        sess["step"] = "ask_audio_samplerate"
        options = _format_menu([f"{rate} Hz" for rate in SAMPLE_RATE_CHOICES])
        client.send_pm(
            requester_id,
            "[Create Channel] Choose sample rate: " + options + " (send number).",
        )
        logger.debug(
            "Channel wizard audio application=%s (requester=%s)",
            selection,
            requester_id,
        )
        return

    if step == "ask_audio_samplerate":
        selection = _parse_numeric_choice(text, SAMPLE_RATE_CHOICES)
        if selection is None:
            client.send_pm(requester_id, "Select a valid sample rate option.")
            return
        sess["audio_props"]["sample_rate"] = selection
        sess["step"] = "ask_audio_channels"
        options = _format_menu([label for _, label, _ in AUDIO_CHANNEL_CHOICES])
        client.send_pm(
            requester_id,
            "[Create Channel] Audio channels: " + options + " (send number).",
        )
        logger.debug(
            "Channel wizard sample_rate=%s (requester=%s)", selection, requester_id
        )
        return

    if step == "ask_audio_channels":
        selection = _parse_named_choice(text, AUDIO_CHANNEL_CHOICES)
        if selection is None:
            client.send_pm(requester_id, "Select 1 for Mono or 2 for Stereo.")
            return
        sess["audio_props"]["channels"] = selection
        sess["step"] = "ask_audio_bitrate"
        options = _format_menu([f"{val} Kbps" for val in BITRATE_CHOICES_KBPS])
        client.send_pm(
            requester_id,
            "[Create Channel] Choose bitrate: " + options + " (send number).",
        )
        logger.debug(
            "Channel wizard channels=%s (requester=%s)", selection, requester_id
        )
        return

    if step == "ask_audio_bitrate":
        selection = _parse_numeric_choice(text, BITRATE_CHOICES_KBPS)
        if selection is None:
            client.send_pm(
                requester_id,
                "Select a valid bitrate option (e.g., send 1 for 16 Kbps).",
            )
            return
        sess["audio_props"]["bitrate_kbps"] = selection
        sess["step"] = "ask_audio_vbr"
        client.send_pm(requester_id, "[Create Channel] Enable variable bitrate? (y/n)")
        logger.debug(
            "Channel wizard bitrate=%s Kbps (requester=%s)", selection, requester_id
        )
        return

    if step == "ask_audio_vbr":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["audio_props"]["variable_bitrate"] = choice
        sess["step"] = "ask_audio_ignore_silence"
        client.send_pm(requester_id, "[Create Channel] Ignore silence (DTX)? (y/n)")
        logger.debug(
            "Channel wizard variable_bitrate=%s (requester=%s)", choice, requester_id
        )
        return

    if step == "ask_audio_ignore_silence":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["audio_props"]["ignore_silence"] = choice
        sess["step"] = "ask_audio_tx_interval"
        client.send_pm(
            requester_id,
            "[Create Channel] Transmit interval (ms, multiples of 20 between 20-500).",
        )
        logger.debug(
            "Channel wizard ignore_silence=%s (requester=%s)", choice, requester_id
        )
        return

    if step == "ask_audio_tx_interval":
        try:
            value = int(text)
        except ValueError:
            client.send_pm(
                requester_id, "Enter a number between 20 and 500 (multiples of 20)."
            )
            return
        if value not in TX_INTERVAL_CHOICES:
            client.send_pm(
                requester_id, "Value must be a multiple of 20 between 20 and 500."
            )
            return
        sess["audio_props"]["transmit_interval_ms"] = value
        sess["step"] = "ask_audio_frame_size"
        client.send_pm(
            requester_id,
            "[Create Channel] Frame size (ms, multiples of 20 between 20-120).",
        )
        logger.debug(
            "Channel wizard tx_interval=%s (requester=%s)", value, requester_id
        )
        return

    if step == "ask_audio_frame_size":
        try:
            value = int(text)
        except ValueError:
            client.send_pm(
                requester_id, "Enter a number between 20 and 120 (multiples of 20)."
            )
            return
        if value not in FRAME_SIZE_CHOICES:
            client.send_pm(
                requester_id, "Value must be a multiple of 20 between 20 and 120."
            )
            return
        sess["audio_props"]["frame_size_ms"] = value
        sess["step"] = "ask_audio_fixed_volume"
        client.send_pm(
            requester_id,
            "[Create Channel] Enable fixed audio volume for all users? (y/n)",
        )
        logger.debug("Channel wizard frame_size=%s (requester=%s)", value, requester_id)
        return

    if step == "ask_audio_fixed_volume":
        choice = _bool_choice_from_text(text)
        if choice is None:
            client.send_pm(requester_id, "Please answer with 'y' or 'n'.")
            return
        sess["audio_props"]["fixed_audio_volume"] = choice
        logger.debug(
            "Channel wizard fixed_audio_volume=%s (requester=%s)", choice, requester_id
        )
        finalize_channel_create(client, requester_id)
        return


def finalize_channel_create(client, requester_id: int):
    """Send the TeamTalk request that creates the configured channel."""
    sess = client._channel_wizards.pop(requester_id, None)
    if not sess:
        logger.warning(
            "Channel wizard finalize called without session (requester=%s)",
            requester_id,
        )
        return
    if not client._can_user_create_channel(requester_id):
        client.send_pm(requester_id, "Channel creation policy prevented this request.")
        logger.info(
            "Channel wizard finalize blocked by policy (requester=%s)", requester_id
        )
        return

    chan = Channel()
    chan.nParentID = sess["parent_id"]
    name_value = sess["name"]
    topic_value = sess.get("topic") or ""
    password_value = sess.get("password") or ""
    op_password_value = sess.get("op_password") or ""
    assign_tt_char_array((chan, "szName"), name_value)
    assign_tt_char_array((chan, "szTopic"), topic_value)
    assign_tt_char_array((chan, "szPassword"), password_value)
    chan.bPassword = bool(password_value)
    assign_tt_char_array((chan, "szOpPassword"), op_password_value)

    channel_cfg = sess.get("channel_props") or _channel_defaults()
    audio_cfg = sess.get("audio_props") or _audio_defaults()

    chan.nMaxUsers = int(channel_cfg.get("max_users", 0))
    chan.nDiskQuota = int(channel_cfg.get("disk_quota_mb", 0)) * 1024 * 1024
    chan.nUserData = 0

    flags = 0
    if channel_cfg.get("permanent", True):
        flags |= ChannelType.CHANNEL_PERMANENT
    if channel_cfg.get("hidden"):
        flags |= ChannelType.CHANNEL_HIDDEN
    if channel_cfg.get("no_interruptions"):
        flags |= ChannelType.CHANNEL_SOLO_TRANSMIT
    if channel_cfg.get("classroom"):
        flags |= ChannelType.CHANNEL_CLASSROOM
    if channel_cfg.get("operator_receive_only"):
        flags |= ChannelType.CHANNEL_OPERATOR_RECVONLY
    if channel_cfg.get("no_voice_activation"):
        flags |= ChannelType.CHANNEL_NO_VOICEACTIVATION
    if channel_cfg.get("no_recording"):
        flags |= ChannelType.CHANNEL_NO_RECORDING
    chan.uChannelType = flags

    ac = AudioCodec()
    ac.nCodec = Codec.OPUS_CODEC
    oc = OpusCodec()
    oc.nApplication = (
        OPUS_APPLICATION_AUDIO
        if audio_cfg.get("application") == "music"
        else OPUS_APPLICATION_VOIP
    )
    oc.nSampleRate = int(audio_cfg.get("sample_rate", SAMPLE_RATE_CHOICES[-1]))
    oc.nChannels = 2 if audio_cfg.get("channels") == "stereo" else 1
    oc.nBitRate = int(audio_cfg.get("bitrate_kbps", 64)) * 1000
    oc.bVBR = bool(audio_cfg.get("variable_bitrate", True))
    oc.bDTX = bool(audio_cfg.get("ignore_silence", False))
    oc.bFEC = False
    oc.bVBRConstraint = False
    oc.nTxIntervalMSec = int(audio_cfg.get("transmit_interval_ms", 20))
    oc.nFrameSizeMSec = int(audio_cfg.get("frame_size_ms", 20))
    oc.nComplexity = 10
    ac.u.opus = oc
    chan.audiocodec = ac

    chan.audiocfg.bEnableAGC = bool(audio_cfg.get("fixed_audio_volume", False))
    chan.audiocfg.nGainLevel = 50 if chan.audiocfg.bEnableAGC else 0

    cmdid = client.doMakeChannel(chan)
    if cmdid <= 0:
        client.send_pm(
            requester_id, "Failed to send channel creation request to the server."
        )
        logger.error(
            "Channel creation command not dispatched (requester=%s, name=%s)",
            requester_id,
            name_value,
        )
        return

    try:
        parent_path_actual = str(client.getChannelPath(chan.nParentID)).strip("/")
    except Exception:
        parent_path_actual = ""
    final_path = (
        f"{parent_path_actual}/{name_value}" if parent_path_actual else name_value
    )
    requester_username = client._get_username(requester_id)
    ttl_msg = _channel_ttl_description()
    meta = {
        "kind": "create_channel",
        "channel_name": name_value,
        "ttl_note": ttl_msg,
        "suppress_default_success": True,
    }
    client._track_pending_cmd(
        cmdid, requester_id, f"create channel '{name_value}'", meta=meta
    )
    client._pending_create_owner[cmdid] = {
        "path": final_path,
        "owner": requester_username,
        "created_at": client._now(),
    }
    logger.info(
        "Channel creation command dispatched (requester=%s, path=%s, parent=%s)",
        requester_id,
        final_path,
        chan.nParentID,
    )
