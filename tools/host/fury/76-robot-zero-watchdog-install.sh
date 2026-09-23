#!/bin/bash
# A watchdog for robot zero's world, so that a show day does not need a person to notice "Waiting for sim".
#
# Two things went wrong on 2026-09-22 and this catches both: pose queries (`gz topic`) that outlive their timeout
# pile up inside the world's unit until its memory limit throttles the whole sim; and once the sim is throttled,
# every episode fails and nobody is told. Every minute, as root:
#   1. any pose query older than 60 s is killed (they finish in well under 8 s when the world is healthy);
#   2. if the renderer has heard r00 at under 10 state messages a second for 4 checks in a row - about four
#      minutes, while the world's unit is active and not starting or stopping - robot zero is restarted the way
#      `fury-mode zero` does it: the world, and with it the frames, the episode loop and the reporter.
# A restart costs about two and a half minutes of "Waiting for sim". Everything it does is in the journal.
#
# ONE-TIME BRING-UP (needs sudo):
#   ./76-robot-zero-watchdog-install.sh install   put the script and the timer in place, start the timer
#   ./76-robot-zero-watchdog-install.sh status    timer state and the last twenty things the watchdog did
#   ./76-robot-zero-watchdog-install.sh remove    stop and remove the timer and the script
#
# This project was developed with assistance from AI tools.
set -euo pipefail
name=robot-zero-watchdog
bin=/usr/local/sbin/$name
die() { echo "${0##*/}: $*" >&2; exit 1; }
need_root() { [[ $EUID -eq 0 ]] || die "run with sudo: sudo ./${0##*/} $*"; }

write_watchdog() {
    install -m 0755 /dev/stdin "$bin" <<'EOF'
#!/bin/bash
# robot-zero-watchdog: reap stuck pose queries; restart robot zero's world when r00 has gone quiet. See
# 76-robot-zero-watchdog-install.sh for the why. Runs every minute from robot-zero-watchdog.timer, as root.
set -u
status_url=http://10.20.0.1:9702/status
counter=/run/robot-zero-watchdog.quiet
world=robot-zero-sim.service
units=(robot-zero-sim.service robot-zero-frames.service robot-zero-episodes.service robot-zero-emitter.service)

# 1. pose queries that outlived their timeout
while read -r pid age; do
    kill -KILL "$pid" 2> /dev/null && echo "killed a pose query stuck for ${age}s (pid $pid)"
done < <(ps -eo pid,etimes,args | awk '/gz-transport-topic -e -t \/world\/[a-z_]+\/pose\/info/ && $2 > 60 {print $1, $2}')

# 2. r00's state rate as the renderer hears it; empty when the renderer does not know r00 or does not answer
rate=$(curl -s --max-time 5 "$status_url" 2> /dev/null | python3 -c '
import json, sys
try:
    robots = json.load(sys.stdin)["robots"]
except Exception:
    sys.exit(0)
for r in robots:
    if r.get("id") == "r00":
        print(int(float(r.get("datagrams_per_s", 0))))
' 2> /dev/null)

state=$(systemctl show "$world" -p ActiveState -p SubState --value | tr '\n' ' ')
if [[ $state != "active running " ]]; then
    echo 0 > "$counter"   # starting or stopping: not our call
    exit 0
fi
if [[ -n $rate && $rate -ge 10 ]]; then
    echo 0 > "$counter"
    exit 0
fi
n=$(( $(cat "$counter" 2> /dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
echo "r00 at ${rate:-no} state messages/s ($n in a row)"
if (( n >= 4 )); then
    echo "restarting robot zero"
    echo 0 > "$counter"
    systemctl reset-failed "${units[@]}" 2> /dev/null
    systemctl restart "$world" && systemctl start "${units[@]}"
fi
EOF
}

write_units() {
    cat > /etc/systemd/system/$name.service <<EOF
[Unit]
Description=Robot zero's watchdog: reap stuck pose queries, restart the world when r00 goes quiet

[Service]
Type=oneshot
ExecStart=$bin
EOF
    cat > /etc/systemd/system/$name.timer <<EOF
[Unit]
Description=Run robot zero's watchdog every minute

[Timer]
OnBootSec=5min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF
}

case ${1:-} in
install)
    need_root install
    systemctl cat robot-zero-sim.service > /dev/null 2>&1 || die "robot zero's units are not installed: ./74-robot-zero-install.sh install"
    write_watchdog
    write_units
    systemctl daemon-reload
    systemctl enable --now $name.timer > /dev/null
    echo "installed: $bin, $name.timer (every minute). First check in a minute; watch it:  journalctl -fu $name"
    ;;
status)
    systemctl list-timers --no-legend --no-pager $name.timer 2> /dev/null || echo "$name.timer: not installed"
    echo "--- the watchdog's last actions:"
    journalctl -u $name.service --no-pager -o cat 2> /dev/null | grep -v "^Starting\|^Finished\|^Deactivated\|^$" | tail -20
    ;;
remove)
    need_root remove
    systemctl disable --now $name.timer 2> /dev/null || true
    rm -f /etc/systemd/system/$name.timer /etc/systemd/system/$name.service "$bin" /run/$name.quiet
    systemctl daemon-reload
    echo "removed"
    ;;
*)
    echo "usage: ${0##*/} install | status | remove" >&2; exit 1 ;;
esac
