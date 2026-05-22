#!/usr/bin/env bash
# Local cron job: top-up LLM summaries for the highest-scoring items, then
# commit + push so the site redeploys. Designed to be run on a schedule.
#
# - Uses whatever summarizer.provider config.yaml specifies (codex by default,
#   which needs the local ChatGPT-extension OAuth — that's why this runs
#   locally, not in CI).
# - If the codex/ChatGPT quota is exhausted, llm_summarize produces no updates
#   and this script pushes nothing. It self-heals on the next run.
# - Idempotent: only items without an "llm" summary are (re)done, so steady
#   state is just the day's new items (~tens), not a big batch.
#
# Install (every 3 hours):
#   crontab -e
#   0 */3 * * * /home/lny/RNEWS/scripts/auto_summarize.sh
# Disable: remove that line from `crontab -e`.
set -u

REPO="/home/lny/RNEWS"
export HOME="${HOME:-/home/lny}"          # cron has a minimal env; codex needs HOME
cd "$REPO" || exit 0

LOG="$REPO/data/auto_summarize.log"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) auto_summarize start ===" >> "$LOG"

# Prevent overlapping runs: draining the backlog can take longer than the cron
# interval, and two runs racing would double-summarize and fight over git.
exec 9>/tmp/rnews_auto_summarize.lock
flock -n 9 || { echo "another run in progress; skipping" >> "$LOG"; exit 0; }

# Sync first so we don't diverge from CI's daily commit.
git pull --rebase --autostash --quiet origin main >> "$LOG" 2>&1 || {
  echo "pull failed; skipping" >> "$LOG"; exit 0;
}

# Summarize every item that still lacks an LLM summary, in priority order
# (news -> github -> arxiv by score). No cap: arXiv is now small enough that
# the backlog drains over a few runs, then steady state is just the day's new
# items. If codex quota runs out mid-run, news+github are already done and the
# rest retries next run (idempotent).
python3 scripts/llm_summarize.py >> "$LOG" 2>&1 || true

# Push only if data actually changed.
if ! git diff --quiet data/processed data/state; then
  git add data/processed data/state
  git commit -q -m "data: auto codex summaries $(date -u +%Y-%m-%dT%H:%MZ)" >> "$LOG" 2>&1
  git push --quiet origin main >> "$LOG" 2>&1 && echo "pushed updates" >> "$LOG"
else
  echo "no summary changes (quota exhausted or all done)" >> "$LOG"
fi
