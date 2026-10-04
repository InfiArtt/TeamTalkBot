"""Answer "@ai <question>" channel messages with Cloudflare Workers AI.

Like ai_review, the HTTP call runs on a background thread; answers come back
through a queue and are posted to the channel from the main thread.
"""

import logging
import queue
import re
import threading
from typing import Any, List, Optional, Tuple

from ai_review import API_URL, DEFAULT_MODEL, call_workers_ai

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the assistant of a TeamTalk voice-chat server run by a school for blind students in Indonesia. People type questions to you in a channel, and your answer is read aloud by screen readers to everyone in that channel.

- Answer in the language of the question (usually Indonesian), in a friendly way that suits students.
- Keep it short: about 4 sentences at most, unless someone asks for more detail.
- Plain text only: no Markdown, no symbols for bold or lists, no tables, no emoji, no links unless asked. Screen readers read symbols aloud.
- You cannot hear the voice chat or see anything. You only get the typed question and, if there are any, the earlier questions and your answers in this channel.
- Briefly refuse anything sexual, hateful, dangerous or meant to hurt someone, and never insult anyone.
- If you are not sure about something, say so instead of guessing."""

# Characters screen readers read aloud from Markdown formatting
_MARKDOWN = re.compile(r"[*#`_]{1,3}")


def clean_answer(text: str) -> str:
    """Strip Markdown symbols and extra blank lines from a model answer."""
    lines = []
    for line in str(text or "").splitlines():
        line = _MARKDOWN.sub("", line).strip()
        line = re.sub(r"^[-•]\s+", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


class AIChat:
    """Ask Workers AI questions on a background thread."""

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

    def _run(self) -> None:
        while True:
            question, history, context = self._jobs.get()
            try:
                answer: Optional[str] = self.answer(question, history)
            except Exception as exc:
                logger.warning("AI chat failed: %s", exc)
                answer = None
            self._results.put((context, answer))
            self._jobs.task_done()

    def answer(self, question: str, history: List[Tuple[str, str]]) -> str:
        """Ask the model (blocking) and return its cleaned answer; raises on failure."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for earlier_question, earlier_answer in history:
            messages.append({"role": "user", "content": earlier_question})
            messages.append({"role": "assistant", "content": earlier_answer})
        messages.append({"role": "user", "content": question})
        payload = call_workers_ai(
            self._api_url,
            self._account_id,
            self._api_token,
            self.model,
            {"messages": messages, "max_tokens": self._max_tokens, "temperature": 0.4},
            self._timeout,
        )
        if not payload.get("success", True):
            raise RuntimeError(f"Workers AI error: {payload.get('errors')}")
        result = payload.get("result") or {}
        text = clean_answer(result.get("response") if isinstance(result, dict) else "")
        if not text:
            raise ValueError("empty answer from the model")
        return text
