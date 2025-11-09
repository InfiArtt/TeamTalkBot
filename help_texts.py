"""Static help text definitions for in-bot /help responses."""

from typing import Optional


HELP_TOPICS = {
    "ru": (
        "Register User",
        "Create a new account through an interactive wizard.\n"
        "Format:\n"
        "  /ru [desired_username]\n\n"
        "Flow:\n"
        "- The bot asks for the username (or uses the one you supplied) and checks availability with the server before moving on.\n"
        "- After the username is approved, the bot prompts for password (8-30 chars, must include uppercase, lowercase, and symbol) and your full name.\n"
        "- Send /cancel at any time to abort the wizard.\n\n"
        "Example:\n"
        "  /ru alex\n",
    ),
    "v": (
        "Bot Version",
        "Display bot build information, operating system details, Python version, and TeamTalk SDK path/version.\n"
        "Format:\n"
        "  /v\n",
    ),
    "ubn": (
        "Unban (Admin)",
        "Remove bans according to BAN_TARGET (USERNAME or IPADDR).\n"
        "Format:\n"
        "  /ubn <value[,value2,...]>\n\n"
        "Notes:\n"
        "- If BAN_TARGET=USERNAME then each value is a username.\n"
        "- If BAN_TARGET=IPADDR then each value is an IP address.\n",
    ),
    "lb": (
        "List Bans (Admin)",
        "Show all active bans on the server.\n"
        "Format:\n"
        "  /lb\n\n"
        "Output includes username/IP, channel path (if any), and ban time.\n",
    ),
    "lu": (
        "List Users (Admin)",
        "Show registered user accounts on the server.\n"
        "Format:\n"
        "  /lu\n\n"
        "Output includes username, account type (admin/default), and last-login info if available.\n",
    ),
    "kc": (
        "Kick User (Admin)",
        "Disconnect users by nickname.\n"
        "Format:\n"
        "  /kc <nick[,nick2,...]>[|reason]\n\n"
        "Notes:\n"
        "- Nicknames are comma-separated; at least one is required.\n"
        "- Optional reason after '|'; leave empty to skip.\n"
        "- The bot sends a warning if abuse warnings are enabled before the kick executes.\n",
    ),
    "bn": (
        "Ban User (Admin)",
        "Ban users by nickname.\n"
        "Format:\n"
        "  /bn <nick[,nick2,...]>[|reason]\n\n"
        "Notes:\n"
        "- Nicknames are comma-separated; at least one is required.\n"
        "- Optional reason after '|'; leave empty to skip.\n"
        "- Ban target follows BAN_TARGET (USERNAME or IPADDR) and the bot auto-kicks after banning.\n",
    ),
    "rc": (
        "Create Channel",
        "Create a new channel under the configured parent path.\n"
        "Format:\n"
        "  /rc <channel_name>\n\n"
        "Interactive flow:\n"
        "- The bot asks for channel password (use '-' for none), topic, and operator password.\n"
        "- You choose whether to keep the configured channel defaults (max users, disk quota, flags) or override a subset (max users, no interruptions, classroom, operator receive only, no VOX, no recording).\n"
        "- Audio is always Opus. You can reuse the configured audio defaults or answer a short questionnaire (application, sample rate, mono/stereo, bitrate, VBR, ignore silence/DTX, transmit interval, frame size, fixed audio volume).\n"
        "- Reply with y/n or the menu numbers shown in each step.\n"
        "- Send /cancel to abort the wizard.\n\n"
        "Notes:\n"
        "- Channel names must start with a letter or number (no leading symbols) and may not include '/' or '\\\\'.\n"
        "- Channels are always created underneath CREATE_CHANNEL_PARENT_PATH (if set) or otherwise in the root. Users cannot override the parent path.\n"
        "- The bot reports if the configured parent path cannot be found.\n\n"
        "Examples:\n"
        "  /rc ChilloutRoom|secret\n"
        "  /rc Lembur_Kuring|\n",
    ),
    "dc": (
        "Delete Channel",
        "Delete a channel by name or path.\n"
        "Format:\n"
        "  /dc <name OR full_path> [--force]\n\n"
        "Confirmation:\n"
        "- Without --force, the bot asks for 'y' or 'n' confirmation.\n\n"
        "Permissions:\n"
        "- Admins: can delete any channel.\n"
        "- Non-admins: can only delete their own channels (tracked by the bot).\n\n"
        "Examples:\n"
        "  /dc Public/English/ChilloutRoom\n"
        "  /dc ChilloutRoom --force\n",
    ),
    "du": (
        "Delete User",
        "Delete a user account.\n"
        "Format:\n"
        "  /du <username> [--force]\n\n"
        "Confirmation:\n"
        "- Without --force the bot asks for 'y' or 'n'.\n\n"
        "Permissions:\n"
        "- Admins: may delete any username.\n"
        "- Non-admins: may only delete their own account.\n\n"
        "Examples:\n"
        "  /du alex\n"
        "  /du alex --force\n",
    ),
    "lc": (
        "List Channels",
        "Show the channel list.\n"
        "Format:\n"
        "  /lc [path]\n\n"
        "Examples:\n"
        "  /lc\n"
        "  /lc Public/English\n",
    ),
    "bwl": (
        "Badword List (Admin)",
        "Show every badword currently loaded by the bot.\n" "Format:\n" "  /bwl\n",
    ),
    "bwa": (
        "Add Badwords (Admin)",
        "Append one or more badwords to the active list without restarting the bot.\n"
        "Format:\n"
        "  /bwa <word[,word2,...]>\n"
        "Examples:\n"
        "  /bwa anjing\n"
        "  /bwa anjing,goblok,setan\n",
    ),
    "bwd": (
        "Delete Badwords (Admin)",
        "Remove one or more badwords from the active list.\n"
        "Format:\n"
        "  /bwd <word[,word2,...]>\n"
        "Examples:\n"
        "  /bwd anjing\n"
        "  /bwd anjing,babi,bangsat\n",
    ),
    "oc": (
        "Check Channel Owner",
        "Show the cached owner of a channel recorded by the bot.\n"
        "Format:\n"
        "  /oc <name OR full_path>\n\n"
        "Example:\n"
        "  /oc Public/English/ChilloutRoom\n",
    ),
    "so": (
        "Set Channel Owner (Admin)",
        "Assign a channel owner manually (admin only).\n"
        "Format:\n"
        "  /so <name OR full_path>|<username>\n\n"
        "Example:\n"
        "  /so Public/English/ChilloutRoom|alex\n",
    ),
    "to": (
        "Transfer Channel Owner",
        "Move channel ownership to another user.\n"
        "Format:\n"
        "  /to <name OR full_path>|<username>\n\n"
        "Permissions:\n"
        "- Admins or the current owner only.\n\n"
        "Example:\n"
        "  /to Public/English/ChilloutRoom|jane\n",
    ),
    "cs": (
        "Change Bot Status (Admin)",
        "Update the bot's status message (admin only).\n"
        "Format:\n"
        "  /cs <status message>\n\n"
        "Example:\n"
        "  /cs Send /help for assistance.\n",
    ),
}


def get_general_help() -> str:
    """Return the general help overview text."""
    lines = [
        "Hello! I'm the TeamTalk administration bot that helps with channel creation, user registration, badword moderation, and other admin tasks.",
        "Developer: Rexya Muhamad Rizki | Contact: rexya2017@gmail.com | Website: https://infiartt.ccom",
        "",
        "Command Overview:",
        "/ru   Register a new user (see /help ru)",
        "/rc   Start the channel creation wizard (see /help rc)",
        "/lc   List channels (see /help lc)",
        "/oc   Check channel owner (see /help oc)",
        "/bwl  Show badwords (admin) | /bwa add | /bwd delete",
        "/v    Show bot version, runtime OS, and TeamTalk SDK info",
        "",
        "Admin Commands:",
        "/dc, /du           Delete channel/user (see /help dc, /help du)",
        "/so, /to           Manage channel ownership (see /help so, /help to)",
        "/kc, /bn, /ubn     Kick/Ban/Unban users (/help kc, /help bn, /help ubn)",
        "/lb, /lu           Ban & account reports (/help lb, /help lu)",
        "/cs                Change bot status (/help cs)",
        "",
        "Use /help <code> for detailed instructions. Example: /help ru",
    ]
    return "\n".join(lines)


def get_topic_help(topic: Optional[str]) -> str:
    """Return help content for ``topic`` or the general overview."""
    if not topic:
        return get_general_help()
    key = topic.strip().lower()
    if key.startswith("/"):
        key = key[1:]
    info = HELP_TOPICS.get(key)
    if not info:
        return get_general_help()
    title, body = info
    return f"{title}\n\n{body}"
