#!/bin/sh
# One-time migration of an existing CFOS installation to string-based commands.
# Run on macOS while CFOS remains running.
#
# Normal invocation from ~/Source/Hope:
#   ./scripts/support/activate-live-hope.sh
# The Hope-root wrapper supplies the known Steam library path.
#
# This is an explicit user action: Stage and guest boot NEVER replace Hope.

set -eu

if [ "$#" -ne 1 ]; then
    echo 'Usage: activate-live-hope.sh /absolute/path/to/steamapps' >&2
    exit 2
fi

steamapps_directory=$1

if [ ! -d "$steamapps_directory/common/CinterForge/Applications" ]; then
    echo "Not a CinterForge Steam library: $steamapps_directory" >&2
    exit 1
fi

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_directory=$(CDPATH= cd -- "$script_directory/../.." && pwd)
source_directory="$repo_directory/guest/layer/usr/lib/cinterforge"
installation_directory="$steamapps_directory/common/CinterForge/Applications/Test"
dispatcher_file="$installation_directory/Dispatcher/Hope"
external_scripts_directory="$installation_directory/Scripts"

if [ ! -f "$dispatcher_file" ]; then
    echo "Existing dispatcher missing: $dispatcher_file" >&2
    exit 1
fi

# Validate the complete source set before changing the live installation.
# A partial deployment leaves the running guest with mismatched commands.
required_scripts='baseline-environments filesystem_inventory.py inspect-unforked-windows test-x86-runtime run-linux-amd64 cinterforge-wine inspect-x86-runtime hope-legacy-commands baseline inspect test installers'

if [ ! -f "$source_directory/hope-dispatcher-seed" ]; then
    echo "Dispatcher source missing: $source_directory/hope-dispatcher-seed" >&2
    exit 1
fi

for script_name in $required_scripts; do
    if [ ! -f "$external_scripts_directory/$script_name" ] && [ ! -f "$source_directory/$script_name" ]; then
        echo "Required command source missing: $source_directory/$script_name" >&2
        echo 'Apply the complete CFOS source patch before activating live Hope.' >&2
        exit 1
    fi
done

backup_file="$dispatcher_file.before-string-dispatch"
if [ -e "$backup_file" ]; then
    echo "Migration backup already exists; refusing to overwrite: $backup_file" >&2
    exit 1
fi

mkdir -p "$external_scripts_directory"

# Install only missing external commands. Existing user-edited tools are preserved.
for script_name in $required_scripts; do
    source_file="$source_directory/$script_name"
    installed_file="$external_scripts_directory/$script_name"

    if [ -e "$installed_file" ]; then
        echo "Preserved existing command/tool: $script_name"
        continue
    fi

    cp "$source_file" "$installed_file"
    if [ "$script_name" != 'filesystem_inventory.py' ]; then
        chmod 755 "$installed_file"
    fi
    echo "Installed command/tool: $script_name"
done

# Back up before replacing the one user-owned dispatcher. Never silently
# repeat this migration, or overwrite the original backup.
cp "$dispatcher_file" "$backup_file"
cp "$source_directory/hope-dispatcher-seed" "$dispatcher_file"
chmod 755 "$dispatcher_file"

echo "Installed string-based dispatcher: $dispatcher_file"
echo "Original dispatcher preserved: $backup_file"
echo 'CFOS can remain running. Run: Hope help, then Hope baseline all.'
