#!/bin/bash
# Stops the host coordinator loop when the bag disk runs low or the bag count hits the cap, so a
# recording loop can never fill the filesystem the VMs live on again (2026-09-09 outage).
# Detached loop: polls every POLL_S seconds, logs to $LOG, keeps running after parking so
# run-coordinator.sh can see it (pgrep) and refuse to start the loop unguarded.
#
#   nohup tools/host/disk-guard.sh >/dev/null 2>&1 &
#   MIN_FREE_GB=150 CAP=400 nohup tools/host/disk-guard.sh >/dev/null 2>&1 &
#
# This project was developed with assistance from AI tools.
set -u

ENGINE=${ENGINE:-docker}                     # docker on the desktop, podman on the Fury
CONTAINER=${CONTAINER:-act-coordinator}      # the loop container name from run-coordinator.sh
BAGS_DIR=${BAGS_DIR:-$HOME/flywheel-data/bags}
MIN_FREE_GB=${MIN_FREE_GB:-100}              # park when the bag filesystem has less than this free
CAP=${CAP:-450}                              # park when this many bag dirs exist
POLL_S=${POLL_S:-60}
LOG=${LOG:-$HOME/disk-guard.log}

log(){ echo "$(date -u +%FT%TZ) [disk-guard] $*" >> "$LOG"; }
free_gb(){ df -BG --output=avail "$BAGS_DIR" 2>/dev/null | tail -1 | tr -dc 0-9; }
bag_count(){ ls -d "$BAGS_DIR"/*/ 2>/dev/null | wc -l; }
running(){ "$ENGINE" ps -q -f "name=^${CONTAINER}$" 2>/dev/null | grep -q .; }

log "armed: MIN_FREE_GB=$MIN_FREE_GB CAP=$CAP bags POLL_S=${POLL_S}s container=$CONTAINER bags_dir=$BAGS_DIR pid=$$"
low=0
while :; do
  free=$(free_gb); n=$(bag_count)
  if [ -z "$free" ]; then
    log "WARN cannot stat $BAGS_DIR; treating as low"; free=0
  fi
  if [ "$free" -lt "$MIN_FREE_GB" ] || [ "$n" -ge "$CAP" ]; then
    if running; then
      "$ENGINE" stop "$CONTAINER" >/dev/null 2>&1; rc=$?
      log "PARKED $CONTAINER: free=${free}G bags=$n (MIN_FREE_GB=$MIN_FREE_GB CAP=$CAP) stop rc=$rc. Archive or prune bags until free>=${MIN_FREE_GB}G and bags<$CAP, then rerun run-coordinator.sh."
    elif [ "$low" -eq 0 ]; then
      log "LOW: free=${free}G bags=$n (MIN_FREE_GB=$MIN_FREE_GB CAP=$CAP); $CONTAINER not running, loop start is blocked"
    fi
    low=1
  elif [ "$low" -eq 1 ]; then
    log "CLEAR: free=${free}G bags=$n"; low=0
  fi
  sleep "$POLL_S"
done
