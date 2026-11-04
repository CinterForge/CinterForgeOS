#!/bin/bash
set -u
control=/run/DeveloperTests
installed=/run/Applications
# KDE can start before systemd has mounted the opt-in virtiofs share.
# Keep the agent alive during guest boot instead of silently exiting once.
for attempt in $(seq 1 90); do
    [ -e "$control/enabled" ] && break
    sleep 1
done
[ -e "$control/enabled" ] || exit 0
printf '[guest] installed GUI test agent ready\n' >> "$control/guest-agent.log"
while [ -e "$control/enabled" ]; do
    if [ -f "$control/request" ]; then
        IFS=$'\t' read -r token platform app version relative window_class < "$control/request" || true
        rm -f "$control/request"
        printf '[guest] accepted request token=%s app=%s\n' "$token" "$app" >> "$control/guest-agent.log"
        case "${token:-}" in *[!a-zA-Z0-9_-]*|'') continue ;; esac
        status="$control/result-$token"
        # A boot-scoped readiness request proves the KDE autostart agent is
        # alive in this actual guest, unlike an old ready line in a host log.
        if [ "${platform:-}" = Probe ]; then
            if [ "${XDG_CURRENT_DESKTOP:-}" = KDE ] || [ "${XDG_CURRENT_DESKTOP:-}" = plasma ] ||
               [[ ":${XDG_CURRENT_DESKTOP:-}:" == *:KDE:* ]]; then
                # The scan runs in the KDE guest against its actual read-only
                # Applications virtiofs mount. Its boot-specific response is
                # returned through the independent writable control share.
                inventory="$control/inventory-$token.tsv"
                if ! ls "$installed" >/dev/null 2>&1; then
                    printf 'ERROR\tApplications mount inaccessible inside KDE: %s\n' "$installed" > "$status"
                    continue
                fi
                if ! find "$installed" -mindepth 3 -maxdepth 3 -type d -print \
                    > "$control/inventory-$token.paths" 2> "$control/inventory-$token.error"; then
                    printf 'ERROR\tApplications inventory scan failed; see inventory-%s.error\n' "$token" > "$status"
                    continue
                fi
                : > "$inventory"
                while IFS= read -r entry; do
                    relative="${entry#"$installed"/}"
                    platform="${relative%%/*}"
                    name_version="${relative#*/}"
                    name="${name_version%/*}"
                    version="${name_version##*/}"
                    case "$platform" in Linux|Windows|macOS) ;; *) continue ;; esac
                    # Tab/newline in names are not supported by the wire format.
                    case "$name$version" in *$'\t'*) continue ;; esac
                    printf '%s\t%s\t%s\n' "$platform" "$name" "$version" >> "$inventory"
                done < "$control/inventory-$token.paths"
                LC_ALL=C sort -o "$inventory" "$inventory"
                # Evidence must be produced by this process inside the SteamOS KDE guest.
                # Do not accept host-side launcher diagnostics as proof of a guest session.
                identity="$control/identity-$token.tsv"
                os_id=$( . /etc/os-release; printf '%s' "${ID:-unknown}" )
                os_name=$( . /etc/os-release; printf '%s' "${PRETTY_NAME:-unknown}" )
                boot_id=$(cat /proc/sys/kernel/random/boot_id)
                session_type="${XDG_SESSION_TYPE:-unknown}"
                session_id="${XDG_SESSION_ID:-unknown}"
                session_desktop="${XDG_CURRENT_DESKTOP:-unknown}"
                session_name="${DESKTOP_SESSION:-unknown}"
                if [ -z "$boot_id" ] || [ "$session_type" != wayland ] && [ "$session_type" != x11 ]; then
                    printf 'ERROR\tGuest has no boot identity or graphical session (XDG_SESSION_TYPE=%s)\n' "$session_type" > "$status"
                    continue
                fi
                printf 'token\t%s\n' "$token" > "$identity"
                printf 'kernel\t%s\n' "$(uname -s)" >> "$identity"
                printf 'architecture\t%s\n' "$(uname -m)" >> "$identity"
                printf 'os_id\t%s\n' "$os_id" >> "$identity"
                printf 'os_name\t%s\n' "$os_name" >> "$identity"
                printf 'boot_id\t%s\n' "$boot_id" >> "$identity"
                printf 'session_type\t%s\n' "$session_type" >> "$identity"
                printf 'session_id\t%s\n' "$session_id" >> "$identity"
                printf 'desktop\t%s\n' "$session_desktop" >> "$identity"
                printf 'desktop_session\t%s\n' "$session_name" >> "$identity"
                printf 'inventory_count\t%s\n' "$(wc -l < "$inventory" | tr -d ' ')" >> "$identity"
                printf '[guest] KDE Applications inventory count=%s\n' "$(wc -l < "$inventory")" >> "$control/guest-agent.log"
                printf 'PASS\tKDE guest evidence and Applications inventory ready\n' > "$status"
            else
                printf 'ERROR\tGuest agent started outside KDE (XDG_CURRENT_DESKTOP=%s)\n' "${XDG_CURRENT_DESKTOP:-unset}" > "$status"
            fi
            continue
        fi
        # Steam is preinstalled in SteamOS. Start it only after the KDE agent
        # has answered the boot-scoped Probe; validate an actual KDE window.
        if [ "${platform:-}" = Application ] && [ "${app:-}" = Steam ]; then
            if ! command -v steam >/dev/null 2>&1; then
                printf 'ERROR\tSteam client executable unavailable in SteamOS guest\n' > "$status"
                continue
            fi
            steam > "$control/output-$token.log" 2>&1 &
            pid=$!
            found=0
            matched_class=''
            matched_window=''
            for attempt in $(seq 1 45); do
                if command -v kdotool >/dev/null 2>&1; then
                    for klass in steam Steam; do
                        windows=$(kdotool search --class "$klass" 2>/dev/null || true)
                        if [ -n "$windows" ]; then found=1; matched_class="$klass"; matched_window="${windows%%$'\n'*}"; break; fi
                    done
                elif command -v xdotool >/dev/null 2>&1; then
                    for klass in steam Steam; do
                        windows=$(xdotool search --class "$klass" 2>/dev/null || true)
                        if [ -n "$windows" ]; then found=1; matched_class="$klass"; matched_window="${windows%%$'\n'*}"; break; fi
                    done
                else
                    printf 'ERROR\tNo semantic KDE window query tool available\n' > "$status"
                    found=2
                    break
                fi
                [ "$found" = 1 ] && break
                # Steam can hand off to an already running client and exit.
                # Do not mistake that normal behavior for GUI verification.
                sleep 2
            done
            if [ "$found" = 1 ]; then
                { printf 'token\t%s\n' "$token"
                  printf 'window_class\t%s\n' "$matched_class"
                  printf 'window_id\t%s\n' "$matched_window"
                  printf 'boot_id\t%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
                } > "$control/window-$token.tsv"
                printf 'PASS\tSteam window visible in KDE\n' > "$status"
            elif [ "$found" = 0 ]; then
                printf 'ERROR\tSteam did not create a detectable KDE window; see output-%s.log\n' "$token" > "$status"
            fi
            continue
        fi
        case "${platform:-}" in Linux|Windows) ;; *) printf 'ERROR\tUnsupported guest platform\n' > "$status"; continue ;; esac
        case "${app:-}/${version:-}/${relative:-}" in
            *..*|*//*|/*|*'\'*|*'~'*) printf 'ERROR\tInvalid application path\n' > "$status"; continue ;;
        esac
        executable="$installed/$platform/$app/$version/$relative"
        if [ ! -f "$executable" ]; then
            printf 'ERROR\tInstalled executable unavailable: %s\n' "$executable" > "$status"
            continue
        fi
        if [ "$platform" = Linux ]; then
            if [ ! -x "$executable" ] || [ ! -x /usr/lib/cinterforge/run-linux-amd64 ]; then
                printf 'ERROR\tLinux executable or shared FEX runner missing\n' > "$status"
                continue
            fi
            /usr/lib/cinterforge/run-linux-amd64 "$executable" > "$control/output-$token.log" 2>&1 &
        elif [ "$app" = 'CinterForge Audio' ]; then
            # Run the production launcher *through its persistent log wrapper*,
            # but enable a one-shot Proton DLL trace for package closure proof.
            # Using bare Wine or a different Proton would invalidate this test.
            CINTER_AUDIO_DLL_DEBUG=1 Cinter launch-with-log windows \
                "$installed/$platform/$app/$version" \
                > "$control/output-$token.log" 2>&1 &
        else
            # Preserve the generic Wine smoke for other installed Windows apps.
            if ! command -v wine >/dev/null 2>&1; then
                printf 'ERROR\tWine unavailable in SteamOS guest\n' > "$status"
                continue
            fi
            export WINEARCH=win64
            export WINEPREFIX="${XDG_RUNTIME_DIR:-/tmp}/cinterforge-smoke-$token"
            export WINEDLLOVERRIDES='mscoree,mshtml='
            wine "$executable" > "$control/output-$token.log" 2>&1 &
        fi
        pid=$!
        verified=0
        for attempt in $(seq 1 40); do
            if command -v kdotool >/dev/null 2>&1; then
                if kdotool search --class "$window_class" 2>/dev/null | grep -q .; then verified=1; break; fi
            elif command -v xdotool >/dev/null 2>&1; then
                if xdotool search --class "$window_class" 2>/dev/null | grep -q .; then verified=1; break; fi
            else
                printf 'ERROR\tNo semantic KDE window query available\n' > "$status"
                verified=2; break
            fi
            kill -0 "$pid" 2>/dev/null || break
            sleep 2
        done
        if [ "$app" = 'CinterForge Audio' ] && [ "$platform" = Windows ]; then
            # Evidence from this exact installed SteamOS Proton run. The Wine
            # loader's builtin/native DLL trace covers runtime LoadLibrary as
            # well as static imports. Keep both the full trace and a small
            # parseable receipt in the writable Builds control share.
            trace="$control/output-$token.log"
            closure="$control/closure-$token.log"
            receipt="$control/closure-$token.tsv"
            proton="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
            version_file="$(dirname "$proton")/version"
            proton_version='unknown'
            if [ -f "$version_file" ]; then
                IFS= read -r proton_version < "$version_file" || true
            fi
            proton_sha='unknown'
            if [ -f "$proton" ]; then
                proton_sha="$(sha256sum "$proton" | cut -d' ' -f1)"
            fi
            dll_loads=$(grep -Eic 'trace:loaddll:' "$trace" || true)
            # Import resolution errors must be investigated; don't claim all
            # plugin/runtime loads are satisfied if any loader reports missing.
            missing_imports=$(grep -Eic 'err:module:.*(import_dll|not found|unresolved|failed to load|failed to import)|err:loaddll:' "$trace" || true)
            {
                printf 'token\t%s\n' "$token"
                printf 'proton\t%s\n' "$proton"
                printf 'proton_version\t%s\n' "$proton_version"
                printf 'proton_sha256\t%s\n' "$proton_sha"
                printf 'boot_id\t%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
                printf 'session\t%s\n' "${XDG_SESSION_TYPE:-unknown}"
                printf 'installed_executable\t%s\n' "$executable"
                printf 'loaded_dll_events\t%s\n' "$dll_loads"
                printf 'loader_errors\t%s\n' "$missing_imports"
                printf 'trace\t%s\n' "$trace"
                printf 'details\t%s\n' "$closure"
            } > "$receipt"
            {
                printf '%s\n' '--- Proton DLL loads (builtin/native, including observed dynamic loads) ---'
                grep -Ei 'trace:loaddll:|trace:module:' "$trace" || true
                printf '%s\n' '--- DLL loading and symbol errors ---'
                grep -Ei 'err:module:.*(import_dll|not found|unresolved|failed to load|failed to import)|err:loaddll:' "$trace" || true
            } > "$closure"
            if [ "$verified" = 1 ] && { [ "$dll_loads" -eq 0 ] || [ "$missing_imports" -ne 0 ]; }; then
                printf 'ERROR\tProton window opened but DLL closure is unverified (loads=%s errors=%s); details=%s\n' \
                    "$dll_loads" "$missing_imports" "$closure" > "$status"
                continue
            fi
        fi
        if [ "$verified" = 1 ]; then
            printf 'PASS\tWindow found for %s\n' "$executable" > "$status"
        elif [ "$verified" = 0 ]; then
            printf 'ERROR\tNo GUI window for %s (pid %s)\n' "$executable" "$pid" > "$status"
        fi
    fi
    sleep 1
done
