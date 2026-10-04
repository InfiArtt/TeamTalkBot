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
        "- Send /cancel at any time to abort the wizard. It also ends by itself after 3 minutes without a reply.\n\n"
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
        "- Send /cancel to abort the wizard. It also ends by itself after 3 minutes without a reply.\n\n"
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
        "- Non-admins: may only delete their own account.\n"
        "- Shared accounts (e.g. murid, tamu, hadirin, osis) cannot be deleted via the bot.\n\n"
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
    "bw": (
        "Badword Menu (Admin)",
        "Manage badwords step by step: the bot offers\n"
        "  1 List, 2 Search, 3 Add, 4 Delete, 5 Test, 6 On/Off (message filter), 0 Done\n"
        "and asks for what it needs. Reply with the number. Send /cancel (or 0) to close; "
        "any other command also closes the menu. The menu closes by itself after 3 minutes "
        "without a reply.\n"
        "Format:\n"
        "  /bw\n",
    ),
    "bwl": (
        "Badword List (Admin)",
        "Show the badwords as a numbered list, optionally only entries containing some text. "
        "The numbers can be used with /bwd.\n"
        "Format:\n"
        "  /bwl [search]\n"
        "Examples:\n"
        "  /bwl\n"
        "  /bwl anj\n",
    ),
    "bwt": (
        "Test Badwords (Admin)",
        "Check which entries would flag a message, e.g. before adding a wildcard entry "
        "or to see why someone was warned. Nobody is warned by this test.\n"
        "Format:\n"
        "  /bwt <text>\n"
        "Example:\n"
        "  /bwt beli anting emas\n",
    ),
    "bwa": (
        "Add Badwords (Admin)",
        "Append one or more badwords to the active list without restarting the bot.\n"
        "Wildcards: * matches any characters inside one word (including symbols), "
        "? matches exactly one character. A wildcard entry needs at least 3 letters "
        "or digits besides * and ?.\n"
        "Stretched spellings are caught automatically: anjing also catches anjiiiing and annnnjing "
        "(a letter typed 3 or more times in a row).\n"
        "Format:\n"
        "  /bwa <word[,word2,...]>\n"
        "Examples:\n"
        "  /bwa anjing\n"
        "  /bwa anjing,goblok,setan\n"
        "  /bwa anj*ng   (anjing, anjeng, anjiing, anj*ng, ...)\n"
        "  /bwa anjing*  (also anjingku, anjingnya)\n",
    ),
    "bwd": (
        "Delete Badwords (Admin)",
        "Remove one or more badwords, by the numbers from your last /bwl list or by the word itself. "
        "Numbers keep pointing at the list you heard, even after deleting some entries. "
        "Type words exactly as /bwl shows them (e.g. /bwd anj*ng).\n"
        "Format:\n"
        "  /bwd <number|word[,number2|word2,...]>\n"
        "Examples:\n"
        "  /bwd 3\n"
        "  /bwd 3,5\n"
        "  /bwd 3-5\n"
        "  /bwd anjing,babi\n",
    ),
    "ab": (
        "Auto-moderation Menu (Admin)",
        "Manage the automatic warnings, kicks and temp bans step by step: the bot offers\n"
        "  1 Status, 2 Forgive, 3 Whitelist, 4 Temp ban, 5 Features, 0 Done\n"
        "and asks for what it needs. Send /cancel (or 0) to close; any other command also "
        "closes the menu. It closes by itself after 3 minutes without a reply.\n"
        "Format:\n"
        "  /ab\n",
    ),
    "abt": (
        "Feature Switches (Admin)",
        "Switch automatic moderation features on or off without restarting:\n"
        "  1 login    login/logout spam detection\n"
        "  2 join     channel join/leave spam detection\n"
        "  3 spam     message spam detection\n"
        "  4 badwords badword filter for messages\n"
        "  5 profile  badword check of nicknames and status\n"
        "  6 pm       checking private messages between users\n"
        "Defaults come from config.json; a switch changed here is remembered across restarts. "
        "Switching a feature off also clears its current warnings and cancels kicks/bans that "
        "were about to happen.\n"
        "Format:\n"
        "  /abt                              show the switches\n"
        "  /abt <number|name> [on|off]       without on/off it flips the switch\n"
        "Examples:\n"
        "  /abt join off\n"
        "  /abt 2\n",
    ),
    "abs": (
        "Auto-moderation Status (Admin)",
        "Numbered list of everyone who currently has a warning (kind, stage, time until it "
        "clears) and every active temp ban (reason, who issued it, time left). The numbers "
        "can be used with /abf.\n"
        "Format:\n"
        "  /abs\n",
    ),
    "abf": (
        "Forgive (Admin)",
        "Clear someone's warnings and cancel a kick or ban that is about to happen; for a "
        "temp ban, lift it right away. Pick by the numbers from /abs, or by username or IP.\n"
        "Format:\n"
        "  /abf <number|username|IP[,...]>\n"
        "Examples:\n"
        "  /abf 2\n"
        "  /abf 1-3\n"
        "  /abf budi\n"
        "  /abf 203.0.113.10\n",
    ),
    "abw": (
        "Auto-moderation Whitelist (Admin)",
        "Usernames or IP addresses that the automatic moderation never warns, kicks or bans "
        "(spam, join, login and badword checks). Admins can still kick or ban them by hand. "
        "Prefer usernames: whitelisting a shared IP (e.g. a school network) exempts everyone "
        "behind it.\n"
        "Format:\n"
        "  /abw                      show the numbered list\n"
        "  /abw add <username|IP>[,...]\n"
        "  /abw del <number|entry>[,...]\n"
        "Examples:\n"
        "  /abw add pakguru\n"
        "  /abw del 2\n",
    ),
    "tb": (
        "Temporary Ban (Admin)",
        "Ban users by nickname for a number of minutes (1 to 10080); the bot lifts the ban by "
        "itself, even after a restart. Uses BAN_TARGET like /bn. With a reason, the user is "
        "told why before being disconnected. Lift early with /abf.\n"
        "Format:\n"
        "  /tb <nickname[,nickname2,...]> <minutes>[|reason]\n"
        "Examples:\n"
        "  /tb budi 30\n"
        "  /tb budi,andi 60|spam PM\n",
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
        "Developers: Rexya Muhamad Rizki and Rafli | Contact: rexya2017@gmail.com | Website: https://infiartt.com",
        "",
        "Command Overview:",
        "/ru   Register a new user (see /help ru)",
        "/rc   Start the channel creation wizard (see /help rc)",
        "/lc   List channels (see /help lc)",
        "/oc   Check channel owner (see /help oc)",
        "/bw   Badword menu (admin) | /bwl list | /bwa add | /bwd delete | /bwt test",
        "/v    Show bot version, runtime OS, and TeamTalk SDK info",
        "",
        "Admin Commands:",
        "/dc, /du           Delete channel/user (see /help dc, /help du)",
        "/so, /to           Manage channel ownership (see /help so, /help to)",
        "/kc, /bn, /ubn     Kick/Ban/Unban users (/help kc, /help bn, /help ubn)",
        "/lb, /lu           Ban & account reports (/help lb, /help lu)",
        "/ab                Auto-moderation menu: /abs status, /abf forgive, /abw whitelist, /tb temp ban",
        "/abt               Switch moderation features on/off (/help abt)",
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
