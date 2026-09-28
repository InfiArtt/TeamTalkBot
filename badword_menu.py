"""Interactive /bw menu that walks admins through badword management."""

import logging

logger = logging.getLogger(__name__)

MENU = "[Badwords] 1 List, 2 Search, 3 Add, 4 Delete, 5 Test, 6 On/Off, 0 Done."

_CHOICES = {
    "1": "list",
    "list": "list",
    "lihat": "list",
    "2": "search",
    "search": "search",
    "cari": "search",
    "3": "add",
    "add": "add",
    "tambah": "add",
    "4": "delete",
    "delete": "delete",
    "hapus": "delete",
    "5": "test",
    "test": "test",
    "tes": "test",
    "6": "switch",
    "on": "switch",
    "off": "switch",
    "0": "done",
    "done": "done",
    "selesai": "done",
}

_PROMPTS = {
    "search": "[Badwords] Type part of a word to search for.",
    "add": "[Badwords] Type the words to add, separated by commas. Wildcards * and ? are allowed.",
    "delete": "[Badwords] Type list numbers (e.g. 3, 3,5 or 3-5) or the words to delete.",
    "test": "[Badwords] Type a sentence to test.",
}


def start(client, requester_id: int):
    """Open the menu for ``requester_id`` if they are an admin."""
    if not client._is_admin(requester_id):
        client.send_pm(requester_id, "Only admins can manage badwords.")
        return
    client._badword_menus[requester_id] = {"step": "menu"}
    client.send_pm(requester_id, MENU)
    logger.debug("Badword menu opened (requester=%s)", requester_id)


def close(client, requester_id: int, message: str = "[Badwords] Menu closed."):
    """Close the menu and optionally tell the admin."""
    if client._badword_menus.pop(requester_id, None) is not None and message:
        client.send_pm(requester_id, message)


def handle_response(client, requester_id: int, content: str) -> bool:
    """Handle a menu reply; return False if it should run as a normal command."""
    session = client._badword_menus.get(requester_id)
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
            client.send_pm(requester_id, "Send a number from 0 to 6.\n" + MENU)
        elif choice == "done":
            close(client, requester_id)
        elif choice == "switch":
            # Flips the message filter; nickname checks are under /abt profile
            client._handle_feature_toggle(requester_id, "badwords")
            client.send_pm(requester_id, MENU)
        elif choice == "list":
            client._handle_badword_list(requester_id, "", hint="")
            client.send_pm(requester_id, MENU)
        else:
            if choice == "delete" and requester_id not in client._badword_list_snapshots:
                # Numbers refer to a list, so read one out first
                client._handle_badword_list(requester_id, "", hint="")
            session["step"] = choice
            client.send_pm(requester_id, _PROMPTS[choice])
        return True

    if not text:
        client.send_pm(requester_id, _PROMPTS.get(step, MENU))
        return True
    if step == "search":
        client._handle_badword_list(requester_id, text.lower(), hint="")
    elif step == "add":
        client._handle_badword_add(requester_id, text)
    elif step == "delete":
        client._handle_badword_delete(requester_id, text)
    elif step == "test":
        client._handle_badword_test(requester_id, text)
    session["step"] = "menu"
    client.send_pm(requester_id, MENU)
    return True
