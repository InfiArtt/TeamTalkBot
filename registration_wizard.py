"""Interactive registration helper for /ru command."""

import logging

logger = logging.getLogger(__name__)


def start(client, requester_id: int, initial_username: str):
    """Kick off the wizard for ``requester_id`` using an optional prefilled username."""
    session = client._registration_wizards.get(requester_id)
    if not session:
        return
    session["step"] = "ask_username"
    if initial_username:
        _handle_username_input(client, requester_id, initial_username, session)
    else:
        client.send_pm(requester_id, "[Register] Enter the username you want to use.")


def handle_response(client, requester_id: int, content: str):
    """Route follow-up responses to the appropriate wizard step."""
    session = client._registration_wizards.get(requester_id)
    if not session:
        return
    text = (content or "").strip()
    if text.lower() in ("/cancel", "cancel"):
        client._cancel_registration_wizard(requester_id, notify=True)
        return
    step = session.get("step")
    if step == "ask_username":
        _handle_username_input(client, requester_id, text, session)
        return
    if step == "await_username_check":
        client.send_pm(
            requester_id, "[Register] Still checking that username; please wait."
        )
        return
    if step == "ask_password":
        _handle_password_input(client, requester_id, text, session)
        return
    if step == "ask_fullname":
        _handle_fullname_input(client, requester_id, text, session)
        return
    if step == "submitting":
        client.send_pm(
            requester_id, "[Register] Registration already in progress. Please wait."
        )
        return
    client.send_pm(
        requester_id, "[Register] Unexpected input. Send /cancel to restart."
    )


def handle_username_check_result(client, requester_id: int, available: bool):
    """Advance the wizard once the asynchronous username check completes."""
    session = client._registration_wizards.get(requester_id)
    if not session or session.get("step") != "await_username_check":
        return
    candidate = session.get("pending_username")
    if available:
        session["username"] = candidate
        session["pending_username"] = None
        session["step"] = "ask_password"
        client.send_pm(
            requester_id,
            "[Register] Username available.\n"
            "Enter a password (8-30 chars, include uppercase, lowercase, and a symbol).",
        )
        logger.debug(
            "Registration wizard username approved (requester=%s, username=%s)",
            requester_id,
            candidate,
        )
    else:
        session["pending_username"] = None
        session["step"] = "ask_username"
        client.send_pm(
            requester_id,
            "[Register] That username is already in use. Please choose another.",
        )
        logger.info(
            "Registration wizard username taken (requester=%s, username=%s)",
            requester_id,
            candidate,
        )


def handle_username_check_error(client, requester_id: int):
    """Reset the wizard if the username lookup failed."""
    session = client._registration_wizards.get(requester_id)
    if not session or session.get("step") != "await_username_check":
        return
    session["pending_username"] = None
    session["step"] = "ask_username"
    client.send_pm(
        requester_id,
        "[Register] Could not verify username availability. Please try again.",
    )
    logger.error(
        "Registration wizard username check failed (requester=%s)", requester_id
    )


def _handle_username_input(client, requester_id: int, text: str, session):
    """Validate username input and trigger availability checks."""
    candidate = (text or "").strip()
    if not candidate:
        client.send_pm(requester_id, "[Register] Username must not be empty.")
        return
    try:
        normalized = client._validate_username(candidate)
    except Exception as exc:
        client.send_pm(requester_id, f"[Register] {exc}")
        return
    if client._begin_username_check(requester_id, normalized):
        session["pending_username"] = normalized
        session["step"] = "await_username_check"
        client.send_pm(
            requester_id, f"[Register] Checking availability for '{normalized}'..."
        )
        logger.debug(
            "Registration wizard checking username (requester=%s, username=%s)",
            requester_id,
            normalized,
        )
    else:
        client.send_pm(
            requester_id,
            "[Register] Unable to initiate username check. Please try again later.",
        )
        logger.error(
            "Registration wizard failed to start username check (requester=%s, username=%s)",
            requester_id,
            normalized,
        )


def _handle_password_input(client, requester_id: int, text: str, session):
    """Validate password strength before moving to the next step."""
    try:
        password = client._validate_password(text)
    except Exception as exc:
        client.send_pm(requester_id, f"[Register] {exc}")
        return
    session["password"] = password
    session["step"] = "ask_fullname"
    client.send_pm(requester_id, "[Register] Enter your full name (1-30 characters).")
    logger.debug("Registration wizard captured password (requester=%s)", requester_id)


def _handle_fullname_input(client, requester_id: int, text: str, session):
    """Capture the full name and submit the accumulated registration payload."""
    try:
        fullname = client._sanitize_fullname(text)
    except Exception as exc:
        client.send_pm(requester_id, f"[Register] {exc}")
        return
    session["fullname"] = fullname
    session["step"] = "submitting"
    logger.debug("Registration wizard captured full name (requester=%s)", requester_id)
    client._complete_registration_wizard(requester_id)
