"""BadWordsFilter: matching, wildcards, stretched spellings and the word file."""

import os
import shutil
import tempfile
import unittest

from badwords import DEFAULT_LIST_NAME, BadWordsFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "words.txt")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("anjing\nkont*l\norang gila\ncok\nBabi, monyet\n")
        self.filter = BadWordsFilter()
        self.filter.load_file(self.path)

    def matches(self, text):
        return self.filter.matching_entries(text)

    def test_plain_words(self):
        self.assertEqual(self.matches("dasar ANJING!"), ["anjing"])
        self.assertEqual(self.matches("babi dan monyet"), ["babi", "monyet"])
        self.assertEqual(self.matches("halo semua"), [])

    def test_whole_words_only(self):
        self.assertEqual(self.matches("cokelat enak"), [])

    def test_stretched_spellings(self):
        self.assertEqual(self.matches("anjiiiiing"), ["anjing"])
        self.assertEqual(self.matches("annnnnjing"), ["anjing"])

    def test_double_letters_are_not_collapsed(self):
        self.assertEqual(self.matches("i cook rice"), [])

    def test_wildcards(self):
        self.assertEqual(self.matches("kontol"), ["kont*l"])
        self.assertEqual(self.matches("kont0l"), ["kont*l"])
        self.assertEqual(self.matches("kont*l"), ["kont*l"])

    def test_phrases(self):
        self.assertEqual(self.matches("dasar orang gila"), ["orang gila"])
        self.assertEqual(self.matches("orang yang gila"), [])

    def test_too_broad_wildcards_are_refused(self):
        self.assertTrue(self.filter.pattern_error("a*"))
        self.assertEqual(self.filter.pattern_error("anj*ng"), "")
        self.assertEqual(self.filter.add_words(["a*", "bangsat"]), ["bangsat"])

    def test_add_and_remove_are_saved(self):
        self.assertEqual(self.filter.add_words(["Bangsat", "anjing"]), ["bangsat"])
        self.assertEqual(self.filter.remove_words(["cok", "tidakada"]), ["cok"])
        reloaded = BadWordsFilter()
        reloaded.load_file(self.path)
        self.assertIn("bangsat", reloaded.list_words())
        self.assertNotIn("cok", reloaded.list_words())

    def test_new_list_starts_from_the_default_list(self):
        with open(os.path.join(self.dir, DEFAULT_LIST_NAME), "w", encoding="utf-8") as f:
            f.write("tolol\n")
        fresh = BadWordsFilter()
        fresh.load_file(os.path.join(self.dir, "new_words.txt"))
        self.assertEqual(fresh.list_words(), ["tolol"])


class ShippedListTest(unittest.TestCase):
    def test_every_entry_is_usable(self):
        checker = BadWordsFilter()
        with open(os.path.join(ROOT, "badwords", DEFAULT_LIST_NAME), encoding="utf-8") as f:
            entries = [w.strip().lower() for line in f for w in line.split(",") if w.strip()]
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(entry=entry):
                self.assertEqual(checker.pattern_error(entry), "")


if __name__ == "__main__":
    unittest.main()
