#!/bin/bash
# Put the MIG layout back at boot. On this GPU generation neither MIG mode nor the instances
# survive a reboot or a driver reload.
#
# Profiles are given by ID because the names change between driver branches:
#   9 = 3g.126gb   14 = 2g.63gb   19 = 1g.31gb
# Layout comes from MIG_LAYOUT in /etc/sysconfig/mig-config; `none` means MIG off (see fury-mode).
#
# This project was developed with assistance from AI tools.
set -euo pipefail

gpu=0
layout=${MIG_LAYOUT:-9,19,19,19}

for _ in {1..30}; do
    nvidia-smi -i $gpu >/dev/null 2>&1 && break
    sleep 2
done

mode() { nvidia-smi -i $gpu --query-gpu=mig.mode.current --format=csv,noheader; }

# MIG_LAYOUT=none: the whole GPU, no MIG. Graphics (the sim's cameras) only exists in this state.
if [[ $layout == none ]]; then
    if [[ $(mode) == Enabled ]]; then
        nvidia-smi mig -i $gpu -dci || true
        nvidia-smi mig -i $gpu -dgi || true
        nvidia-smi -i $gpu -mig 0
        [[ $(mode) == Disabled ]] || { echo "could not leave mig mode: something is holding the gpu" >&2; exit 1; }
    fi
    nvidia-smi -L
    exit 0
fi

if [[ $(mode) != Enabled ]]; then
    nvidia-smi -i $gpu -mig 1
    [[ $(mode) == Enabled ]] || { echo "mig mode is pending: something is holding the gpu" >&2; exit 1; }
fi

if nvidia-smi -L | grep -q 'MIG '; then
    echo "instances already exist, leaving them alone"
else
    nvidia-smi mig -i $gpu -cgi "$layout" -C
fi
nvidia-smi -L
