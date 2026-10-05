"""Answer "@ai <question>" channel messages with Cloudflare Workers AI.

Like ai_review, the HTTP call runs on a background thread; answers come back
through a queue and are posted to the channel from the main thread.
Supports function calling to execute bot commands safely on the main thread.
"""

import json
import logging
import queue
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from ai_review import API_URL, DEFAULT_MODEL, call_workers_ai

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an AI assistant for a TeamTalk server run by a school for blind students in Indonesia. Users type questions in a channel, and your answers are read aloud by screen readers to everyone in that channel.
Your Persona & Tone:
Adopt a natural, casual, and friendly Gen Z vibe. When speaking Indonesian, use "aku" and "kamu" (NEVER use "lu" and "gua").
Don't sound stiff or robotic. Act like a supportive, chill friend chatting with the students.
Answer in the language of the question (usually Indonesian).
Formatting Rules (CRITICAL):
Plain text ONLY.
STRICTLY NO Markdown (no asterisks for bold, no dashes/bullet points, no hashtags), no tables, and no emojis.
Do not include links unless specifically asked.
Remember: screen readers read punctuation and symbols aloud, so keep your text clean, flowing, and conversational.
Response Guidelines:
Keep it brief: about 4 sentences maximum, unless someone explicitly asks for more details.
You cannot hear the voice chat or see anything. You only have access to the typed text and the channel's chat history.
If you are not sure about something, just be honest and say so instead of guessing.
Briefly and firmly refuse anything sexual, hateful, dangerous, or meant to hurt someone, and never insult anyone.

Server Tools & Function Calling:
You have tools to perform actions on the TeamTalk server:
- kick_user: Kick a user by nickname (sensitive, admin only).
- ban_user: Ban a user permanently by nickname (sensitive, admin only).
- temp_ban_user: Ban a user temporarily for X minutes (sensitive, admin only).
- unban_user: Unban a user by username or IP (sensitive, admin only).
- move_user: Move a user to another channel (sensitive, admin only).
- change_bot_status: Change the bot's status message (sensitive, admin only).
- toggle_moderation_feature: Turn on or turn off moderation features like word filter / badwords, spam/antispam, login, join, profile, pm, ai review, aichat, or all moderation features at once (sensitive, admin only).
- get_moderation_status: Check status of server moderation features (sensitive, admin only).
- forgive_user: Forgive/clear warnings and temp bans for a user or all users (sensitive, admin only).
- add_badword: Add words to the word filter / badword list (sensitive, admin only).
- delete_badword: Remove words from the word filter / badword list (sensitive, admin only).
- list_badwords: View or search words in the word filter (sensitive, admin only).
- list_bans: View active temporary bans (sensitive, admin only).
- list_online_users: List online users and their channels.
- list_channels: List server channels.
- find_user: Find where a user is located and their status.
- check_channel_owner: Check who owns a channel.
- change_bot_nickname: Change the bot's nickname using SDK doChangeNickname (sensitive, admin only).
- join_channel: Make the bot join a specific channel using SDK doJoinChannelByID (sensitive, admin only).
- leave_channel: Make the bot leave the current channel and return to root/lobi (sensitive, admin only).
- set_channel_operator: Grant or revoke channel operator status ChanOp using SDK doChannelOpEx (sensitive, admin only).
- get_channel_info: View technical channel details like topic and max users from SDK getChannel.
- get_user_info: View user profile details from SDK getUser.
- broadcast_message: Broadcast an announcement to everyone on the server using SDK (sensitive, admin only).
- get_bot_info: Get info about the bot and its features.

Handling Commands & Actions:
1. When a user asks you to perform an action or command:
   - If there is a matching tool in your tools list, ALWAYS invoke that tool.
   - If the user asks you to perform an action or command that is NOT available in your tools list (contoh: memutar musik, memesan makanan, restart server atau komputer, atau perintah apapun yang tidak ada di daftar fungsi), JANGAN mengarang atau berpura-pura melaksanakannya. Katakan secara ramah dan santai khas Gen Z bahwa fungsinya belum ada (contoh: "Wah kayaknya belum ada deh function buat itu di bot ini", atau "Fitur itu belum tersedia nih").
2. If a sensitive function returns PERMISSION_DENIED because the requester is not an admin, firmly and casually refuse in your Gen Z vibe (for example: "Woi kamu bukan admin bro, gak boleh aneh-aneh ya").
3. If an action succeeds, confirm it casually in plain text without Markdown or emojis."""

# Characters screen readers read aloud from Markdown formatting
_MARKDOWN = re.compile(r"[*#`_~]{1,3}")
# Emojis screen readers read aloud; strip them
_EMOJI = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\ud83c-\ud83e][\ud000-\udfff]"
)


def clean_answer(text: str) -> str:
    """Strip Markdown symbols, emojis, and extra blank lines from a model answer."""
    lines = []
    for line in str(text or "").splitlines():
        line = _MARKDOWN.sub("", line).strip()
        line = _EMOJI.sub("", line).strip()
        line = re.sub(r"^[-•]\s+", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


TOOLS = [
    {
        "name": "kick_user",
        "description": "Kick a user from the TeamTalk server by nickname. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user to kick.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for kicking the user.",
                },
            },
            "required": ["nickname"],
        },
    },
    {
        "name": "ban_user",
        "description": "Ban a user permanently from the TeamTalk server by nickname. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user to ban.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for banning the user.",
                },
            },
            "required": ["nickname"],
        },
    },
    {
        "name": "temp_ban_user",
        "description": "Temporarily ban a user for a specific duration in minutes. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user to temporarily ban.",
                },
                "minutes": {
                    "type": "integer",
                    "description": "Duration of the ban in minutes (e.g. 5, 10, 60).",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for the temporary ban.",
                },
            },
            "required": ["nickname", "minutes"],
        },
    },
    {
        "name": "unban_user",
        "description": "Unban a user by username or IP address. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "The username or IP address to unban.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "move_user",
        "description": "Move a user to a different channel by nickname and channel name/path. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user to move.",
                },
                "channel": {
                    "type": "string",
                    "description": "The name or path of the destination channel.",
                },
            },
            "required": ["nickname", "channel"],
        },
    },
    {
        "name": "change_bot_status",
        "description": "Change the bot's status message. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "The new status message.",
                },
            },
            "required": ["status"],
        },
    },
    {
        "name": "toggle_moderation_feature",
        "description": "Turn on or turn off moderation features such as word filter / badwords, spam/antispam, login, join, profile, pm, ai review, aichat, or all moderation features at once. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "description": "The feature to toggle: 'badwords' (or 'word_filter'), 'spam' (or 'antispam'), 'login', 'join', 'profile', 'pm', 'ai', 'aichat', or 'all'/'moderation' for all moderation features.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "True to enable/turn on (hidupkan/aktifkan), False to disable/turn off (matikan/nonaktifkan).",
                },
            },
            "required": ["feature", "enabled"],
        },
    },
    {
        "name": "get_moderation_status",
        "description": "Check current ON/OFF status of all server moderation and protection features. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "forgive_user",
        "description": "Clear warnings and lift active temporary bans for a user, nickname, username, IP, or 'all'. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "The nickname, username, IP, or 'all' to forgive.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "add_badword",
        "description": "Add new word(s) or patterns to the word filter / badword list. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "words": {
                    "type": "string",
                    "description": "Word(s) to add, comma-separated if multiple.",
                },
            },
            "required": ["words"],
        },
    },
    {
        "name": "delete_badword",
        "description": "Remove word(s) from the word filter / badword list. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "words": {
                    "type": "string",
                    "description": "Word(s) to remove, comma-separated if multiple.",
                },
            },
            "required": ["words"],
        },
    },
    {
        "name": "list_badwords",
        "description": "List or search entries in the server's word filter / badword list. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional search term to filter words.",
                },
            },
        },
    },
    {
        "name": "list_bans",
        "description": "List active bans and temporary bans on the server. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_online_users",
        "description": "List all users currently connected/online on the server.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_channels",
        "description": "List all channels on the server.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "find_user",
        "description": "Find a user on the server to check which channel they are in and their status message.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user to find.",
                },
            },
            "required": ["nickname"],
        },
    },
    {
        "name": "check_channel_owner",
        "description": "Check who is the registered owner of a channel.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "The channel name or path to check.",
                },
            },
            "required": ["channel"],
        },
    },
    {
        "name": "change_bot_nickname",
        "description": "Change the bot's nickname/display name on the server using SDK doChangeNickname. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The new nickname for the bot.",
                },
            },
            "required": ["nickname"],
        },
    },
    {
        "name": "join_channel",
        "description": "Make the bot join a specific channel on the server using SDK doJoinChannelByID. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "The target channel name or path.",
                },
                "password": {
                    "type": "string",
                    "description": "Optional channel password.",
                },
            },
            "required": ["channel"],
        },
    },
    {
        "name": "leave_channel",
        "description": "Make the bot leave its current channel and return to the root channel using SDK. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "set_channel_operator",
        "description": "Grant or revoke Channel Operator (ChanOp) status for a user in a channel using SDK doChannelOpEx. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user.",
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel name or path (defaults to user's current channel).",
                },
                "is_operator": {
                    "type": "boolean",
                    "description": "True to grant operator status, False to revoke operator status.",
                },
            },
            "required": ["nickname", "is_operator"],
        },
    },
    {
        "name": "get_channel_info",
        "description": "Get detailed channel information and technical settings from SDK getChannel. Available to everyone.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "The channel name or path.",
                },
            },
            "required": ["channel"],
        },
    },
    {
        "name": "get_user_info",
        "description": "Get detailed user info from SDK getUser (account username, channel, status, admin role). Sensitive data like IP only for admins.",
        "parameters": {
            "type": "object",
            "properties": {
                "nickname": {
                    "type": "string",
                    "description": "The nickname of the user.",
                },
            },
            "required": ["nickname"],
        },
    },
    {
        "name": "broadcast_message",
        "description": "Broadcast an announcement message to all users on the server using SDK. Sensitive: requires admin privileges.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The announcement message to broadcast.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "get_bot_info",
        "description": "Get general information about the bot, version, and supported capabilities.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


class ToolRequest:
    """Thread-safe request to execute a tool on the main thread."""

    def __init__(self, name: str, args: dict, context: Any) -> None:
        self.name = name
        self.args = args
        self.context = context
        self.result: Optional[Dict[str, Any]] = None
        self.event = threading.Event()


class AIChat:
    """Ask Workers AI questions on a background thread with function calling support."""

    MAX_PENDING = 5

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
        max_tokens: int = 300,
        api_url: str = API_URL,
    ) -> None:
        self._account_id = str(account_id or "").strip()
        self._api_token = str(api_token or "").strip()
        self.model = str(model or DEFAULT_MODEL).strip()
        self._timeout = float(timeout or 30.0)
        self._max_tokens = int(max_tokens or 300)
        self._api_url = api_url
        self._jobs: "queue.Queue[Tuple[str, list, Any]]" = queue.Queue(self.MAX_PENDING)
        self._results: "queue.Queue[Tuple[Any, Optional[str]]]" = queue.Queue()
        self._tool_requests: "queue.Queue[ToolRequest]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    @property
    def configured(self) -> bool:
        return bool(self._account_id and self._api_token)

    def ask(self, question: str, history: List[Tuple[str, str]], context: Any) -> bool:
        """Queue a question with earlier (question, answer) pairs; False if busy."""
        if not self.configured:
            return False
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="ai-chat", daemon=True)
            self._thread.start()
        try:
            self._jobs.put_nowait((question, list(history), context))
            return True
        except queue.Full:
            return False

    def drain(self) -> List[Tuple[Any, Optional[str]]]:
        """Finished questions as (context, answer or None); call from the main thread."""
        done = []
        while True:
            try:
                done.append(self._results.get_nowait())
            except queue.Empty:
                return done

    def drain_tool_requests(self) -> List[ToolRequest]:
        """Pending tool execution requests; call from the main thread."""
        reqs = []
        while True:
            try:
                reqs.append(self._tool_requests.get_nowait())
            except queue.Empty:
                return reqs

    def request_tool(self, name: str, args: dict, context: Any) -> Dict[str, Any]:
        """Ask the main thread to execute a tool and wait for its result."""
        req = ToolRequest(name, args, context)
        self._tool_requests.put(req)
        if req.event.wait(timeout=5.0):
            return req.result or {}
        return {"status": "error", "error": "TIMEOUT", "message": "Eksekusi fungsi timed out."}

    def _run(self) -> None:
        while True:
            question, history, context = self._jobs.get()
            try:
                answer: Optional[str] = self.answer(question, history, context)
            except Exception as exc:
                logger.warning("AI chat failed: %s", exc)
                answer = None
            self._results.put((context, answer))
            self._jobs.task_done()

    def _extract_tool_calls(self, result: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from Workers AI result payload or generated text."""
        if not isinstance(result, dict):
            return None
        tool_calls = result.get("tool_calls")
        if not tool_calls and isinstance(result.get("response"), dict):
            tool_calls = result["response"].get("tool_calls")
        if not tool_calls and isinstance(result.get("choices"), list) and result["choices"]:
            tool_calls = result["choices"][0].get("message", {}).get("tool_calls")

        if tool_calls and isinstance(tool_calls, list):
            parsed_calls = []
            for call in tool_calls:
                name = call.get("name")
                if not name and isinstance(call.get("function"), dict):
                    name = call["function"].get("name")
                args = call.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if name:
                    parsed_calls.append({"name": name, "arguments": args, "id": call.get("id") or "call_1"})
            if parsed_calls:
                return parsed_calls

        # Fallback: check text for tool call patterns
        raw_text = result.get("response") if isinstance(result, dict) else ""
        known_tools = {t["name"] for t in TOOLS}
        if isinstance(raw_text, str) and (
            any(t in raw_text for t in known_tools)
            or any(kw in raw_text for kw in ('"name"', '"function"', '"tool"', '"action"'))
        ):
            decoder = json.JSONDecoder()
            idx = 0
            length = len(raw_text)
            while idx < length:
                start_brace = raw_text.find("{", idx)
                if start_brace == -1:
                    break
                try:
                    obj, end_pos = decoder.raw_decode(raw_text[start_brace:])
                    if isinstance(obj, dict):
                        if "tool_calls" in obj and isinstance(obj["tool_calls"], list):
                            extracted = self._extract_tool_calls(obj)
                            if extracted:
                                return extracted
                        t_name = obj.get("name") or obj.get("function") or obj.get("tool") or obj.get("action")
                        t_args = obj.get("arguments") or obj.get("parameters") or {}
                        if isinstance(t_args, str):
                            try:
                                t_args = json.loads(t_args)
                            except Exception:
                                t_args = {}
                        if t_name:
                            return [{"name": t_name, "arguments": t_args, "id": "call_1"}]
                    idx = start_brace + max(1, end_pos)
                except Exception:
                    idx = start_brace + 1
        return None

    def answer(self, question: str, history: List[Tuple[str, str]], context: Any = None) -> str:
        """Ask the model and return its cleaned answer; handles function calls."""
        messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for earlier_question, earlier_answer in history:
            messages.append({"role": "user", "content": earlier_question})
            messages.append({"role": "assistant", "content": earlier_answer})
        messages.append({"role": "user", "content": question})

        body = {
            "messages": messages,
            "tools": TOOLS,
            "max_tokens": self._max_tokens,
            "temperature": 0.4,
        }
        try:
            payload = call_workers_ai(
                self._api_url,
                self._account_id,
                self._api_token,
                self.model,
                body,
                self._timeout,
            )
        except Exception:
            # Fallback without tools if model/endpoint dislikes tools parameter
            body.pop("tools", None)
            payload = call_workers_ai(
                self._api_url,
                self._account_id,
                self._api_token,
                self.model,
                body,
                self._timeout,
            )

        if not payload.get("success", True):
            raise RuntimeError(f"Workers AI error: {payload.get('errors')}")

        result = payload.get("result") or {}
        tool_calls = self._extract_tool_calls(result)

        if tool_calls:
            call = tool_calls[0]
            tool_name = call["name"]
            tool_args = call.get("arguments", {})
            call_id = call.get("id") or "call_1"

            logger.info("Executing AI tool call: %s(%s)", tool_name, tool_args)
            tool_result = self.request_tool(tool_name, tool_args, context)
            logger.info("AI tool call result for %s: %s", tool_name, tool_result)

            # Try follow-up with tool role
            followup_messages = list(messages)
            followup_messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "name": tool_name,
                        "arguments": tool_args,
                    }
                ],
            })
            followup_messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": call_id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })
            try:
                f_payload = call_workers_ai(
                    self._api_url,
                    self._account_id,
                    self._api_token,
                    self.model,
                    {
                        "messages": followup_messages,
                        "max_tokens": self._max_tokens,
                        "temperature": 0.4,
                    },
                    self._timeout,
                )
                if f_payload.get("success", True):
                    f_res = f_payload.get("result") or {}
                    final_ans = clean_answer(f_res.get("response") if isinstance(f_res, dict) else "")
                    if final_ans:
                        return final_ans
            except Exception as exc:
                logger.warning("Follow-up with role 'tool' failed: %s", exc)

            # Fallback follow-up with user prompt
            try:
                alt_messages = list(messages)
                alt_messages.append({
                    "role": "assistant",
                    "content": f"Aku akan menjalankan fungsi {tool_name}.",
                })
                alt_messages.append({
                    "role": "user",
                    "content": f"[Hasil eksekusi {tool_name}]: {json.dumps(tool_result, ensure_ascii=False)}. Berikan respon santai dan ramah khas Gen Z dalam bahasa Indonesia ke user.",
                })
                alt_payload = call_workers_ai(
                    self._api_url,
                    self._account_id,
                    self._api_token,
                    self.model,
                    {
                        "messages": alt_messages,
                        "max_tokens": self._max_tokens,
                        "temperature": 0.4,
                    },
                    self._timeout,
                )
                if alt_payload.get("success", True):
                    alt_res = alt_payload.get("result") or {}
                    final_ans = clean_answer(alt_res.get("response") if isinstance(alt_res, dict) else "")
                    if final_ans:
                        return final_ans
            except Exception as exc:
                logger.warning("Alternative follow-up failed: %s", exc)

            # Direct fallback if second LLM turn unavailable
            if tool_result.get("error") == "PERMISSION_DENIED":
                return "Woi kamu bukan admin bro, gak boleh aneh-aneh ya."
            if tool_result.get("error") == "UNKNOWN_TOOL":
                return "Wah kayaknya belum ada deh function buat itu di bot ini."
            if tool_result.get("status") == "success":
                return tool_result.get("message") or "Udah beres aku lakuin ya."
            return tool_result.get("message") or "Maaf, perintah tadi gagal diproses."

        text = clean_answer(result.get("response") if isinstance(result, dict) else "")
        if not text:
            raise ValueError("empty answer from the model")
        return text
