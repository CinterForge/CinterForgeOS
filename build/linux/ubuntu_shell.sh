#!/usr/bin/env bash
# Headless Ubuntu starts the same installed API as KDE, then opens a live shell.
set -euo pipefail
source /usr/local/lib/cfos-ubuntu-service.sh
CFOS_API_PID=0
trap cfos_stop_web_api EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cfos_start_web_api /tmp/cfos-ubuntu-api.log
# Do not exec: the containing process owns and shuts down its API child.
/bin/bash -i
