#!/bin/bash
# Step 30 (builder container): work/out/steamac-layer.img
# Read-only erofs image; its usr/ becomes the top lowerdir of the guest /usr
# overlay (lowerdir=<layer>/usr:<rootfs>/usr), see guest/initramfs/init.
# Contents: guest/layer/usr (steamac files) + work/out/mesa-venus/usr (Venus
# ICDs and the x86_64 fault reporter from guest/mesa; required unless
# ALLOW_NO_VENUS=1) + the fx-progress-agent binary built by step 25 from
# guest/progress-agent.
set -euo pipefail
. /src/scripts/config.env

OUT=/work/out/steamac-layer.img
VENUS=/work/out/mesa-venus
FEX=/work/out/fex
ST=$(mktemp -d)
trap 'rm -rf "$ST"' EXIT

cp -a /src/guest/layer/usr "$ST/usr"

# ZIP archives and source checkouts do not always preserve executable bits.
# Every authored command in this directory has a script interpreter header.
# Install these commands with executable permissions instead of relying on
# whichever permissions happened to survive archive extraction.
guest_command_sources="/src/guest/layer/usr/lib/cinterforge"
guest_command_destination="$ST/usr/lib/cinterforge"

for source_script in "$guest_command_sources"/*; do
    if [[ ! -f "$source_script" ]]; then
        continue
    fi

    read -r first_line < "$source_script" || true

    if [[ "$first_line" != '#!'* ]]; then
        continue
    fi

    command_name="$(basename "$source_script")"
    install -D -m 0755 "$source_script" "$guest_command_destination/$command_name"
done

# KDE's login preparation requires all four groups of eight launchers.
# Explicitly stage the authored entries and reject an incomplete OS layer
# during BUILD, not during SDDM login (which otherwise prevents the desktop).
for platform in linux windows; do
    for flavor in built unforked; do
        for app in gimp krita inkscape scribus blender kdenlive ardour rapidraw; do
            entry="cinterforge-$flavor-$platform-$app.desktop"
            source_entry="/src/guest/layer/usr/share/applications/$entry"
            [[ -s "$source_entry" ]] || { echo "[layer] missing KDE source launcher: $source_entry" >&2; exit 1; }
            install -D -m 0644 "$source_entry" "$ST/usr/share/applications/$entry"
        done
    done
done
# Two independent original-publisher installer categories.
for platform in linux windows; do
    for app in gimp krita inkscape scribus blender kdenlive ardour rapidraw; do
        entry="cinterforge-install-$platform-$app.desktop"
        source_entry="/src/guest/layer/usr/share/applications/$entry"
        [[ -s "$source_entry" ]] || { echo "[layer] missing installer launcher: $source_entry" >&2; exit 1; }
        install -D -m 0644 "$source_entry" "$ST/usr/share/applications/$entry"
    done
done
for group in cinterforge-linux cinterforge-windows linux-unforked windows-unforked linux-install windows-install; do
    source_directory="/src/guest/layer/usr/share/desktop-directories/$group.directory"
    [[ -s "$source_directory" ]] || { echo "[layer] missing KDE menu directory: $source_directory" >&2; exit 1; }
    install -D -m 0644 "$source_directory" "$ST/usr/share/desktop-directories/$group.directory"
done
menu=/src/guest/layer/usr/share/cinterforge/kde/cinterforge-unforked.menu
[[ -s "$menu" ]] || { echo "[layer] missing KDE four-group menu: $menu" >&2; exit 1; }
install -D -m 0644 "$menu" "$ST/usr/share/cinterforge/kde/cinterforge-unforked.menu"
echo '[layer] validated 32 KDE launchers and 4 application-menu groups'
# Exactly one shared Steam library: remove old nested VirtioFS mount units
# even if an older checkout still contains them. Don't layer two mounts.
rm -f "$ST/usr/lib/systemd/system/run-DeveloperTests.mount" \
      "$ST/usr/lib/systemd/system/run-DeveloperTests.automount" \
      "$ST/usr/lib/systemd/system/multi-user.target.wants/run-DeveloperTests.automount" \
      "$ST/usr/lib/systemd/system/run-Applications.mount" \
      "$ST/usr/lib/systemd/system/run-Applications.automount" \
      "$ST/usr/lib/systemd/system/run-Applications-Logs.mount" \
      "$ST/usr/lib/systemd/system/multi-user.target.wants/run-Applications.automount" \
      "$ST/usr/lib/systemd/system/multi-user.target.wants/run-Applications-Logs.mount" \
      "$ST/usr/lib/systemd/system/run-Support.mount" \
      "$ST/usr/lib/systemd/system/run-Support.automount" \
      "$ST/usr/lib/systemd/system/multi-user.target.wants/run-Support.automount"
# Stage the two authored steamapps units explicitly, as we do KDE launchers.
# The host source may have both units while a stale/intermediate copied tree
# does not; fail with the exact source path rather than a misleading generic
# staged-unit error. Never recreate the deleted Applications/Support mounts.
for unit in run-steamapps.mount run-steamapps.automount; do
    source_unit="/src/guest/layer/usr/lib/systemd/system/$unit"
    [[ -s "$source_unit" ]] || { echo "[layer] missing authored steamapps unit: $source_unit" >&2; exit 1; }
    install -D -m 0644 "$source_unit" "$ST/usr/lib/systemd/system/$unit"
done
ln -sfn ../run-steamapps.automount "$ST/usr/lib/systemd/system/multi-user.target.wants/run-steamapps.automount"
[[ -f "$ST/usr/lib/systemd/system/run-steamapps.mount" && -f "$ST/usr/lib/systemd/system/run-steamapps.automount" ]] \
  || { echo '[layer] steamapps units failed to stage' >&2; exit 1; }
# Install the guest Hope entry point with executable permissions.
# Source archive permissions are not reliable, so set the mode explicitly.
guest_hope_source="/src/guest/layer/usr/bin/Hope"
guest_hope_destination="$ST/usr/bin/Hope"

if [[ ! -f "$guest_hope_source" ]]; then
    echo "[layer] guest Hope source file missing: $guest_hope_source" >&2
    exit 1
fi

install -D -m 0755 "$guest_hope_source" "$guest_hope_destination"

if [[ ! -x "$guest_hope_destination" ]]; then
    echo "[layer] guest Hope entry point is not executable: $guest_hope_destination" >&2
    exit 1
fi

sh -n "$guest_hope_destination"
# Standalone FEX is a required ARM64 runtime, not the bundled FEXBash stub.
[[ -x "$FEX/usr/bin/FEX" ]] || {
    echo "[layer] FEX ARM64 runtime missing: run scripts/build-image.sh fex" >&2; exit 1;
}
cp -a "$FEX/usr/." "$ST/usr/"
[[ -x "$ST/usr/bin/FEX" ]] || { echo '[layer] FEX binary not installed' >&2; exit 1; }

# Standalone x86-64 userspace is independent of Valve's Steam runtime.
# Only copy verified Build artifacts; never edit the installed SteamOS disk.
USERSPACE=/work/out/fex-userspace
[[ -s "$USERSPACE/wine-executable.txt" && -x "$USERSPACE/RootFS/usr/bin/bash" && -e "$USERSPACE/RootFS/usr/lib/x86_64-linux-gnu/libglib-2.0.so.0" ]] || {
    echo '[layer] FEX x86-64 RootFS/Wine missing: run scripts/build-image.sh userspace' >&2; exit 1;
}
mkdir -p "$ST/usr/share/cinterforge/fex" "$ST/usr/share/fex-emu"
cp -a "$USERSPACE/RootFS" "$ST/usr/share/cinterforge/fex/RootFS"
cp -a "$USERSPACE/Wine" "$ST/usr/share/cinterforge/fex/Wine"
install -m 0644 "$USERSPACE/wine-executable.txt" "$ST/usr/share/cinterforge/fex/wine-executable.txt"
cat > "$ST/usr/share/fex-emu/Config.json" <<'FEX_CONFIG'
{
  "Config": {
    "RootFS": "/usr/share/cinterforge/fex/RootFS"
  }
}
FEX_CONFIG
# Export a stable command for the installed Windows launcher, but leave
# Steam/Proton's own Wine and pressure-vessel environment entirely untouched.

# Authored CinterForgeOS guest configuration: package the actual KDE/systemd
# paths from guest_config, never generate/inject shell scripts from Hope.
CFG=/src/guest/layer/usr/share/cinterforge/guest_config
install -D -m 0755 "$CFG/select-kde-desktop-session" "$ST/usr/lib/cinterforge/select-kde-desktop-session"
# Fail the build if an obsolete guest login script is accidentally staged.
# SDDM ExecStartPre runs this on EVERY boot; an invalid awk expression keeps
# the user at the serial login prompt instead of starting Plasma.
sh -n "$ST/usr/lib/cinterforge/select-kde-desktop-session"
grep -Fq 'Hidden=true' "$ST/usr/lib/cinterforge/select-kde-desktop-session" \
    || { echo '[layer] KDE protection missing: Steam autostart must be disabled' >&2; exit 1; }
grep -Fq 'substr($0, 1, 1) == "["' "$ST/usr/lib/cinterforge/select-kde-desktop-session" \
    || { echo '[layer] obsolete KDE login script (missing INI parser fix)' >&2; exit 1; }
install -D -m 0755 "$CFG/select-kde-wallpaper" "$ST/usr/lib/cinterforge/select-kde-wallpaper"
install -D -m 0755 "$CFG/verify-virtiofs-mounts" "$ST/usr/lib/cinterforge/verify-virtiofs-mounts"
install -D -m 0755 "$CFG/install-kde-places" "$ST/usr/lib/cinterforge/install-kde-places"
install -D -m 0644 "$CFG/user-places.xbel" "$ST/usr/share/cinterforge/kde/user-places.xbel"
install -D -m 0644 "$CFG/cinterforge-installed-test.desktop" "$ST/usr/share/cinterforge/kde/cinterforge-installed-test.desktop"
install -D -m 0644 "$CFG/cinterforge-installed-test.desktop" "$ST/usr/share/xdg/autostart/cinterforge-installed-test.desktop"
install -D -m 0644 "$CFG/sddm-desktop.conf" "$ST/usr/lib/systemd/system/sddm.service.d/90-cinterforgeos-desktop.conf"
install -D -m 0644 "$CFG/cinterforge-kde-prepare.service" "$ST/usr/lib/systemd/system/cinterforge-kde-prepare.service"
install -D -m 0644 "$CFG/cinterforge-virtiofs-verify.service" "$ST/usr/lib/systemd/system/cinterforge-virtiofs-verify.service"

# KDE readiness stays inside the guest layer; no extra host mount is required.

# Steamac owns executable permissions and the complete desktop integration.
# Fail during the layer build, not at login, if authored bridge files disappear.
for file in \
    "$ST/usr/share/xdg/autostart/cinterforge-support-desktop-ready.desktop" \
    "$ST/usr/share/cinterforge/backgrounds/cinterforge-linux-1920x1080.png" \
    "$ST/usr/share/wallpapers/Steam Deck Logo Default.jpg"; do
    test -e "$file" || { echo "[layer] missing authored KDE file: $file" >&2; exit 1; }
done


if [[ -d $VENUS/usr ]]; then
    cp -a "$VENUS/usr/." "$ST/usr/"
    [[ -f $ST/usr/lib/steamac/x86_64/fault-report.so ]] \
        || { echo "[layer] $VENUS lacks usr/lib/steamac/x86_64/fault-report.so: rebuild guest/mesa (x86 step)" >&2; exit 1; }
    venus_info=$(cd "$VENUS/usr" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16)
    echo "[layer] included Venus tree from $VENUS ($venus_info)"
elif [[ ${ALLOW_NO_VENUS:-} == 1 ]]; then
    venus_info="ABSENT (ALLOW_NO_VENUS=1)"
    echo "[layer] WARNING: $VENUS/usr missing, building layer WITHOUT Venus (ALLOW_NO_VENUS=1)" >&2
else
    echo "[layer] $VENUS/usr missing: build guest/mesa first (or ALLOW_NO_VENUS=1 for a test layer)" >&2
    exit 1
fi

AGENT=/work/cache/progress-agent/fx-progress-agent
[[ -x $AGENT ]] || { echo "[layer] $AGENT missing: run step 25 (scripts/build-image.sh layer does)" >&2; exit 1; }
install -m 0755 "$AGENT" "$ST/usr/lib/steamac/fx-progress-agent"
for target in gamescope-session.target plasma-session.target; do
    [[ -L $ST/usr/lib/systemd/user/$target.wants/fx-progress-agent.service ]] \
        || { echo "[layer] fx-progress-agent.service is not wanted by $target" >&2; exit 1; }
    [[ -L $ST/usr/lib/systemd/user/$target.wants/fx-clipboard-agent.service ]] \
        || { echo "[layer] fx-clipboard-agent.service is not wanted by $target" >&2; exit 1; }
done
# Desktop Mode: our plasma-session.target replaces the Frame's VR desktop target
# (SteamVR units are masked); gamescope-session runs the nested desktop, whose
# Steam autostart keeps the selected client.
[[ -f $ST/usr/lib/systemd/user/plasma-session.target && -f $ST/usr/lib/systemd/user/steamac-nested-desktop.service ]] \
    && ! grep -q '^Wants=.*steamvr' "$ST/usr/lib/systemd/user/plasma-session.target" \
    && grep -q 'steamac-nested-desktop.service' "$ST/usr/lib/steamos/gamescope-session" \
    && grep -q '^Exec=/usr/lib/steamac/steam-kde-login' "$ST/usr/lib/steamac/desktop-xdg/autostart/steam.desktop" \
    || { echo "[layer] Desktop Mode units (plasma-session.target, steamac-nested-desktop.service, Steam autostart) missing" >&2; exit 1; }
# Same binary, root mode: started by udev for the launcher's fx.clock port.
grep -q 'fx-progress-agent clock-sync' "$ST/usr/lib/systemd/system/fx-clock-sync.service" \
    && grep -q 'fx-clock-sync.service' "$ST/usr/lib/udev/rules.d/70-fx-progress.rules" \
    || { echo "[layer] fx-clock-sync.service / its udev rule missing" >&2; exit 1; }
# Same binary, root mode: the gamepad, started by udev for the launcher's fx.pad port.
grep -q 'fx-progress-agent pad$' "$ST/usr/lib/systemd/system/fx-pad.service" \
    && grep -q 'fx-pad.service' "$ST/usr/lib/udev/rules.d/70-fx-progress.rules" \
    || { echo "[layer] fx-pad.service / its udev rule missing" >&2; exit 1; }
# ... and one instance per further player's port (fx.pad2, ...).
grep -q 'fx-progress-agent pad$' "$ST/usr/lib/systemd/system/fx-pad@.service" \
    && grep -q 'FX_PAD_PORT=/dev/virtio-ports/%i$' "$ST/usr/lib/systemd/system/fx-pad@.service" \
    && grep -q 'fx-pad@\$attr{name}.service' "$ST/usr/lib/udev/rules.d/70-fx-progress.rules" \
    || { echo "[layer] fx-pad@.service / its udev rule missing" >&2; exit 1; }
# Same binary as systemd-suspend.service's ExecStart: the launcher pauses the VM instead.
grep -q 'fx-progress-agent sleep suspend$' "$ST/usr/lib/systemd/system/systemd-suspend.service.d/50-steamac-sleep.conf" \
    || { echo "[layer] systemd-suspend.service drop-in (fx-progress-agent sleep) missing" >&2; exit 1; }

# Sanity: the pieces the initramfs and the A/B flow depend on.
for f in usr/bin/splctl usr/lib/rauc/post-install.sh usr/lib/steamac/kernelsetup.sh \
         usr/lib/steamac/rauc-shims/steamos-chroot usr/lib/steamac/steam-gfx-env \
         usr/lib/steamac/steam-shader-defaults usr/lib/steamac/steam-client \
         usr/lib/steamac/steam-kde-login \
         usr/lib/steamac/nested-desktop usr/lib/steamac/desktop-bin/kwin_wayland_wrapper \
         usr/lib/steamac/desktop-bin/steamosctl usr/lib/steamos/gamescope-session; do
    # Source archives may lose executable mode. Install the required OS startup
    # commands explicitly, just as we do the guest Hope bridge above.
    source_command="/src/guest/layer/$f"
    if [[ ! -f "$source_command" ]]; then
        echo "[layer] required OS command missing: $source_command" >&2
        exit 1
    fi
    install -D -m 0755 "$source_command" "$ST/$f"
    [[ -x "$ST/$f" ]] || { echo "[layer] $f missing or not executable" >&2; exit 1; }
    bash -n "$ST/$f"
done
[[ -f $ST/usr/lib/systemd/user/steam.service.d/50-steamac.conf ]] \
    || { echo "[layer] steam.service drop-in missing" >&2; exit 1; }
[[ ! -e $ST/usr/lib/systemd/user/steam.service && ! -e $ST/usr/share/deckard/RUNSTEAM.sh ]] \
    || { echo "[layer] proprietary stock Steam files must not be overlaid" >&2; exit 1; }
grep -q 'ExecStartPre=/usr/lib/steamac/steam-client' "$ST/usr/lib/systemd/user/steam.service.d/50-steamac.conf" \
    && grep -q 'ExecStartPost=/usr/lib/steamac/steam-client --watch' "$ST/usr/lib/systemd/user/steam.service.d/50-steamac.conf" \
    || { echo "[layer] steam.service client setup or watcher missing" >&2; exit 1; }
[[ -f $ST/usr/lib/steamac/bootstrap-steam-arm64.py ]] \
    || { echo '[layer] Valve ARM64 client bootstrap missing' >&2; exit 1; }
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$ST/usr/lib/steamac/bootstrap-steam-arm64.py"
ls "$ST"/usr/lib/steamac/masks.d/*.list >/dev/null

# layer-release: content hash (excluding itself) so a boot log identifies the layer.
tree_hash=$(cd "$ST" && find usr -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16)
cat > "$ST/usr/lib/steamac/layer-release" <<EOF
steamac-layer content=$tree_hash rootfs-pin=$STEAMOS_BUILDID venus=$venus_info
EOF

# Directory/file modes come from the repo checkout; normalise and make root-owned.
find "$ST" -type d -exec chmod 0755 {} +
find "$ST" -type f -perm -u+x -exec chmod 0755 {} +
find "$ST" -type f ! -perm -u+x -exec chmod 0644 {} +

epoch=${SOURCE_DATE_EPOCH:-1767225600}
rm -f "$OUT.tmp"
mkfs.erofs -zlz4hc -T "$epoch" -U 5fea1f2a-0c6b-4d5e-9a1e-0000000000a1 --all-root -L steamac-layer "$OUT.tmp" "$ST" >/dev/null
mv "$OUT.tmp" "$OUT"
echo "[layer] $OUT: $(stat -c %s "$OUT") bytes; $(cat "$ST/usr/lib/steamac/layer-release")"
