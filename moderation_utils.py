"""Utility helpers for detecting badwords in text or profiles."""

import config
from TeamTalkPy.TeamTalk5 import UserType
from tt_compat import from_tt_char


def check_text_badwords(client, textmessage):
    """Inspect a text message and escalate to the bot's violation handler."""
    try:
        from_uid = textmessage.nFromUserID
        if from_uid == (client.getMyUserID() or 0):
            return
        content = from_tt_char(textmessage.szMessage)
        if not content:
            return
        if getattr(config, "BADWORDS_IGNORE_ADMINS", True):
            try:
                u = client.getUser(from_uid)
                if u.uUserType & UserType.USERTYPE_ADMIN:
                    return
            except Exception:
                pass
        if client._badwords.contains(content):
            ip = client._get_user_ip(from_uid)
            client.handle_badword_violation(from_uid, ip, "text")
    except Exception:
        pass


def check_user_profile_badwords(client, user):
    """Inspect profile fields for badwords."""
    try:
        if user.nUserID == (client.getMyUserID() or 0):
            return
        if getattr(config, "BADWORDS_IGNORE_ADMINS", True) and (
            user.uUserType & UserType.USERTYPE_ADMIN
        ):
            return
        fields = [from_tt_char(user.szNickname), from_tt_char(user.szStatusMsg)]
        text = " ".join(fields)
        if client._badwords.contains(text):
            ip = from_tt_char(user.szIPAddress)
            client.handle_badword_violation(user.nUserID, ip, "profile")
    except Exception:
        pass
