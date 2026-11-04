#!/bin/sh
# External Cinter dispatcher. The guest /usr/bin/Cinter only forwards arguments here.
# Command names remain stable; implementation scripts are grouped by purpose.
set -eu
scripts_directory=/run/steamapps/common/CinterForge/Applications/Test/Scripts

resolve_command() {
    command_name=$1
    match_count=0
    match_path=
    for candidate in "$scripts_directory"/*/"$command_name.sh" "$scripts_directory"/*/"$command_name.py" "$scripts_directory"/"$command_name.sh"; do
        if [ -f "$candidate" ]; then
            match_count=$((match_count + 1))
            match_path=$candidate
        fi
    done
    if [ "$match_count" -ne 1 ]; then
        echo "[Cinter] Expected one implementation for '$command_name'; found $match_count" >&2
        return 1
    fi
    if [ ! -x "$match_path" ]; then
        echo "[Cinter] Command is not executable: $match_path" >&2
        return 1
    fi
    printf '%s\n' "$match_path"
}

show_help() {
    echo 'Usage: Cinter <command> [arguments...]'
    echo 'Available commands:'
    for candidate in "$scripts_directory"/*/*.sh "$scripts_directory"/*/*.py; do
        if [ -f "$candidate" ] && [ -x "$candidate" ]; then
            command_name=${candidate##*/}
            command_name=${command_name%.sh}
            command_name=${command_name%.py}
            case "$command_name" in
                cinter-dispatcher|cinter-commands) continue ;;
            esac
            printf '  %s\n' "$command_name"
        fi
    done
}

if [ "$#" -eq 0 ]; then
    show_help
    exit 0
fi
command_name=$1
shift
# CFOS/Test are Konsole aliases for the same externally staged test command.
case "$command_name" in
    CFOS|cfos)
        [ "${1:-}" = test ] || { echo 'Usage: CFOS test <app> <linux|win> Release' >&2; exit 2; }
        shift
        command_name=test
        ;;
    Test)
        command_name=test
        ;;
esac
case "$command_name" in
    help|-h|--help) show_help; exit 0 ;;
    ''|*[!a-zA-Z0-9_-]*) echo "[Cinter] Invalid command name: $command_name" >&2; exit 2 ;;
esac
script_path=$(resolve_command "$command_name") || exit 2
exec "$script_path" "$@"
