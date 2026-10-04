#!/usr/bin/env python3
"""Measure how well the AI tells insults from ordinary uses of ambiguous badwords.

Sends a fixed set of labelled sentences to the model configured in config.json
(AI_CLOUDFLARE_ACCOUNT_ID, AI_CLOUDFLARE_API_TOKEN, AI_MODEL) and prints the
mistakes and the accuracy. Run it on the server before switching the feature
on with /abt ai on, and again after changing AI_MODEL.

Usage:
  python3 tools/ai_eval.py
  python3 tools/ai_eval.py --model @cf/google/gemma-4-26b-a4b-it
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from ai_review import AIReviewer, INSULT, OK  # noqa: E402

# (message, flagged word, expected verdict); kept apart from the prompt's examples
CASES = [
    ("kemarin aku digigit anjing waktu pulang sekolah", "anjing", OK),
    ("anjing pemandu bisa bantu teman tunanetra jalan", "anjing", OK),
    ("aku alergi bulu anjing", "anjing", OK),
    ("tetangga sebelah melihara tiga ekor anjing", "anjing", OK),
    ("babi hutan sering masuk kebun kakek", "babi", OK),
    ("di pasar ada yang jual sate babi", "babi", OK),
    ("monyet di kebun binatang lucu banget", "monyet", OK),
    ("asu dalam bahasa jawa artinya anjing", "asu", OK),
    ("kampret itu sebutan buat kelelawar kecil", "kampret*", OK),
    ("jangan injak tahi ayam di depan rumah", "tahi", OK),
    ("suara penyanyinya edan, keren banget", "edan", OK),
    ("planet itu bisa dilihat dengan mata telanjang", "telanjang", OK),
    ("pendidikan seks penting buat remaja", "seks", OK),
    ("di pelajaran biologi kita bahas ejakulasi dan menstruasi", "ejakulasi", OK),
    ("situs porno harus diblokir di jaringan sekolah", "porno", OK),
    ("temanku gay dan dia orang baik", "gay", OK),
    ("he came out as gay last year", "gay", OK),
    ("anjing lu, bohong terus", "anjing", INSULT),
    ("anjiiing, kalah lagi main game", "anjing", INSULT),
    ("dasar anjing tidak tahu diri", "anjing", INSULT),
    ("muka lu kayak monyet", "monyet", INSULT),
    ("babi lu, makan terus kerjaannya", "babi", INSULT),
    ("asu tenan kowe", "asu", INSULT),
    ("kampret, siapa yang matiin lampu", "kampret*", INSULT),
    ("tahi lu, ngomong doang", "tahi", INSULT),
    ("dasar edan, kerjaan gak pernah beres", "edan", INSULT),
    ("bangke lu, nyusahin aja", "bangke", INSULT),
    ("lu kayak kunyuk", "kunyuk", INSULT),
    ("kirim foto telanjang dong", "telanjang", INSULT),
    ("mau seks sama aku ga", "seks", INSULT),
    ("gay lu, cupu banget", "gay", INSULT),
    ("you are so gay, loser", "gay", INSULT),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", help="Workers AI model to test (default: AI_MODEL)")
    args = parser.parse_args()

    reviewer = AIReviewer(
        getattr(config, "AI_CLOUDFLARE_ACCOUNT_ID", ""),
        getattr(config, "AI_CLOUDFLARE_API_TOKEN", ""),
        args.model or getattr(config, "AI_MODEL", ""),
        float(getattr(config, "AI_TIMEOUT_SEC", 10) or 10),
    )
    if not reviewer.configured:
        sys.exit("Set AI_CLOUDFLARE_ACCOUNT_ID and AI_CLOUDFLARE_API_TOKEN in config.json first.")
    print(f"Model: {reviewer.model}\n")

    correct = missed = false_alarms = errors = 0
    times = []
    for text, word, expected in CASES:
        start = time.monotonic()
        try:
            verdict = reviewer.classify(text, [word])
        except Exception as exc:
            errors += 1
            print(f"ERROR  {text!r}: {exc}")
            continue
        times.append(time.monotonic() - start)
        if verdict == expected:
            correct += 1
            continue
        if expected == INSULT:
            missed += 1
            print(f"MISSED insult      {text!r} (AI said ok)")
        else:
            false_alarms += 1
            print(f"FALSE alarm        {text!r} (AI said insult)")

    answered = len(CASES) - errors
    print(
        f"\nCorrect: {correct}/{len(CASES)}"
        f" | insults missed: {missed} | false alarms: {false_alarms} | errors: {errors}"
    )
    if times:
        print(f"Average answer time: {sum(times) / len(times):.1f}s over {answered} answers")


if __name__ == "__main__":
    main()
