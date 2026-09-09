#!/bin/bash
# This project was developed with assistance from AI tools.
#
# Port-as-you-go (D043). Versioned copy of ~/after_assemble.sh on the desktop.
# Waits for assemble_all.sh to finish, then deletes the bags whose episodes are in the pushed dataset
# manifest (prune_bags.py --yes: the 'ported' class only; proof/unported/orphan are kept). Output is
# appended to ~/prune-dryrun.txt under a timestamp header — the file name predates D043 and is kept
# so the retention log stays in one place; with --yes it now records what was deleted.
# MinIO credentials come from ~/.minio-env (mode 600): MINIO_ACCESS_KEY / MINIO_SECRET_KEY.
set -u
LOG=$HOME/assemble-all.log
OUT=$HOME/prune-dryrun.txt
ENV_FILE=$HOME/.minio-env
stamp() { date '+%m-%d %H:%M'; }

until grep -qE "\[assemble-all\] (DONE|FAILED)" "$LOG" 2>/dev/null; do sleep 60; done
# Judge the most recent run, not any DONE left in the log by an earlier one.
if [ "$(grep -oE "\[assemble-all\] (DONE|FAILED)" "$LOG" | tail -n1)" != "[assemble-all] DONE" ]; then
  echo "$(stamp) assembly FAILED — no prune" >> "$OUT"; exit 1
fi
[ -r "$ENV_FILE" ] || { echo "$(stamp) ${ENV_FILE} missing (MINIO_ACCESS_KEY/MINIO_SECRET_KEY) — no prune" >> "$OUT"; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
sleep 30
echo "=== $(stamp) prune_bags.py --yes ===" >> "$OUT"
docker run --rm --network host -e MINIO_ACCESS_KEY -e MINIO_SECRET_KEY -e DS -e CUT \
  -v "$HOME/prune_bags.py:/prune.py:ro" -v "$HOME/flywheel-data:/flywheel" \
  --entrypoint python3 act-inference:latest /prune.py --yes >> "$OUT" 2>&1
rc=$?
echo "$(stamp) prune finished (exit ${rc})" >> "$OUT"
exit $rc
