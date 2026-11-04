#!/bin/sh
# Editable KDE-ready automation hook, exported to Built/.../work/out/Test/.
# Runs inside the SteamOS KDE user session after Plasma is available.
set -eu

cinterforgeis_ready() {
    printf '[CinterForgeOS] KDE ready; user=%s desktop=%s\n' "$(id -un)" "${XDG_CURRENT_DESKTOP:-unset}"
    printf '[CinterForgeOS] Applications (/run/steamapps/common/CinterForge/Applications):\n'
    if [ -d /run/steamapps/common/CinterForge/Applications ]; then
        ls -la /run/steamapps/common/CinterForge/Applications
    else
        printf '  not mounted\n'
    fi
    printf '[CinterForgeOS] Test (/run/steamapps/common/CinterForge/Applications/Test):\n'
    if [ -d /run/steamapps/common/CinterForge/Applications/Test ]; then
        ls -la /run/steamapps/common/CinterForge/Applications/Test
    else
        printf '  not mounted\n'
    fi
}

cinterforgeis_ready
