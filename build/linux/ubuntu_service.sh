#!/usr/bin/env bash
# CFOS Ubuntu's ONE guest-local WebApp HTTP service controller.
# Sourced by both the headless console and KDE launchers. Not a host daemon.
cfos_start_web_api() {
    local app_root=/run/steamapps/common/CinterForge/Applications/WebApps/CinterForgeWebApp/2026
    local runtime="$app_root/Server/CinterRuntime.py"
    [[ -f "$runtime" && -f "$app_root/websites/dev/index.html" ]] || {
        echo 'CFOS Ubuntu: WebApp is not installed in /run/steamapps; complete CFOS Stage' >&2
        return 1
    }
    local logfile="${1:-/dev/null}"
    local python=/run/steamapps/common/CinterForge/Applications/Server/CinterForgeServer/2026/Python/bin/python
    [[ -x "$python" ]] || {
        echo "CFOS Ubuntu: staged Server Python environment missing: $python" >&2
        return 1
    }
    "$python" -u "$runtime" --builds "$app_root" --host 127.0.0.1 --port 20380 >"$logfile" 2>&1 &
    CFOS_API_PID=$!
    local i
    for i in $(seq 1 40); do
        if curl --silent --fail --max-time 2 http://127.0.0.1:20380/api/health >/dev/null; then
            echo '[CFOS Ubuntu] READY WebApp API 127.0.0.1:20380'
            return 0
        fi
        if ! kill -0 "$CFOS_API_PID" 2>/dev/null; then
            cat "$logfile" >&2
            return 1
        fi
        sleep 0.25
    done
    echo 'CFOS Ubuntu: WebApp API failed to bind port 20380' >&2
    return 1
}

cfos_stop_web_api() {
    if [[ "${CFOS_API_PID:-0}" =~ ^[1-9][0-9]*$ ]]; then
        kill "$CFOS_API_PID" 2>/dev/null || true
        wait "$CFOS_API_PID" 2>/dev/null || true
    fi
}
