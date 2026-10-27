#!/bin/sh
# Invoked by KDE after the user desktop session starts. Edit this file in
# Applications/macOS/CinterForgeOS/<version>/Support/ without rebuilding.
# Runs in the SteamOS guest as steamos; no sudo or root privileges.
set -eu
printf '[CinterForgeOS Support] desktop ready: user=%s session=%s desktop=%s\n' "$(id -un)" "${XDG_SESSION_TYPE:-unset}" "${XDG_CURRENT_DESKTOP:-unset}"

# Desktop shortcuts are provisioned once, by guest_config/install-kde-places.
# Do not generate a second pair here: the previous implementation raced
# with KDE provisioning and used different paths/launcher names.
# In particular, never rewrite desktop entries during Plasma startup.
printf '[CinterForgeOS Support] benchmark launchers are managed by install-kde-places\n'
