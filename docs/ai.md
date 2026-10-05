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

### Natural language commands & function calling

Users can request actions in natural conversation without remembering manual slash commands (for example: `@ai matiin word filter dong`, `@ai matiin moderation`, `@ai kick orang yang namanya kak fian dong`, or `@ai siapa aja yang lagi online?`).

The AI automatically invokes tools executed safely on the bot's main thread:
- `toggle_moderation_feature`: enable or disable moderation features (`word_filter` / `badwords`, `spam`, `login`, `join`, `profile`, `pm`, `ai`, `aichat`, or `all`/`moderation` for all features at once) (sensitive, requires admin).
- `get_moderation_status`: check current ON/OFF status of all moderation features (sensitive, requires admin).
- `kick_user`: kick a user by nickname (sensitive, requires admin).
- `ban_user`: permanently ban a user by nickname (sensitive, requires admin).
- `temp_ban_user`: temporarily ban a user for X minutes (sensitive, requires admin).
- `unban_user`: unban a user by username or IP (sensitive, requires admin).
- `forgive_user`: clear warnings and lift temp bans for a user or all users (sensitive, requires admin).
- `add_badword`: add word(s) to the word filter (sensitive, requires admin).
- `delete_badword`: remove word(s) from the word filter (sensitive, requires admin).
- `list_badwords`: list or search words in the word filter (sensitive, requires admin).
- `list_bans`: view active temporary bans (sensitive, requires admin).
- `move_user`: move a user to another channel (sensitive, requires admin).
- `change_bot_status`: update the bot's status message (sensitive, requires admin).
- `list_online_users`: list online users and their channels (available to everyone).
- `list_channels`: list server channels (available to everyone).
- `find_user`: locate a user and see what channel they are in and their status (available to everyone).
- `check_channel_owner`: check the owner of a channel (available to everyone).
- `change_bot_nickname`: change the bot's nickname/display name on the server using SDK `doChangeNickname` (sensitive, requires admin).
- `join_channel`: make the bot join a specific channel using SDK `doJoinChannelByID` (sensitive, requires admin).
- `leave_channel`: make the bot leave its current channel and return to root using SDK (sensitive, requires admin).
- `set_channel_operator`: grant or revoke Channel Operator (ChanOp) status for a user using SDK `doChannelOpEx` (sensitive, requires admin).
- `get_channel_info`: view technical channel settings (topic, max users, password protection) from SDK `getChannel` (available to everyone).
- `get_user_info`: view user profile details from SDK `getUser` (available to everyone; sensitive fields like IP are admin only).
- `broadcast_message`: broadcast an announcement message across the server using SDK `TextMsgType.MSGTYPE_BROADCAST` (sensitive, requires admin).
- `get_bot_info`: get bot version and supported features (available to everyone).

**Admin permissions**: Sensitive actions strictly check the requester's `uUserType & UserType.USERTYPE_ADMIN`. If a non-admin requests a restricted action (like kicking, moving, or disabling moderation), permission is denied and the AI replies in its friendly Gen Z persona (for example: *"Woi kamu bukan admin bro, gak boleh aneh-aneh ya"*).

**Unrecognized or unsupported commands**: If a user asks the AI to perform a task or action that does not have a corresponding function in the bot (for example: playing songs, ordering food, restarting servers), the AI does not hallucinate or guess; it politely informs the user in its Gen Z persona that the function is not yet available (for example: *"Wah kayaknya belum ada deh function buat itu di bot ini"*).

**Live Server Context & Fuzzy Lookups**:
- **Server snapshot injection**: When answering `@ai`, the bot injects real-time context into the AI's prompt: requester nickname, requester's current channel, online users and their channels, and the server channel list.
- **Self-referential requests**: Requests mentioning "aku", "saya", or omitted nicknames (e.g. `@ai jadiin aku operator channel dong` or `@ai move aku ke channel ekskul dong`) automatically resolve to the requester and their current channel.
- **Fuzzy user lookups**: The bot strips common Indonesian honorifics (`kak`, `bang`, `mas`, `pak`, `bu`, `@`, `bro`) and matches by exact name, stem, tokens (e.g. `kak fian` matches `Kak Fian (Ketua)` or `Fian`), or username.
- **Fuzzy channel lookups**: Strips conversational prepositions and channel terms (e.g. `ke channel ekskul` or `ruang ekskul` cleanly matches `Ekskul Robotik` or `/Root/Ekskul Robotik`). Channel terms like `sini` or `channel ini` resolve to the requester's current channel.

Setup: the same Cloudflare keys as above, then `/abt aichat on` (off by
default). Other settings: `AI_CHAT_PREFIX` (default `@ai`), `AI_CHAT_MODEL`
(empty: same as `AI_MODEL`), `AI_CHAT_MAX_TOKENS` (default 300),
`AI_CHAT_BLOCKED_USERS` (default `["tamu", "hadirin"]`),
`AI_CHAT_BLOCKED_MESSAGE` and `AI_CHAT_DISCLAIMER` (the line after every
answer; empty to leave it out).
Users can read `/help ai`.


