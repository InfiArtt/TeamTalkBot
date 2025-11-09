"""Entrypoint for launching the TeamTalk bot application."""

import logging
import os
import sys
import time

from tt_compat import assign_tt_char_array


def _prepare_teamtalk_dll_path() -> None:
    """Ensure the TeamTalk DLL directory is available on the runtime path."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dll_dir = os.path.join(base_dir, "TeamTalk_DLL")
    if os.name == "nt":
        try:
            # Python 3.8+ Windows secure DLL loading
            os.add_dll_directory(dll_dir)  # type: ignore[attr-defined]
        except Exception:
            pass
        os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")


def _as_bool(value) -> bool:
    """Normalize configuration values to boolean."""
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
    return bool(value)


def _configure_logging(cfg) -> None:
    """Initialize logging based on the provided configuration module."""
    level_raw = getattr(cfg, "LOG_LEVEL", "INFO")
    if isinstance(level_raw, str):
        level = logging.getLevelName(level_raw.upper())
        if not isinstance(level, int):
            level = logging.INFO
    elif isinstance(level_raw, int):
        level = level_raw
    else:
        level = logging.INFO
    kwargs = {}
    fmt = getattr(cfg, "LOG_FORMAT", "") or ""
    datefmt = getattr(cfg, "LOG_DATE_FORMAT", "") or ""
    if fmt:
        kwargs["format"] = fmt
    if datefmt:
        kwargs["datefmt"] = datefmt
    logging.basicConfig(level=level, **kwargs)


def main() -> int:
    """Application entrypoint used by the CLI launcher."""
    _prepare_teamtalk_dll_path()

    # Import after the DLL path is prepared
    import config

    _configure_logging(config)
    logger = logging.getLogger("teamtalkbot.main")
    if getattr(config, "CONFIG_CREATED", False):
        logger.warning("Generated default config.json. Please review and rerun the bot.")
        print(
            "A new config.json has been created with default values.\n"
            "Edit that file to match your TeamTalk server settings, "
            "then run the bot again."
        )
        return 1

    from tt_client import BotClient

    bot = BotClient()

    encryption_enabled = _as_bool(getattr(config, "SERVER_ENCRYPTION_ENABLED", False))
    logger.info("Encryption enabled: %s", encryption_enabled)
    if encryption_enabled:
        from TeamTalkPy.TeamTalk5 import EncryptionContext

        ctx = EncryptionContext()
        assign_tt_char_array(
            (ctx, "szCertificateFile"),
            getattr(config, "TLS_CERTIFICATE_FILE", "") or "",
        )
        assign_tt_char_array(
            (ctx, "szPrivateKeyFile"), getattr(config, "TLS_PRIVATE_KEY_FILE", "") or ""
        )
        assign_tt_char_array(
            (ctx, "szCAFile"), getattr(config, "TLS_CA_FILE", "") or ""
        )
        assign_tt_char_array((ctx, "szCADir"), getattr(config, "TLS_CA_DIR", "") or "")
        ctx.bVerifyPeer = _as_bool(getattr(config, "TLS_VERIFY_SERVER", False))
        ctx.bVerifyClientOnce = _as_bool(
            getattr(config, "TLS_VERIFY_CLIENT_ONCE", False)
        )
        try:
            ctx.nVerifyDepth = int(getattr(config, "TLS_VERIFY_DEPTH", 0) or 0)
        except Exception:
            ctx.nVerifyDepth = 0

        if not bot.setEncryptionContext(ctx):
            logger.error("Failed to configure the TeamTalk encryption context.")
            return 1

    # Connect to server
    logger.info(
        "Connecting to %s:%s (UDP %s)",
        config.SERVER_HOST,
        config.TCP_PORT,
        config.UDP_PORT,
    )
    ok = bot.connect(
        config.SERVER_HOST,
        config.TCP_PORT,
        config.UDP_PORT,
        0,
        0,
        encryption_enabled,
    )
    if not ok:
        logger.error("Failed to initiate connection to the TeamTalk server.")
        return 1

    # Event loop
    try:
        while True:
            wait_ms = int(getattr(config, "EVENT_LOOP_WAIT_MS", 500) or 500)
            bot.runEventLoop(wait_ms)
            # process delayed tasks (e.g., delayed kicks after warnings)
            try:
                bot.process_scheduled()
            except Exception:
                pass
            # Short sleep to avoid maxing out CPU
            sleep_sec = float(getattr(config, "EVENT_LOOP_SLEEP_SEC", 0.05) or 0.05)
            time.sleep(sleep_sec)
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        try:
            bot.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.getLogger("teamtalkbot.main").critical(
            "Fatal error: %s", exc, exc_info=True
        )
        raise
