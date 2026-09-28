"""Interactive /ab menu for the auto-moderation tools (status, forgive, whitelist, temp ban)."""

import logging

from command_handler import parse_private_command

logger = logging.getLogger(__name__)

MENU = "[Auto-moderation] 1 Status, 2 Forgive, 3 Whitelist, 4 Temp ban, 5 Features, 0 Done."

_CHOICES = {
    "1": "status",
    "status": "status",
    "2": "forgive",
    "forgive": "forgive",
    "maafkan": "forgive",
    "3": "whitelist",
    "whitelist": "whitelist",
    "4": "tempban",
    "tempban": "tempban",
    "ban": "tempban",
    "5": "features",
    "features": "features",
    "fitur": "features",
    "0": "done",
    "done": "done",
    "selesai": "done",
}

_PROMPTS = {
    "forgive": "[Auto-moderation] Type status numbers (e.g. 1 or 1-3), usernames or IPs to forgive.",
    "whitelist": "[Auto-moderation] Type 'add <username or IP>' or 'del <number>'.",
    "tempban": "[Auto-moderation] Type <nickname> <minutes>, optionally |reason. Example: budi 30|spam PM",
    "features": "[Auto-moderation] Send a feature number to switch it on or off, or 0 for the main menu.",
}


def start(client, requester_id: int):
    """Open the menu for ``requester_id`` if they are an admin."""
    if not client._is_admin(requester_id):
        client.send_pm(requester_id, "Only admins can use auto-moderation tools.")
        return
    client._abuse_menus[requester_id] = {"step": "menu"}
    client.send_pm(requester_id, MENU)
    logger.debug("Auto-moderation menu opened (requester=%s)", requester_id)


def close(client, requester_id: int, message: str = "[Auto-moderation] Menu closed."):
    """Close the menu and optionally tell the admin."""
    if client._abuse_menus.pop(requester_id, None) is not None and message:
        client.send_pm(requester_id, message)


def handle_response(client, requester_id: int, content: str) -> bool:
    """Handle a menu reply; return False if it should run as a normal command."""
    session = client._abuse_menus.get(requester_id)
    if not session:
        return False
    text = (content or "").strip()
    if text.lower() in ("/cancel", "cancel", "batal"):
        close(client, requester_id)
        return True
    if text.startswith("/"):
        # Any other command leaves the menu and is processed as usual
        close(client, requester_id)
        return False

    step = session.get("step")
    if step == "menu":
        choice = _CHOICES.get(text.lower())
        if choice is None:
            client.send_pm(requester_id, "Send a number from 0 to 5.\n" + MENU)
        elif choice == "done":
            close(client, requester_id)
        elif choice == "status":
            client._handle_abuse_status(requester_id, hint="")
            client.send_pm(requester_id, MENU)
        elif choice == "forgive":
            if requester_id not in client._abuse_status_snapshots:
                # Numbers refer to the status list, so read it out first
                client._handle_abuse_status(requester_id, hint="")
            if client._abuse_status_snapshots.get(requester_id):
                session["step"] = "forgive"
                client.send_pm(requester_id, _PROMPTS["forgive"])
            else:
                client.send_pm(requester_id, MENU)
        else:
            if choice == "whitelist":
                client._handle_abuse_whitelist(requester_id, "list", hint="")
            elif choice == "features":
                client._handle_feature_toggle(requester_id, "", hint="")
            session["step"] = choice
            client.send_pm(requester_id, _PROMPTS[choice])
        return True

    if not text:
        client.send_pm(requester_id, _PROMPTS.get(step, MENU))
        return True
    if step == "features":
        # Stay here so several features can be switched in a row; 0 goes back
        if text in ("0", "back", "kembali"):
            session["step"] = "menu"
            client.send_pm(requester_id, MENU)
        else:
            client._handle_feature_toggle(requester_id, text)
            client.send_pm(requester_id, "Send another number, or 0 for the main menu.")
        return True
    if step == "forgive":
        client._handle_abuse_forgive(requester_id, text)
    elif step == "whitelist":
        client._handle_abuse_whitelist(requester_id, text, hint="")
    elif step == "tempban":
        try:
            parsed = parse_private_command("/tb " + text)
        except ValueError as exc:
            client.send_pm(requester_id, f"{exc}\n{_PROMPTS['tempban']}")
            return True
        _, args = parsed
        client._handle_temp_ban(
            requester_id, args["names"], args["minutes"], args.get("reason", "")
        )
    session["step"] = "menu"
    client.send_pm(requester_id, MENU)
    return True
