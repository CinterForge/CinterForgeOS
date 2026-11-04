#!/bin/sh
# Launch an installed Linux executable directly on x86-64 or through FEX on ARM.
set -eu

if [ "$#" -lt 1 ]; then
    echo 'Usage: run-linux-amd64 <installed executable> [args...]' >&2
    exit 2
fi

application_executable=$1
if [ ! -f "$application_executable" ] || [ ! -x "$application_executable" ]; then
    echo "Installed application executable missing: $application_executable" >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64)
        exec "$@"
        ;;

    aarch64|arm64)
        if ! command -v FEXBash >/dev/null 2>&1; then
            echo 'FEXBash missing: Linux Heaven execution requires FEXBash.' >&2
            echo 'Run Cinter inspect-x86-runtime for specific missing components.' >&2
            exit 1
        fi

        # Do not rely on a per-user FEXConfig created on first run.
        # The distribution-owned amd64 RootFS is staged in the layer.
        FEX_ROOTFS=/usr/share/cinterforge/fex/RootFS
        if [ ! -e "$FEX_ROOTFS/usr/lib/x86_64-linux-gnu/libglib-2.0.so.0" ]; then
            echo "FEX amd64 RootFS missing GLib: $FEX_ROOTFS" >&2
            echo 'Rebuild the CinterForgeOS FEX userspace and layer.' >&2
            exit 1
        fi

        export FEX_ROOTFS
        # Match the proven Linux Heaven entry path: FEXBash -c, not direct FEX.
        # Pass the program and its arguments as positional parameters rather
        # than assembling a shell command (paths may contain spaces).
        export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
        exec FEXBash -c 'exec "$@"' -- "$@"
        ;;

    *)
        echo "Unsupported SteamOS host architecture: $(uname -m)" >&2
        exit 1
        ;;
esac
