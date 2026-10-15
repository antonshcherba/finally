#!/usr/bin/env bash
# Stop and remove the FinAlly container. The finally-data volume is kept.
docker rm -f finally >/dev/null 2>&1 || true
echo "FinAlly stopped (data volume finally-data preserved)."
