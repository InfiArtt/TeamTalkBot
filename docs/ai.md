# AI context check for ambiguous badwords

Some badwords also have an ordinary meaning: *"anjing tetanggaku suka berisik
kalau malam"* is about a dog, *"anjing lah"* is a curse. A word list cannot
tell them apart, so the bot can ask an AI model on
[Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/).

## How it works

1. The badword filter checks every message as before.
2. If a **clear** badword matches (e.g. `kontol`, `bangsat`), the bot acts
   right away, without the AI.
3. If **only ambiguous** words match (the `AI_AMBIGUOUS_WORDS` list: animals,
   `tahi`, `telanjang`, `seks`, `gay`, ...), the bot sends that one message to
   the AI and waits for "insult" or "ok" in the background:
   - insult: counted like any badword (warning, kick, ban as configured)
   - ok: not counted
   - no answer (AI down, slow, or too many questions at once): **not
     counted** — better to miss one curse than warn someone for talking
     about their dog
4. Every decision is logged: `journalctl -u TeamTalkBot | grep "AI review"`.

The bot never waits for the AI; it keeps running even if Cloudflare is down.

## Privacy

Only the text of a message that the filter already flagged, and only when
every match is ambiguous, is sent to Cloudflare. No nickname, username or IP
address is sent. This includes private messages between users, so mention
it in the server rules.

## Setup

1. In the Cloudflare dashboard create an **API token** with the permissions
   **Workers AI – Read** and **Workers AI – Edit**, and note your
   **Account ID**.
2. On the server, add these keys to `~/TeamTalkBot/config.json` (it is
   git-ignored, so the token never ends up on GitHub):

   ```json
   "AI_CLOUDFLARE_ACCOUNT_ID": "your account id",
   "AI_CLOUDFLARE_API_TOKEN": "your api token",
   ```

   Optional: `AI_MODEL` (default `@cf/meta/llama-3.3-70b-instruct-fp8-fast`),
   `AI_TIMEOUT_SEC` (default 10), `AI_AMBIGUOUS_WORDS` (which badword entries
   go to the AI; use the entries exactly as `/bwl` shows them).
3. Measure the model before using it on students:

   ```bash
   cd ~/TeamTalkBot && python3 tools/ai_eval.py
   ```

   It sends 32 labelled sentences and prints the mistakes, e.g.
   `Correct: 30/32 | insults missed: 1 | false alarms: 1`. Try another model
   with `--model <id>` if the result is poor.
4. Restart the bot (`sudo systemctl restart TeamTalkBot`) so it reads the
   new keys, then switch the check on from TeamTalk: `/abt ai on`.
5. Try it: `/bwt anjing tetanggaku suka berisik kalau malam` answers with the
   matched word, then the AI's verdict a moment later.

Switch it off again any time with `/abt ai off`; ambiguous words are then
counted immediately, as before.
