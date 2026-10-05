"""AIReviewer: judging ambiguous badwords with Workers AI."""

import time
import unittest
from unittest import mock

import ai_review
from ai_review import ERROR, EXAMPLES, INSULT, OK, OTHER, SAME, AIReviewer, format_message, parse_verdict


def reply(response):
    return {"success": True, "result": {"response": response}}


class ParseVerdictTest(unittest.TestCase):
    def test_json_answer(self):
        self.assertEqual(parse_verdict(reply('{"verdict": "insult"}')), INSULT)
        self.assertEqual(parse_verdict(reply('Sure! {"verdict": "ok"}')), OK)

    def test_already_parsed_answer(self):
        self.assertEqual(parse_verdict(reply({"verdict": "ok"})), OK)

    def test_plain_word_answer(self):
        self.assertEqual(parse_verdict(reply("insult")), INSULT)
        self.assertEqual(parse_verdict(reply("OK")), OK)

    def test_unusable_answer_raises(self):
        with self.assertRaises(ValueError):
            parse_verdict(reply("maybe"))

    def test_error_response_raises(self):
        with self.assertRaises(RuntimeError):
            parse_verdict({"success": False, "errors": ["bad token"]})


class FormatMessageTest(unittest.TestCase):
    def test_message_alone(self):
        self.assertEqual(format_message("anjing lah"), "Message to judge:\nanjing lah")

    def test_with_conversation(self):
        text = format_message("anjing", [(SAME, "aku punya binatang baru")], [(OTHER, "lucu")])
        self.assertEqual(
            text.splitlines(),
            [
                "Earlier messages (oldest first):",
                "- [same person] aku punya binatang baru",
                "Message to judge:",
                "anjing",
                "Later messages:",
                "- [someone else] lucu",
            ],
        )

    def test_question_to_the_assistant(self):
        lines = format_message("@ai anjing", to_assistant=True).splitlines()
        self.assertEqual(lines[0], "(This message is a question typed to the AI assistant.)")
        self.assertEqual(lines[1:], ["Message to judge:", "@ai anjing"])

    def test_long_text_is_cut(self):
        self.assertEqual(len(format_message("a" * 1000).splitlines()[1]), ai_review.MAX_TEXT)


class ClassifyTest(unittest.TestCase):
    def test_request(self):
        bodies = []

        def fake(api_url, account, token, model, body, timeout):
            bodies.append(body)
            return reply('{"verdict": "ok"}')

        reviewer = AIReviewer("account", "token")
        with mock.patch.object(ai_review, "call_workers_ai", fake):
            verdict = reviewer.classify("@ai anjing", ["anjing"], [(OTHER, "hewan apa?")], [], True)
        self.assertEqual(verdict, OK)
        messages = bodies[0]["messages"]
        self.assertIn("anjing", messages[0]["content"])
        self.assertEqual(len(messages), 1 + 2 * len(EXAMPLES) + 1)
        self.assertEqual(
            messages[-1]["content"], format_message("@ai anjing", [(OTHER, "hewan apa?")], [], True)
        )
        self.assertEqual(bodies[0]["temperature"], 0)

    def test_examples_are_complete(self):
        for example in EXAMPLES:
            with self.subTest(example=example[1]):
                self.assertEqual(len(example), 5)
                self.assertIn(example[3], (INSULT, OK))


class BackgroundTest(unittest.TestCase):
    def drain(self, reviewer, count):
        done, deadline = [], time.time() + 5
        while len(done) < count and time.time() < deadline:
            done += reviewer.drain()
            time.sleep(0.01)
        return done

    def test_verdicts_come_back_through_drain(self):
        reviewer = AIReviewer("account", "token")

        def classify(text, words, before=(), after=(), to_assistant=False):
            if text == "down":
                raise RuntimeError("timeout")
            return INSULT if to_assistant else OK

        reviewer.classify = classify
        self.assertTrue(reviewer.submit("anjing", ["anjing"], 1))
        self.assertTrue(reviewer.submit("@ai x", ["anjing"], 2, to_assistant=True))
        self.assertTrue(reviewer.submit("down", ["anjing"], 3))
        self.assertEqual(sorted(self.drain(reviewer, 3)), [(1, OK), (2, INSULT), (3, ERROR)])

    def test_not_configured(self):
        reviewer = AIReviewer("", "")
        self.assertFalse(reviewer.configured)
        self.assertFalse(reviewer.submit("anjing", ["anjing"], 1))


if __name__ == "__main__":
    unittest.main()
