#!/bin/sh
# Editable KDE-ready automation hook, exported to Built/.../work/out/Test/.
# Runs inside the SteamOS KDE user session after Plasma is available.
set -eu

cinterforgeis_ready() {
    printf '[CinterForgeOS] KDE ready; user=%s desktop=%s\n' "$(id -un)" "${XDG_CURRENT_DESKTOP:-unset}"
    printf '[CinterForgeOS] Applications (/run/Applications):\n'
    if [ -d /run/Applications ]; then
        ls -la /run/Applications
    else
        printf '  not mounted\n'
    fi
    printf '[CinterForgeOS] Test (/run/DeveloperTests):\n'
    if [ -d /run/DeveloperTests ]; then
        ls -la /run/DeveloperTests
    else
        printf '  not mounted\n'
    fi
}

cinterforgeis_ready
