#!/bin/bash
# Keep the journal across reboots. As delivered /var/log/journal doesn't exist, so journald logs
# to /run only and a boot that goes wrong leaves nothing behind to read.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
journalctl --flush
journalctl --disk-usage
ls -ld /var/log/journal
