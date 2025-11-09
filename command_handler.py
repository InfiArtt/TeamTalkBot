"""Parsing helpers for slash commands handled by the bot."""

from typing import Optional, Tuple, Dict


Command = Tuple[str, Dict[str, str]]


def parse_private_command(text: str) -> Optional[Command]:
    """Return a parsed ``(action, args)`` tuple for the incoming private command."""
    if not text:
        return None

    text = text.strip()
    if not text.startswith("/"):
        return None

    # Normalize spacing after the command keyword
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Helper for splitting <a>|<b>
    def split_pipe(s: str, require_right=True) -> Tuple[str, str]:
        if "|" in s:
            left, right = s.split("|", 1)
        else:
            left, right = s, ""
        left = left.strip()
        right = right.strip()
        if require_right and not right:
            raise ValueError("Password must not be empty")
        return left, right

    def split_badwords(s: str):
        if not s:
            return []
        return [part.strip() for part in s.replace(",", " ").split() if part.strip()]

    if cmd == "/ru":  # register user (interactive)
        username = (rest or "").strip()
        if "|" in username:
            raise ValueError(
                "Use the new interactive format: /ru <optional username> (no '|')."
            )
        return ("register_user", {"username": username})

    if cmd == "/rc":  # register/create channel
        channel_name = (rest or "").strip()
        if not channel_name:
            raise ValueError("Format: /rc <channel_name>")
        if "|" in channel_name:
            raise ValueError(
                "Channel name must not contain '|'. The password will be requested interactively."
            )
        if any(sep in channel_name for sep in ("/", "\\")):
            raise ValueError(
                "Channel name must not contain '/' or '\\'. Channel path is fixed by configuration."
            )
        if not channel_name[0].isalnum():
            raise ValueError(
                "Channel name must start with a letter or number (no leading symbols)."
            )
        return ("create_channel", {"name": channel_name})

    if cmd == "/dc":  # delete channel
        if not rest:
            raise ValueError("Format: /dc <channel_name OR full_path> [--force]")
        force = False
        tokens = rest.split()
        filtered = []
        for t in tokens:
            if t == "--force":
                force = True
            else:
                filtered.append(t)
        target = " ".join(filtered).strip()
        if not target:
            raise ValueError("Channel target must not be empty")
        return ("delete_channel", {"target": target, "force": force})

    if cmd == "/du":  # delete user account
        if not rest:
            raise ValueError("Format: /du <username> [--force]")
        force = False
        tokens = rest.split()
        filtered = []
        for t in tokens:
            if t == "--force":
                force = True
            else:
                filtered.append(t)
        username = " ".join(filtered).strip()
        if not username:
            raise ValueError("Username must not be empty")
        return ("delete_user", {"username": username, "force": force})

    if cmd == "/lc":  # list channels
        path = rest.strip() if rest else ""
        return ("list_channels", {"path": path})

    if cmd == "/bwl":
        return ("badword_list", {})

    if cmd == "/bwa":
        if not rest:
            raise ValueError("Format: /bwa <word[,word2,...]>")
        words = split_badwords(rest)
        if not words:
            raise ValueError("You must provide at least one word to add.")
        return ("badword_add", {"words": ",".join(words)})

    if cmd == "/bwd":
        if not rest:
            raise ValueError("Format: /bwd <word[,word2,...]>")
        words = split_badwords(rest)
        if not words:
            raise ValueError("You must provide at least one word to remove.")
        return ("badword_delete", {"words": ",".join(words)})

    if cmd == "/v":
        return ("version_info", {})

    if cmd == "/oc":  # owner check
        if not rest:
            raise ValueError("Format: /oc <name OR full_path>")
        target = rest.strip()
        return ("owner_check", {"target": target})

    if cmd == "/so":  # set owner (admin only)
        if not rest:
            raise ValueError("Format: /so <name OR full_path>|<username>")
        if "|" not in rest:
            raise ValueError("You must provide the new username after '|'")
        target, newowner = rest.split("|", 1)
        target = target.strip()
        newowner = newowner.strip()
        if not target or not newowner:
            raise ValueError("Channel target and username must not be empty")
        return ("set_owner", {"target": target, "username": newowner})

    if cmd == "/to":  # transfer owner (admin or current owner)
        if not rest:
            raise ValueError("Format: /to <name OR full_path>|<username>")
        if "|" not in rest:
            raise ValueError("You must provide the new username after '|'")
        target, newowner = rest.split("|", 1)
        target = target.strip()
        newowner = newowner.strip()
        if not target or not newowner:
            raise ValueError("Channel target and username must not be empty")
        return ("transfer_owner", {"target": target, "username": newowner})

    if cmd in ("/help", "/h"):
        topic = rest.strip() if rest else ""
        return ("help", {"topic": topic})

    if cmd == "/cs":  # change bot status (admin only)
        if not rest:
            raise ValueError("Format: /cs <status message>")
        status = rest.strip()
        return ("change_status", {"status": status})

    if cmd == "/kc":  # kick users by nickname(s)
        if not rest:
            raise ValueError("Format: /kc <nickname[,nickname2,...]>[|reason]")
        names_part, reason = (rest.split("|", 1) + [""])[:2]
        names = [n.strip() for n in names_part.split(",") if n.strip()]
        if not names:
            raise ValueError("At least one nickname is required")
        return ("kick_users", {"names": ",".join(names), "reason": reason.strip()})

    if cmd == "/bn":  # ban users by nickname(s)
        if not rest:
            raise ValueError("Format: /bn <nickname[,nickname2,...]>[|reason]")
        names_part, reason = (rest.split("|", 1) + [""])[:2]
        names = [n.strip() for n in names_part.split(",") if n.strip()]
        if not names:
            raise ValueError("At least one nickname is required")
        return ("ban_users", {"names": ",".join(names), "reason": reason.strip()})

    if cmd == "/ubn":  # unban by username/IP depending on config
        if not rest:
            raise ValueError("Format: /ubn <value[,value2,...]>")
        vals = [n.strip() for n in rest.split(",") if n.strip()]
        if not vals:
            raise ValueError("At least one value is required")
        return ("unban", {"values": ",".join(vals)})

    if cmd == "/lb":  # list bans
        return ("list_bans", {})

    if cmd == "/lu":  # list users
        return ("list_users", {})

    return None
