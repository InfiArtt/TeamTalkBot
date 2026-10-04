#!/bin/sh
# Update this checkout to the latest main and restart the bot.
#
# Runs on the server as the bot's user. GitHub Actions calls it over SSH with
# a key that may only run this script (see docs/deploy.md). The checkout must
# not hold code edits; runtime data (config.json, *.dat, badwords/words.txt,
# the TeamTalk SDK) is git-ignored and left alone.
set -eu
cd "$(dirname "$0")/.."

SERVICE="${TEAMTALKBOT_SERVICE:-TeamTalkBot}"

# Checkouts from before badwords/words.txt became git-ignored still track it.
# Keep the admins' /bwa and /bwd edits across the update that untracks it,
# and also when the update fails halfway.
backup="$(mktemp)"
trap 'rm -f "$backup"' EXIT
restore_words() {
    if [ -s "$backup" ]; then
        cp -p "$backup" badwords/words.txt
    fi
}
if [ -f badwords/words.txt ]; then
    cp -p badwords/words.txt "$backup"
    git checkout --quiet -- badwords/words.txt 2>/dev/null || true
fi

if ! git fetch --quiet origin main || ! git merge --ff-only --quiet origin/main; then
    restore_words
    echo "Update failed; the server checkout may have local commits or edits." >&2
    exit 1
fi
if [ ! -f badwords/words.txt ]; then
    restore_words
fi

# -n: fail instead of waiting for a password (see the sudoers rule in docs/deploy.md)
sudo -n systemctl restart "$SERVICE"
sleep 5
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "$SERVICE is not running after the restart; check: journalctl -u $SERVICE" >&2
    exit 1
fi
echo "Deployed $(git rev-parse --short HEAD); $SERVICE is running."
