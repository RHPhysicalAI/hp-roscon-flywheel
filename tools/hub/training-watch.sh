#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# The training tenant at work, in a terminal: follows its output on the GPU host and shows, for a projector, what
# matters of each loss line - step of the round, loss, steps a second, seconds a step waited for data - with every
# round's start and end called out. AT DEMO TIME, from a laptop or on the host itself; it reads and changes nothing.
# The same numbers as curves: the GPU tenants dashboard (tools/host/fury/75-tenant-metrics.md).
#
#   FURY_SSH=user@host tools/hub/training-watch.sh          from a laptop, over ssh
#   tools/hub/training-watch.sh --local                     on the host itself
#   ... --raw                                               the lines as lerobot and the tenant printed them
#   ... --stdin                                             format what arrives on standard input instead of
#                                                           following anything (tests/tenant_metrics uses it)
# Ctrl-C leaves.
#
# What it follows is the unit's journal (journalctl -u training-tenant), which the host's login account reads
# without sudo: the tenant's script passes on lerobot's loss lines - one every 100 steps, about every ten seconds -
# and says when a round starts and ends (tools/host/fury/flywheel/training-tenant.sh). lerobot abbreviates its own
# step: above 999 (1K for 1100), so the step shown is the progress bar's, which sits on the same line.
# These rounds are real fine-tunes and nothing from them is promoted: it is the training tenant, not the flywheel's
# governed training.
#
#   FURY_SSH     ssh target of the host, user@host          (required unless --local)
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
raw=no where=ssh
for a in "$@"; do
    case $a in
    --raw)   raw=yes ;;
    --local) where=local ;;
    --stdin) where=stdin ;;
    *) die "usage: [FURY_SSH=user@host] ${0##*/} [--local | --stdin] [--raw]" ;;
    esac
done
if [[ $where == ssh ]]; then
    : "${FURY_SSH:?set FURY_SSH=user@host (the ssh target of the machine), or run this on the host with --local}"
    # user@host and nothing else: a value that begins with - would reach ssh as an option
    [[ $FURY_SSH =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
        die "FURY_SSH='$FURY_SSH' is not user@host. Set it to the ssh target of the machine:  FURY_SSH=user@host $0"
elif [[ $where == local ]]; then
    command -v journalctl >/dev/null || die "no journalctl here: --local is for the GPU host. From a laptop:  FURY_SSH=user@host $0"
fi

# What runs on the host, as the login account, no sudo: the unit's state, the line that opened the round in
# progress, then the journal from a few lines back, followed. Nothing in it comes from a variable.
# shellcheck disable=SC2016  # the $( ) is for the far side
follow='echo "[watch] unit: $(systemctl is-active training-tenant.service 2>/dev/null)"
journalctl -u training-tenant -o cat --no-pager -n 1 -g "^\[tenant\] round [0-9]+: " 2>/dev/null
exec journalctl -u training-tenant -o cat --no-pager -f -n 12'

# -tt: with a terminal on the far side Ctrl-C reaches journalctl there and the session ends with it, instead of
# leaving a follower behind on the host until its next write
stream() {
    if [[ $where == ssh ]]; then
        # shellcheck disable=SC2029  # nothing in the remote command comes from a variable
        ssh -tt -o ServerAliveInterval=15 -o LogLevel=ERROR -- "$FURY_SSH" "$follow"
    elif [[ $where == local ]]; then
        bash -c "$follow"
    else
        cat
    fi
}

bold='' dim='' green='' red='' reset=''
if [[ -t 1 && -z ${NO_COLOR:-} ]]; then
    bold=$'\033[1m' dim=$'\033[2m' green=$'\033[32m' red=$'\033[31m' reset=$'\033[0m'
fi

# awk of any make, the one on a Mac included: no gawk extensions. No apostrophe inside the program: it ends the quote
show() {
    awk -v B="$bold" -v D="$dim" -v G="$green" -v E="$red" -v R="$reset" -v RAW="$raw" '
    # the value after "key:" in a loss line
    function field(s, key,    v) {
        s = " " s
        if (!match(s, "[ \t]" key ":[^ \t]+")) return ""
        v = substr(s, RSTART, RLENGTH); sub(/^[^:]*:/, "", v); return v
    }
    # a number after "key": in the ledger line the tenant prints when a round ends
    function number(s, key,    v) {
        if (!match(s, "\"" key "\": *[-0-9.eE+]+")) return "?"
        v = substr(s, RSTART, RLENGTH); sub(/^[^:]*: */, "", v); return v
    }
    function bar(frac,    i, n, out) {
        n = int(frac * 24 + 0.5); out = ""
        for (i = 0; i < 24; i++) out = out (i < n ? "#" : ".")
        return out
    }
    # What the journal holds is what a training process printed, and a terminal obeys it: ESC [ 2 J wipes the
    # projected screen. In the formatted view every CSI sequence goes, then every control character but the tab -
    # which takes the ESC of any other sequence with it and leaves its text harmless. --raw is as it arrives.
    BEGIN {
        CSI = sprintf("%c", 27) "\\[[0-9:;<=>?]*[ -/]*[@-~]"
        CTL = "["; for (i = 1; i < 32; i++) if (i != 9 && i != 10) CTL = CTL sprintf("%c", i); CTL = CTL sprintf("%c", 127) "]"
    }
    # a terminal on the far side ends lines with CR LF, and the log driver of the container stores each message
    # with its newline, which -o cat prints as an empty line after every entry
    { sub(/\r$/, "") }
    RAW != "yes" { gsub(CSI, ""); gsub(CTL, "") }
    /^$/ { next }
    /^\[watch\] unit: / {
        if ($3 != "active") {
            print E "the training tenant is not running (" $3 "). It runs in tenants mode; on the host:  sudo systemctl start training-tenant.service" R
            print D "what follows is what it last printed; new lines appear when it runs again" R
        }
        fflush(); next
    }
    /not seeing messages from other users/ {
        print E "this account cannot read the journal of training-tenant. On the host, once:  sudo usermod -aG systemd-journal $USER   then log in again" R
        fflush(); next
    }
    RAW == "yes" { print; fflush(); next }
    /^\[tenant\] round [0-9]+: / {
        n = $3; sub(/:$/, "", n)
        if (n == opened) next
        opened = n
        what = $0; sub(/^\[tenant\] round [0-9]+: /, "", what); sub(/ -> .*$/, "", what)
        print ""
        print B "==== training tenant, round " n " ====" R "  " what
        fflush(); next
    }
    /^\[tenant\] round [0-9]+ done in / {
        what = $0; sub(/^\[tenant\] /, "", what); sub(/: \{.*$/, "", what)
        print G B what R G "   final loss " number($0, "last_loss") "   " number($0, "steps_per_s") " steps/s over the whole round" R
        fflush(); next
    }
    /^\[tenant\] .*FAILED/ { print E $0 R; fflush(); next }
    /^\[tenant\] / { print D $0 R; fflush(); next }
    /loss:/ && /step:/ {
        loss = field($0, "loss"); u = field($0, "updt_s"); w = field($0, "data_s")
        rate = (u != "" && w != "" && u + w > 0) ? sprintf("%.1f", 1 / (u + w)) : "?"
        if (match($0, /[0-9]+\/[0-9]+ +\[/)) {
            t = substr($0, RSTART, RLENGTH); sub(/ +\[$/, "", t); split(t, part, "/")
            step = part[1] + 0; total = part[2] + 0
            # the bar is drawn just before the step it belongs to is counted: 1099 on the line of step 1100
            if ((step + 1) % 100 == 0) step++
            printf "  step %5d / %d  %s %3d%%   %sloss %s%s   %5s steps/s   data wait %s s\n", step, total, bar(step / total), int(100 * step / total), B, loss, R, rate, (w == "" ? "?" : w)
        } else {
            printf "  step %s   %sloss %s%s   %5s steps/s   data wait %s s\n", field($0, "step"), B, loss, R, rate, (w == "" ? "?" : w)
        }
        fflush(); next
    }
    /End of training/ { next }
    { print; fflush() }
    '
}

trap 'echo; exit 0' INT
if [[ $where == ssh ]]; then on=" on ${FURY_SSH#*@}"; else on=''; fi
[[ $where == stdin ]] || echo "${dim}the training tenant's output$on - Ctrl-C leaves${reset}"
# both sides of the pipe: the follower's status, and the formatter's
ends=(0 0)
stream | show || ends=("${PIPESTATUS[@]}")
[[ ${ends[1]} -eq 0 ]] || die "the formatter (awk) ended with status ${ends[1]} (above) - the lines as they are:  $0 --raw"
# 130: Ctrl-C reached the far side's journalctl, which is the way out
case ${ends[0]} in
0|130) exit 0 ;;
255)   die "ssh to ${FURY_SSH:-the host} ended or never connected (above). Check the target and the tailnet:  ssh ${FURY_SSH:-user@host} true" ;;
*)     die "the follower ended with status ${ends[0]} (above)" ;;
esac
