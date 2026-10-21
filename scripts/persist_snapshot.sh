#!/usr/bin/env bash
# Persist a completed read-only OANDA snapshot without overwriting newer collector output.
set -euo pipefail
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
base_blob="$(git rev-parse HEAD:data/summary.json 2>/dev/null || true)"
git add -A data/
if git diff --cached --quiet; then
  echo 'No modified research data to publish.'
  exit 0
fi
git commit -m "data: update timestamped OANDA research snapshot [skip ci]"
for attempt in 1 2 3 4; do
  git fetch origin main
  remote_blob="$(git rev-parse origin/main:data/summary.json 2>/dev/null || true)"
  # An intervening OANDA collection is authoritative. The earlier run cannot overwrite it.
  if [[ "$base_blob" != "$remote_blob" ]]; then
    echo "::warning::A different collector has persisted fresh market data; this run's older snapshot was superseded (attempt $attempt). Skipping stale publication."
    exit 0
  fi
  # When only documentation/workflow/code changed, rebase the data-only commit safely.
  if ! git rebase origin/main; then
    git rebase --abort || true
    echo '::error::Data commit conflicted with upstream. No force push attempted.'
    exit 1
  fi
  if git push origin HEAD:main; then
    echo "Verified persisted market snapshot at $(git rev-parse --short HEAD)"
    exit 0
  fi
  echo "::warning::Concurrent code change detected; safe retry $attempt/4"
  sleep 2
done
echo '::error::Research snapshot publication failed after 4 guarded retries.'
exit 1
