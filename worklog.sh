#!/bin/bash
# Start the worklog web app detached and open it; reuse it if already running.
#   ./worklog.sh         start (or reuse) and open the browser
#   ./worklog.sh stop
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${WL_PORT:-$(python3 -c 'import sys; sys.path.insert(0,"'"$DIR"'"); from config import CFG; print(CFG["server"]["port"])')}"
URL="http://127.0.0.1:$PORT/"
pid=$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ "$1" = "stop" ]; then
    if [ -n "$pid" ]; then kill $pid && echo "stopped worklog (pid $pid)"; else echo "worklog not running"; fi
    exit 0
fi
if [ -n "$pid" ]; then
    echo "worklog already running (pid $pid) at $URL"
else
    cd "$DIR"
    nohup ./serve.py -p "$PORT" >> serve.log 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do curl -sf -o /dev/null "$URL" && break; sleep 0.5; done
    curl -sf -o /dev/null "$URL" || { echo "worklog failed to start; see $DIR/serve.log"; exit 1; }
    echo "started worklog (pid $!) at $URL"
fi
case "$(uname)" in Darwin) open "$URL";; *) xdg-open "$URL" 2>/dev/null || echo "$URL";; esac
