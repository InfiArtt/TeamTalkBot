"""AIChat: what is sent to Workers AI and how answers come back."""

import copy
import time
import unittest
from unittest import mock

import ai_chat
from ai_chat import TOOLS, AIChat, clean_answer


class FakeWorkersAI:
    """Stands in for call_workers_ai: records request bodies, returns replies in order.

    A reply that is an exception is raised instead; the last reply repeats.
    """

    def __init__(self, *replies):
        self.bodies = []
        self.replies = list(replies)

    def __call__(self, api_url, account, token, model, body, timeout):
        self.bodies.append(copy.deepcopy(body))  # answer() edits the body to retry
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return {"success": True, "result": reply}


def asker_line(body):
    system = body["messages"][0]["content"]
    return next(line for line in system.splitlines() if line.startswith("Penanya"))


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.chat = AIChat("account", "token")

    def ask(self, fake, question="halo", history=(), context=None):
        with mock.patch.object(ai_chat, "call_workers_ai", fake):
            return self.chat.answer(question, list(history), context)

    def test_channel_question(self):
        fake = FakeWorkersAI({"response": "Halo semua"})
        context = {"nickname": "Siti", "channel": 17, "channel_name": "/Kelas 7/", "is_pm": False}
        self.assertEqual(self.ask(fake, context=context), "Halo semua")
        self.assertEqual(asker_line(fake.bodies[0]), "Penanya: Siti (berada di channel: /Kelas 7/).")

    def test_pm_question(self):
        fake = FakeWorkersAI({"response": "Halo"})
        context = {"nickname": "Siti", "channel": 17, "channel_name": "/Kelas 7/", "is_pm": True}
        self.ask(fake, context=context)
        line = asker_line(fake.bodies[0])
        self.assertIn("Private Message", line)
        self.assertIn("/Kelas 7/", line)

    def test_context_without_channel_name(self):
        fake = FakeWorkersAI({"response": "Halo"})
        self.ask(fake, context={"nickname": "Siti", "channel": 17})
        self.assertEqual(asker_line(fake.bodies[0]), "Penanya: Siti (berada di channel: channel 17).")

    def test_without_context(self):
        fake = FakeWorkersAI({"response": "Halo"})
        self.assertEqual(self.ask(fake), "Halo")

    def test_history_comes_before_the_question(self):
        fake = FakeWorkersAI({"response": "Lebih simpel: ..."})
        self.ask(fake, "jelasin lebih simpel", [("apa itu fotosintesis?", "Fotosintesis adalah ...")])
        self.assertEqual(
            fake.bodies[0]["messages"][1:],
            [
                {"role": "user", "content": "apa itu fotosintesis?"},
                {"role": "assistant", "content": "Fotosintesis adalah ..."},
                {"role": "user", "content": "jelasin lebih simpel"},
            ],
        )

    def test_answer_is_cleaned(self):
        fake = FakeWorkersAI({"response": "**Halo** semua"})
        self.assertEqual(self.ask(fake), "Halo semua")

    def test_retries_without_tools_when_the_model_rejects_them(self):
        fake = FakeWorkersAI(RuntimeError("tools not supported"), {"response": "Halo"})
        self.assertEqual(self.ask(fake), "Halo")
        self.assertIn("tools", fake.bodies[0])
        self.assertNotIn("tools", fake.bodies[1])

    def test_failed_request_raises(self):
        def failing(*args):
            return {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}

        with self.assertRaises(RuntimeError):
            self.ask(failing)

    def test_tool_call_runs_the_tool_and_returns_the_follow_up(self):
        fake = FakeWorkersAI(
            {"tool_calls": [{"name": "list_channels", "arguments": {}}]},
            {"response": "Ada 3 channel."},
        )
        context = {"nickname": "Guru", "channel": 1, "channel_name": "/", "is_admin": True}
        with mock.patch.object(
            self.chat, "request_tool", return_value={"status": "success", "message": "/, /Kelas 7/, /Musik/"}
        ) as request_tool:
            answer = self.ask(fake, "channel apa saja?", context=context)
        request_tool.assert_called_once_with("list_channels", {}, context)
        self.assertEqual(answer, "Ada 3 channel.")
        self.assertEqual(fake.bodies[1]["messages"][-1]["role"], "tool")


class ExtractToolCallsTest(unittest.TestCase):
    def setUp(self):
        self.extract = AIChat("account", "token")._extract_tool_calls

    def test_native_tool_call_with_string_arguments(self):
        calls = self.extract({"tool_calls": [{"name": "kick_user", "arguments": '{"nickname": "budi"}'}]})
        self.assertEqual(calls, [{"name": "kick_user", "arguments": {"nickname": "budi"}, "id": "call_1"}])

    def test_alias(self):
        calls = self.extract({"tool_calls": [{"name": "pm_user", "arguments": {}}]})
        self.assertEqual(calls[0]["name"], "send_private_message")

    def test_unknown_tool_is_ignored(self):
        self.assertIsNone(self.extract({"tool_calls": [{"name": "format_disk", "arguments": {}}]}))

    def test_tool_call_written_as_text(self):
        calls = self.extract({"response": 'Oke: {"name": "list_channels", "arguments": {}}'})
        self.assertEqual(calls[0]["name"], "list_channels")

    def test_plain_answer_has_no_tool_call(self):
        self.assertIsNone(self.extract({"response": "Fotosintesis adalah proses ..."}))


class BackgroundTest(unittest.TestCase):
    def drain(self, chat, count):
        done, deadline = [], time.time() + 5
        while len(done) < count and time.time() < deadline:
            done += chat.drain()
            time.sleep(0.01)
        return done

    def test_answers_come_back_through_drain(self):
        chat = AIChat("account", "token")
        answers = {"halo": "Halo juga"}

        def answer(question, history, context=None):
            if question not in answers:
                raise RuntimeError("down")
            return answers[question]

        chat.answer = answer
        self.assertTrue(chat.ask("halo", [], {"id": 1}))
        self.assertTrue(chat.ask("gagal", [], {"id": 2}))
        self.assertEqual(sorted(self.drain(chat, 2), key=lambda r: r[0]["id"]),
                         [({"id": 1}, "Halo juga"), ({"id": 2}, None)])

    def test_not_configured(self):
        self.assertFalse(AIChat("", "").ask("halo", [], {}))


class HelpersTest(unittest.TestCase):
    def test_clean_answer(self):
        self.assertEqual(clean_answer("**Fotosintesis** adalah proses `x`"), "Fotosintesis adalah proses x")
        self.assertEqual(clean_answer("Langkah:\n\n- satu\n• dua\n"), "Langkah:\nsatu\ndua")

    def test_tools_are_well_formed(self):
        names = [tool["name"] for tool in TOOLS]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool.get("description"))
                self.assertEqual(tool["parameters"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
