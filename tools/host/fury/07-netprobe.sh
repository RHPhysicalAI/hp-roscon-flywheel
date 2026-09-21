#!/bin/bash
# Stand-in guest for checking fury-net before any VM exists: a network namespace on the bridge
# at 10.20.0.99. From it: gateway, dns from the host, and the way out. Leave it up to ping
# 10.20.0.99 from a laptop on the tailnet, which is the real test of the subnet route.
#
#   ./07-netprobe.sh up | down
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

ns=furyprobe
case ${1:-} in
up)
    ip netns add $ns
    ip link add fp-host type veth peer name fp-guest
    ip link set fp-host master virbr-fury up
    ip link set fp-guest netns $ns
    ip -n $ns addr add 10.20.0.99/24 dev fp-guest
    ip -n $ns link set lo up
    ip -n $ns link set fp-guest up
    ip -n $ns route add default via 10.20.0.1
    sleep 3    # stp on the bridge
    guest() { ip netns exec $ns "$@"; }
    echo "## gateway";   guest ping -c2 -W2 10.20.0.1 || true
    echo "## host dns";  guest dig +short +time=3 +tries=1 @10.20.0.1 api.sno-flywheel.local || true
    echo "## way out";   addr=$(dig +short quay.io | grep -E '^[0-9.]+$' | head -1)
    guest curl -s -o /dev/null -w 'quay.io over the masquerade: http %{http_code}\n' --max-time 10 --resolve "quay.io:443:$addr" https://quay.io/ || true
    echo "probe is up at 10.20.0.99 - ping it from the laptop, then run: $0 down"
    ;;
down)
    ip link del fp-host 2>/dev/null || true
    ip netns del $ns 2>/dev/null || true
    ;;
*)  echo "usage: $0 up|down" >&2; exit 1 ;;
esac
