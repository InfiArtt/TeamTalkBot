# Automatic deployment

Work happens on branches. A pull request into `main` runs the **Check** job
(every Python file must compile). When the pull request is merged, the
**Deploy to the server** job connects to the server over SSH, and
`tools/deploy.sh` there pulls the new `main`, restarts the bot and checks
that it is running. Both jobs live in `.github/workflows/deploy.yml`; their
results are in the repository's **Actions** tab.

The SSH key that GitHub uses can only run `tools/deploy.sh` on the server:
even if it leaked, all it could do is update and restart the bot.

Until the secrets below are set, the deploy job is skipped with a warning.

## One-time setup on the server

Run these as the account that owns the checkout and runs the bot (not root).
They assume the checkout is `~/TeamTalkBot` and the systemd service is called
`TeamTalkBot` (see `TeamTalkBot.service`).

### 1. Bring the checkout up to date once by hand

`badwords/words.txt` (the admins' `/bwa` and `/bwd` edits) is no longer
tracked by git; the shipped list is `badwords/default_words.txt`. Keep your
current list while pulling this change:

```bash
cd ~/TeamTalkBot
cp badwords/words.txt /tmp/words.txt.bak
git checkout -- badwords/words.txt
git pull --ff-only
cp /tmp/words.txt.bak badwords/words.txt
```

From then on `tools/deploy.sh` does this by itself.

### 2. Let the bot account restart the service without a password

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart TeamTalkBot" | sudo tee /etc/sudoers.d/teamtalkbot
sudo chmod 440 /etc/sudoers.d/teamtalkbot
sudo visudo -cf /etc/sudoers.d/teamtalkbot
```

Check it: `sudo -n systemctl restart TeamTalkBot` must work without asking
for a password.

No account is created here. `$USER` is your own account (the one that owns
`~/TeamTalkBot`; this is also `DEPLOY_USER` below), `TeamTalkBot` is the
systemd service name and must match `TeamTalkBot.service` exactly, and
`teamtalkbot` is only the name of the rule file.

### 3. Create the deploy key

```bash
ssh-keygen -t ed25519 -N "" -C "github-actions deploy" -f ~/teamtalkbot_deploy
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "command=\"$HOME/TeamTalkBot/tools/deploy.sh\",restrict $(cat ~/teamtalkbot_deploy.pub)" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

`command=...` makes the key run only `tools/deploy.sh`; `restrict` disables
port forwarding, terminals and the like.

### 4. Get the server's host key

GitHub checks the server's identity against this. Run it from another
computer, with the address and SSH port GitHub will use:

```bash
ssh-keyscan -p 22 your.server.example
```

On Windows use Git Bash: the `ssh-keyscan` that ships with Windows is too old
for current Ubuntu servers and fails with
`choose_kex: unsupported KEX method sntrup761x25519-sha512@openssh.com`.
Keep all the lines that do not start with `#`. To make sure they really are
your server's keys, compare the `ED25519` fingerprint from
`ssh-keyscan -p 22 your.server.example | ssh-keygen -lf -` with the one the
server prints for `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.

## One-time setup on GitHub

1. **Settings → Environments → New environment**, name it `production`.
   Under *Deployment branches and tags* choose *Selected branches and tags*
   and add `main`, so only `main` can use these secrets.
2. In that environment add these **secrets**:

   | Secret | Value |
   | --- | --- |
   | `DEPLOY_HOST` | server address, e.g. `tt.example.org` |
   | `DEPLOY_USER` | the bot account on the server |
   | `DEPLOY_PORT` | SSH port (optional, default 22) |
   | `DEPLOY_SSH_KEY` | the whole content of `~/teamtalkbot_deploy` (the private key) |
   | `DEPLOY_KNOWN_HOSTS` | the output of `ssh-keyscan` from step 4 |

   To get the private key into `DEPLOY_SSH_KEY`, either run
   `cat ~/teamtalkbot_deploy` on the server and paste everything from
   `-----BEGIN OPENSSH PRIVATE KEY-----` to `-----END OPENSSH PRIVATE KEY-----`
   (both lines included), or, from a computer where the GitHub CLI is logged in:

   ```bash
   scp -P 22 youruser@your.server.example:teamtalkbot_deploy .
   gh secret set DEPLOY_SSH_KEY --env production --repo InfiArtt/TeamTalkBot < teamtalkbot_deploy
   rm teamtalkbot_deploy
   ```
3. Delete the private key from the server once it is stored on GitHub:
   `rm ~/teamtalkbot_deploy` (keep `~/teamtalkbot_deploy.pub` if you like).
4. Test it: **Actions → Check and deploy → Run workflow** on `main`.

## Day-to-day

1. Create a branch, commit, push, open a pull request into `main`.
2. Wait for **Check** to pass, then merge.
3. The bot restarts with the new code within a minute or two; the run's log
   shows `Deployed <commit>; TeamTalkBot is running.`

Do not edit code directly on the server: the update only fast-forwards, so
local commits or edits there make the deploy fail (runtime files such as
`config.json`, `*.dat`, `badwords/words.txt` and the SDK are git-ignored and
are never touched).

## Troubleshooting

| Message in the Actions log | Fix |
| --- | --- |
| `Deploy secrets are not set up yet` | Add the secrets to the `production` environment. |
| `Host key verification failed` | `DEPLOY_KNOWN_HOSTS` does not match the server; rerun `ssh-keyscan`. |
| `Permission denied (publickey)` | The public key is missing from `~/.ssh/authorized_keys`, or `DEPLOY_USER` is wrong. |
| `sudo: a password is required` | The sudoers rule from step 2 is missing or names another account/command. |
| `Update failed; the server checkout may have local commits or edits` | Run `git status` on the server and undo the local changes. |
| `TeamTalkBot is not running after the restart` | Look at `journalctl -u TeamTalkBot` on the server. |
