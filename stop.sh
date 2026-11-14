#!/bin/bash
# Stop the hub, the sidecar and Bonsai. Use ./stop.sh --keep-bonsai to leave the model server running.
stop_port() { # name port
    pids=$(lsof -ti "TCP:$2" -sTCP:LISTEN 2>/dev/null)
    if [ -n "$pids" ]; then kill $pids 2>/dev/null; echo "stopped $1 (:$2)"; else echo "$1 was not running"; fi
}
stop_port hub 8000
stop_port sidecar 8790
[ "${1:-}" = "--keep-bonsai" ] || stop_port Bonsai 8080
rm -f /tmp/nod/*.pid
