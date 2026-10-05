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
   `tahi`, `telanjang`, `seks`, `gay`, ...), the bot waits 8 seconds and then
   sends the message to the AI **together with the conversation around it**,
   so a lone *"anjing"* right after *"aku punya binatang baru waaa"* is
   understood as the new pet. It asks for "insult" or "ok" in the background:
   - insult: counted like any badword (warning, kick, ban as configured)
   - ok: not counted
   - no answer (AI down, slow, or too many questions at once): **not
     counted** — better to miss one curse than warn someone for talking
     about their dog
4. Every decision is logged: `journalctl -u TeamTalkBot | grep "AI review"`.

The bot never waits for the AI; it keeps running even if Cloudflare is down.

## Privacy

The AI is only asked about a message that the filter already flagged and
whose matches are all ambiguous. With it go up to `AI_CONTEXT_MESSAGES`
(default 4) earlier messages from the last 2 minutes and up to 2 messages
that follow within 8 seconds, labelled only `[same person]` or
`[someone else]`:

- in a channel: messages of everyone in that channel;
- in private messages: only the sender's own messages (the TeamTalk SDK does
  not tell the bot who a private message was for).

No nickname, username or IP address is sent. Recent messages are kept in
memory only while the AI check is on (never written to disk or logs), and
never include commands or answers to the bot's prompts such as passwords.
Set `AI_CONTEXT_MESSAGES` to 0 to send only the flagged message. Private
messages are included, so mention the check in the server rules.

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

   It sends 38 labelled sentences (some with earlier messages as context) and prints the mistakes, e.g.
   `Correct: 36/38 | insults missed: 1 | false alarms: 1`. Try another model
   with `--model <id>` if the result is poor.
4. Restart the bot (`sudo systemctl restart TeamTalkBot`) so it reads the
   new keys, then switch the check on from TeamTalk: `/abt ai on`.
5. Try it: `/bwt anjing tetanggaku suka berisik kalau malam` answers with the
   matched word, then the AI's verdict a moment later.

Switch it off again any time with `/abt ai off`; ambiguous words are then
counted immediately, as before.

## Ask the AI in a channel (@ai)

Anyone can start a channel message with `@ai` followed by a question:

```
@ai apa itu fotosintesis?
```

The bot answers **in that channel** (as an admin it does not need to join
it), so everyone there hears the answer:

```
[AI] untuk Siti: Fotosintesis adalah ... 
AI bisa salah. Periksa kembali info penting.
```

- Answers are short plain text in the language of the question, with no
  Markdown or emoji, because screen readers read symbols aloud.
- The AI remembers the last `AI_CHAT_HISTORY` (default 3) questions and
  answers of that channel from the last 10 minutes, so follow-ups such as
  `@ai jelasin lebih simpel` work. Nicknames are not sent to the AI.
- `@ai` must be followed by a space, `:` or `,`: `@aisyah halo` is not a
  question. Private messages starting with `@ai` are not answered.
- Limits: one question per person every `AI_CHAT_COOLDOWN_SEC` (default 20)
  seconds and `AI_CHAT_DAILY_LIMIT` (default 200) questions per day for the
  whole server; people who hit a limit are told privately.
- Guest accounts (`AI_CHAT_BLOCKED_USERS`, default `tamu`, `hadirin`) cannot
  use `@ai`; people on those accounts are told privately.
- An answer that contains a clear badword is replaced by a short refusal.
- The question itself is still an ordinary channel message: the badword
  filter and spam detection apply to it as usual. A question that gets a
  badword warning is **not answered**. With the context check on
  (`/abt ai on`), a question with only ambiguous words (`@ai anjing`) is
  judged by the AI first, which is told it is a question to the assistant:
  asking about a word is fine and gets an answer, insulting someone in the
  question is counted and gets none. With the check off such a question is
  counted and not answered, like any other badword.
- In classroom channels the server may refuse the bot's answer unless the
  bot is allowed to write there.

Setup: the same Cloudflare keys as above, then `/abt aichat on` (off by
default). Other settings: `AI_CHAT_PREFIX` (default `@ai`), `AI_CHAT_MODEL`
(empty: same as `AI_MODEL`), `AI_CHAT_MAX_TOKENS` (default 300),
`AI_CHAT_BLOCKED_USERS` (default `["tamu", "hadirin"]`),
`AI_CHAT_BLOCKED_MESSAGE` and `AI_CHAT_DISCLAIMER` (the line after every
answer; empty to leave it out).
Users can read `/help ai`.
