#!/bin/sh
# Invoked by KDE after the user desktop session starts. Edit this file in
# Applications/macOS/CinterForgeOS/<version>/Support/ without rebuilding.
# Runs in the SteamOS guest as steamos; no sudo or root privileges.
set -eu
printf '[CinterForgeOS Support] desktop ready: user=%s session=%s desktop=%s\n' "$(id -un)" "${XDG_SESSION_TYPE:-unset}" "${XDG_CURRENT_DESKTOP:-unset}"
# Add application automation here, for example:
# /usr/lib/cinterforge/launch-ardour-linux
# /usr/lib/cinterforge/launch-ardour-windows
