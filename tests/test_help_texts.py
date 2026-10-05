"""Help texts: every topic can be shown."""

import unittest

from help_texts import HELP_TOPICS, get_general_help, get_topic_help


class HelpTest(unittest.TestCase):
    def test_every_topic(self):
        for topic, (title, body) in HELP_TOPICS.items():
            with self.subTest(topic=topic):
                self.assertTrue(title and body.strip())
                self.assertEqual(get_topic_help(topic), f"{title}\n\n{body}")
                self.assertEqual(get_topic_help("/" + topic.upper()), get_topic_help(topic))

    def test_unknown_topic_shows_the_overview(self):
        self.assertEqual(get_topic_help("tidakada"), get_general_help())
        self.assertEqual(get_topic_help(None), get_general_help())
        self.assertIn("@ai", get_general_help())


if __name__ == "__main__":
    unittest.main()
