#!/usr/bin/env bash
# The graphical mode of the exact CFOS Ubuntu rootfs used for headless builds.
# The host publishes only loopback; guest services are never directly public.
set -euo pipefail
mode="${1:-launch}"
case "$mode" in launch|test) ;; *) echo "CFOS Ubuntu desktop: unknown mode $mode" >&2; exit 2 ;; esac
for tool in Xvfb xdpyinfo dbus-run-session startplasma-x11 x11vnc websockify; do
    command -v "$tool" >/dev/null || { echo "CFOS Ubuntu desktop: missing $tool" >&2; exit 1; }
done
test -r /usr/share/novnc/vnc.html || { echo 'CFOS Ubuntu desktop: missing noVNC' >&2; exit 1; }
if [[ "$(id -u)" == 0 ]]; then
    exec runuser -u cinterforge -- /usr/local/bin/cfos-ubuntu-desktop "$mode"
fi
export DISPLAY=:95
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=KDE
export KDE_FULL_SESSION=true
export LIBGL_ALWAYS_SOFTWARE=1
export QT_X11_NO_MITSHM=1
export XDG_RUNTIME_DIR="/tmp/cfos-xdg-$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"
# All files are inside the disposable running container, not the source repo.
log_dir="$XDG_RUNTIME_DIR/logs"
mkdir -p "$log_dir"
Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp -ac >"$log_dir/xvfb.log" 2>&1 &
xvfb_pid=$!
plasma_pid=0
vnc_pid=0
web_pid=0
source /usr/local/lib/cfos-ubuntu-service.sh
CFOS_API_PID=0
cleanup() {
    cfos_stop_web_api
    for pid in "$web_pid" "$vnc_pid" "$plasma_pid" "$xvfb_pid"; do
        if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then kill "$pid" 2>/dev/null || true; fi
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ready=0
for i in $(seq 1 40); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then ready=1; break; fi
    kill -0 "$xvfb_pid" 2>/dev/null || { cat "$log_dir/xvfb.log" >&2; exit 1; }
    sleep 0.25
done
[[ "$ready" == 1 ]] || { echo 'CFOS Ubuntu: Xvfb did not start' >&2; exit 1; }
dbus-run-session -- startplasma-x11 >"$log_dir/plasma.log" 2>&1 &
plasma_pid=$!
x11vnc -display "$DISPLAY" -rfbport 5900 -localhost -nopw -forever -shared -xkb \
    >"$log_dir/vnc.log" 2>&1 &
vnc_pid=$!
websockify --web /usr/share/novnc 0.0.0.0:6080 localhost:5900 \
    >"$log_dir/web.log" 2>&1 &
web_pid=$!
# Desktop readiness requires a working Plasma session, not merely a VNC port.
ready=0
for i in $(seq 1 90); do
    if pgrep -u "$(id -u)" -x plasmashell >/dev/null && \
       curl -fsS --max-time 2 http://127.0.0.1:6080/vnc.html >/dev/null; then
        ready=1
        break
    fi
    if ! kill -0 "$plasma_pid" 2>/dev/null || ! kill -0 "$vnc_pid" 2>/dev/null || ! kill -0 "$web_pid" 2>/dev/null; then
        echo 'CFOS Ubuntu desktop: KDE or display transport terminated' >&2
        cat "$log_dir/plasma.log" "$log_dir/vnc.log" "$log_dir/web.log" >&2
        exit 1
    fi
    sleep 1
done
if [[ "$ready" != 1 ]]; then
    echo 'CFOS Ubuntu desktop: KDE did not become ready' >&2
    tail -70 "$log_dir/plasma.log" "$log_dir/vnc.log" "$log_dir/web.log" >&2
    exit 1
fi
echo '[CFOS Ubuntu desktop] READY KDE Plasma + noVNC'
if [[ "$mode" == test ]]; then
    echo '[CFOS Ubuntu desktop] PASS graphical session and loopback display transport'
    exit 0
fi
# Both headless and KDE use the same CFOS-owned API service controller.
cfos_start_web_api "$log_dir/cinter-runtime.log"
# Container lifetime is tied to the launch adapter and the Swift window.
while kill -0 "$plasma_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do sleep 2; done
echo 'CFOS Ubuntu desktop: graphical session exited' >&2
exit 1
