#!/bin/sh
# Execute Creative Suite installs in the persistent Debian rootfs.
# The Alpine LinuxKit service is only a VM supervisor, never a package manager.
set -eu
ROOT=/mnt/cinter-bds/CreativeSuite/Linux
DEBIAN=/mnt/cinter-debian
JOB="$ROOT/.cinter/creative-suite-job"
RESULT="$ROOT/.cinter/creative-suite-result"
LOG="$ROOT/.cinter/creative-suite-log"
finish() {
  code="$1"
  printf '%s\n' "$code" > "$RESULT"
  printf '%s\n' "$code" > "$ROOT/.cinter/vm-stop-request"
  exit "$code"
}
trap 'finish 70' HUP INT TERM
operation=$(sed -n '1p' "$JOB")
application=$(sed -n '2p' "$JOB")
package=$(sed -n '3p' "$JOB")
case "$application" in
  GIMP|Krita|Inkscape|Scribus|Blender|Kdenlive|Ardour|Wine|CinterForge\ Converter|cfdocument) ;;
  *) echo "Unknown application: $application" > "$LOG"; finish 71 ;;
esac
case "$package" in
  *..*|/*) echo "Invalid package path: $package" > "$LOG"; finish 72 ;;
esac
# The supervisor registers Rosetta before dispatching any Debian job.
# Check the contract here too so direct dispatcher invocation fails loud.
if [ ! -x /mnt/rosetta/rosetta ] || [ ! -r /proc/sys/fs/binfmt_misc/rosetta ]; then
  echo CINTER_CREATIVE_SUITE_ERROR rosetta-not-ready
  finish 89
fi
rosetta_enabled=
while IFS= read -r line; do
  [ "$line" = enabled ] && rosetta_enabled=yes
done < /proc/sys/fs/binfmt_misc/rosetta
if [ "$rosetta_enabled" != yes ]; then
  echo CINTER_CREATIVE_SUITE_ERROR rosetta-disabled
  finish 89
fi
echo CINTER_CREATIVE_SUITE_ROSETTA_DISPATCH_READY
# Do not chroot into macOS VirtioFS: dpkg must write into ext4.
# The raw disk is presented by Virtualization.framework as virtio-blk.
mkdir -p "$DEBIAN"
if [ ! -b /dev/vda ]; then
  echo CINTER_CREATIVE_SUITE_ERROR debian-block-device-missing
  ls -l /dev/vd* 2>&1 || true
  finish 82
fi
if ! mount -t ext4 -o rw /dev/vda "$DEBIAN"; then
  echo CINTER_CREATIVE_SUITE_ERROR debian-ext4-mount
  finish 83
fi
echo CINTER_CREATIVE_SUITE_DEBIAN_EXT4_MOUNTED
[ -x "$DEBIAN/bin/sh" ] || [ -x "$DEBIAN/usr/bin/sh" ] || {
  echo "Debian shell missing from ext4: $DEBIAN" > "$LOG"; finish 78;
}
mkdir -p "$ROOT/Installed/$application" "$ROOT/Tests" "$DEBIAN/mnt/cinter-apps" "$DEBIAN/proc" "$DEBIAN/dev" "$DEBIAN/sys"
# Bind app storage and kernel interfaces into Debian; all packages and
# persistent dpkg state remain outside the immutable LinuxKit image.
mount --bind "$ROOT" "$DEBIAN/mnt/cinter-apps"
mkdir -p "$DEBIAN/mnt/cinter-tests"
# Test artifacts live under the CreativeSuite/Linux root used by the job.
# Do not bind the unrelated CreativeSuite/Tests path: it may not exist.
if ! mount --bind "$ROOT/Tests" "$DEBIAN/mnt/cinter-tests"; then
  echo CINTER_CREATIVE_SUITE_ERROR tests-bind-mount
  finish 84
fi
mount -t proc proc "$DEBIAN/proc"
# Debian runs on Linux ext4. Never expose host-backed device
# entries to the chroot: device nodes in the exported rootfs are omitted,
# and binding the service's /dev has produced EACCES for APT's _apt user.
# Give Debian its own Linux tmpfs /dev and create the standard character
# devices with their real major/minor numbers and world-writable modes.
mount -t tmpfs -o mode=755,nosuid tmpfs "$DEBIAN/dev"
mkdir -p "$DEBIAN/dev/pts" "$DEBIAN/dev/shm"
mknod -m 666 "$DEBIAN/dev/null" c 1 3
mknod -m 666 "$DEBIAN/dev/zero" c 1 5
mknod -m 666 "$DEBIAN/dev/random" c 1 8
mknod -m 666 "$DEBIAN/dev/urandom" c 1 9
mknod -m 666 "$DEBIAN/dev/tty" c 5 0
chmod 666 "$DEBIAN/dev/null" "$DEBIAN/dev/zero" "$DEBIAN/dev/random" "$DEBIAN/dev/urandom" "$DEBIAN/dev/tty"
mount -t devpts devpts "$DEBIAN/dev/pts"
mount --bind /sys "$DEBIAN/sys"
# Rosetta handles x86-64 instructions, but the ELF interpreter named
# by Blender must also exist inside the Debian chroot. Reuse the
# x86-64 glibc runtime already staged by the shared LinuxKit build.
if [ "$operation" = smoke ] || [ "$operation" = install ] || [ "$operation" = build-cfaudio ]; then
  GLIBC_SOURCE=/mnt/cinter-bds/Linux/glibc-x86_64
  GLIBC_GUEST=/opt/cinter-glibc-x86_64
  if [ ! -s "$GLIBC_SOURCE/ld-linux-x86-64.so.2" ]; then
    echo "CINTER_CREATIVE_SUITE_ERROR x86-64-loader-source-missing path=$GLIBC_SOURCE/ld-linux-x86-64.so.2" > "$LOG"
    finish 90
  fi
  mkdir -p "$DEBIAN$GLIBC_GUEST" "$DEBIAN/lib64"
  if ! mount --bind "$GLIBC_SOURCE" "$DEBIAN$GLIBC_GUEST"; then
    echo CINTER_CREATIVE_SUITE_ERROR x86-64-glibc-bind-mount > "$LOG"
    finish 91
  fi
  if ! cp "$GLIBC_SOURCE/ld-linux-x86-64.so.2" "$DEBIAN/lib64/ld-linux-x86-64.so.2"; then
    echo CINTER_CREATIVE_SUITE_ERROR x86-64-loader-install > "$LOG"
    finish 92
  fi
  chmod 755 "$DEBIAN/lib64/ld-linux-x86-64.so.2"
  echo "CINTER_CREATIVE_SUITE_X86_64_LOADER_READY path=/lib64/ld-linux-x86-64.so.2"
fi
# The Debian chroot shares the LinuxKit network namespace. A loopback DNS
# stub from another namespace cannot resolve package repositories here.
cp /etc/resolv.conf "$DEBIAN/etc/resolv.conf"
if ! grep -Eq '^[[:space:]]*nameserver[[:space:]]+[^[:space:]]+' "$DEBIAN/etc/resolv.conf"; then
  echo 'CINTER_CREATIVE_SUITE_ERROR linuxkit-dns-no-nameserver' >&2
  finish 93
fi
if ! chroot "$DEBIAN" /usr/bin/getent ahostsv4 repo.msys2.org >/dev/null 2>&1 && [ "$operation" = build-cfaudio ]; then
  echo 'CINTER_CREATIVE_SUITE_ERROR linuxkit-dns-resolution-failed host=repo.msys2.org; check LinuxKit VM DNS/network before retrying Windows Ardour' >&2
  finish 93
fi
# Fail at the actual filesystem boundary, before APT reports misleading
# GPG errors. Both root and APT's restricted account need /dev/null.
chroot "$DEBIAN" /bin/sh -c 'printf "" > /dev/null' || {
  echo CINTER_CREATIVE_SUITE_ERROR debian-dev-null-root
  finish 80
}
# LinuxKit's Alpine/BusyBox chroot does not support GNU --userspec.
# Enter Debian as root, then use Debian's setpriv to test the exact
# unprivileged UID/GID APT uses without disabling APT's sandbox.
if [ ! -x "$DEBIAN/usr/bin/setpriv" ]; then
  echo CINTER_CREATIVE_SUITE_ERROR debian-setpriv-missing
  finish 81
fi
chroot "$DEBIAN" /usr/bin/setpriv --reuid=42 --regid=65534 --clear-groups /bin/sh -c 'printf "" > /dev/null' || {
  echo CINTER_CREATIVE_SUITE_ERROR debian-dev-null-apt
  finish 81
}
echo CINTER_CREATIVE_SUITE_DEBIAN_DEV_READY
# The persistent Debian disk can retain a damaged compressed APT index
# between jobs. Remove only downloaded package indexes before refreshing;
# never touch dpkg's installed-package database or installed applications.
# Refresh failure is fatal rather than silently using stale package lists.
. /usr/local/lib/cinter-creative-suite/env/apt.sh
# The LinuxKit kernel is ARM64, but the Creative Suite Debian rootfs is
# AMD64. Rosetta binfmt executes its userland inside this chroot.
debian_arch=$(chroot "$DEBIAN" /usr/bin/dpkg --print-architecture) || finish 90
if [ "$debian_arch" != amd64 ]; then
  echo "CINTER_CREATIVE_SUITE_ERROR expected-amd64-rootfs got=$debian_arch" > "$LOG"
  finish 91
fi
echo CINTER_CREATIVE_SUITE_DEBIAN_ARCH amd64
# The same generic installation inspection applies to every supported app.
# Script is staged by the existing host guest runner in its host-visible share.
if [ "$operation" = install ]; then
  inspector="$ROOT/.cinter/install_inventory.py"
  [ -s "$inspector" ] || { echo "CINTER_INSTALL_INSPECTION_ERROR missing=$inspector" >&2; finish 94; }
  chroot "$DEBIAN" /usr/bin/python3 /mnt/cinter-apps/.cinter/install_inventory.py --phase before --app "$application" || {
    echo "CINTER_INSTALL_INSPECTION_ERROR baseline=$application" >&2; finish 94;
  }
fi
# Capture downloaded Debian package archives *before* dpkg installs them.
# Package-owned file paths and control metadata are independent evidence from
# the filesystem before/after delta. Filenames are overwritten per run.
inspect_downloaded_debs() {
  inspection="$ROOT/Inspection/$(printf '%s' "$application" | tr ' ' '_')"
  mkdir -p "$inspection"
  : > "$inspection/DownloadedPackages.txt"
  : > "$inspection/PackageContents.txt"
  : > "$inspection/PackageControl.txt"
  # APT archives are in persistent Debian ext4, never the macOS source tree.
  for archive in "$DEBIAN"/var/cache/apt/archives/*.deb; do
    [ -f "$archive" ] || continue
    name=${archive##*/}
    printf '%s\n' "$name" >> "$inspection/DownloadedPackages.txt"
    # Retain the actual downloaded package (including resolved dependencies)
    # in the VM's existing host-visible inspection share before APT consumes it.
    # Files here are evidence, not installed Creative Suite application builds.
    mkdir -p "$inspection/DebianPackages"
    cp "$archive" "$inspection/DebianPackages/$name" || return 1
    chroot "$DEBIAN" /usr/bin/dpkg-deb --contents "/var/cache/apt/archives/$name" >> "$inspection/PackageContents.txt" || return 1
    chroot "$DEBIAN" /usr/bin/dpkg-deb --field "/var/cache/apt/archives/$name" >> "$inspection/PackageControl.txt" || return 1
  done
}
inspect_external_deb() {
  inspection="$ROOT/Inspection/$(printf '%s' "$application" | tr ' ' '_')"
  mkdir -p "$inspection"
  printf '%s\n' "$package" > "$inspection/DownloadedPackages.txt"
  chroot "$DEBIAN" /usr/bin/dpkg-deb --contents "/mnt/cinter-apps/$package" > "$inspection/PackageContents.txt" || return 1
  chroot "$DEBIAN" /usr/bin/dpkg-deb --field "/mnt/cinter-apps/$package" > "$inspection/PackageControl.txt" || return 1
}
install_inspected_apt() {
  refresh_debian_apt &&
  chroot "$DEBIAN" /usr/bin/apt-get install -y --download-only --reinstall --no-install-recommends "$@" &&
  inspect_downloaded_debs &&
  chroot "$DEBIAN" /usr/bin/apt-get install -y --no-install-recommends "$@"
}
# Explicitly dispatch a fixed command by application and package type.
# No host-supplied shell fragments are evaluated.
set +e
{
  echo "CINTER_DEBIAN_JOB operation=$operation app=$application payload=$package"
  case "$operation" in
    install)
      case "$package" in
        '')
          case "$application" in
            Inkscape) install_inspected_apt cfvector ;;
            Scribus) install_inspected_apt cfdocument ;;
            Blender) test -d "$ROOT/Blender" && echo "Pre-extracted Blender payload present" ;;
            Ardour) install_inspected_apt cfaudio xvfb xauth dbus-x11 xdotool openbox jackd2 ffmpeg ;;
            CinterForge\ Converter) install_inspected_apt ffmpeg imagemagick assimp-utils ;;
            *) echo "Missing package for $application"; false ;;
          esac ;;
        *.deb)
          refresh_debian_apt &&
          inspect_external_deb &&
          chroot "$DEBIAN" /usr/bin/apt-get install -y --no-install-recommends "/mnt/cinter-apps/$package" ;;
        *.AppImage|*.appimage)
          cp "$ROOT/$package" "$ROOT/Installed/$application/application.AppImage" &&
          chmod 755 "$ROOT/Installed/$application/application.AppImage" &&
          : ;;
        *.tar.gz|*.tgz|*.tar.xz)
          tar -xf "$ROOT/$package" -C "$ROOT/Installed/$application" ;;
        *) echo "Unsupported package: $package"; false ;;
      esac ;;
    prepare-wine)
      # WineHQ's official Debian 12 (Bookworm) installation, in the
      # persistent AMD64 ext4 rootfs. Never install packages in test-cfaudio.
      if chroot "$DEBIAN" /usr/bin/dpkg-query -W -f='${Status}' winehq-stable 2>/dev/null | grep -qx 'install ok installed' &&
         chroot "$DEBIAN" /usr/bin/dpkg-query -W -f='${Status}' wine-stable-i386 2>/dev/null | grep -qx 'install ok installed'; then
        echo CINTER_WINEHQ_ALREADY_INSTALLED
      else
        chroot "$DEBIAN" /usr/bin/dpkg --add-architecture i386 &&
        refresh_debian_apt &&
        chroot "$DEBIAN" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
          /usr/bin/apt-get install -y --no-install-recommends ca-certificates wget &&
        mkdir -p "$DEBIAN/etc/apt/keyrings" "$DEBIAN/etc/apt/sources.list.d" &&
        chroot "$DEBIAN" /usr/bin/wget -q -O /etc/apt/keyrings/winehq-archive.key \
          https://dl.winehq.org/wine-builds/winehq.key &&
        chroot "$DEBIAN" /usr/bin/wget -q -O /etc/apt/sources.list.d/winehq-bookworm.sources \
          https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources &&
        refresh_debian_apt &&
        chroot "$DEBIAN" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
          /usr/bin/apt-get install -y --install-recommends winehq-stable
      fi &&
      chroot "$DEBIAN" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
        /usr/bin/apt-get install -y --no-install-recommends \
        xvfb xauth openbox dbus-x11 x11-utils procps \
        libasound2 libjack-jackd2-0 libgtk2.0-0 libgtk-3-0 &&
      chroot "$DEBIAN" /usr/bin/dpkg-query -W -f='${Status}' wine-stable-i386 | grep -qx 'install ok installed' &&
      chroot "$DEBIAN" /usr/bin/wine --version ;;
    test-wine)
      # Exercise WineHQ itself, independent of Ardour or any installed app.
      for dependency in winehq-stable wine-stable-amd64 wine-stable-i386 xvfb xauth x11-utils; do
        chroot "$DEBIAN" /usr/bin/dpkg-query -W -f='${Status}' "$dependency" 2>/dev/null | grep -qx 'install ok installed' || {
          echo "CINTER_WINE_TEST_MISSING dependency=$dependency" >&2
          false
        }
      done &&
      chroot "$DEBIAN" /bin/sh /mnt/cinter-apps/WineTest/test_guest.sh ;;
    test-cfaudio)
      # The host stages the complete versioned distribution into the existing
      # VirtioFS mount. Debian executes that distribution, never apt's Ardour.
      target="$package"
      case "$target" in
        win-gui|win-headless|linux-gui|linux-headless) ;;
        *) echo "CINTER_CREATIVE_SUITE_ERROR invalid-cfaudio-test-target=$target"; false ;;
      esac
      # GUI tests must consume the already-built canonical distribution.
      # Never mutate its dependencies via apt while running a test.
      # Native Linux GUI tests do not require Wine. Windows tests do.
      # Check only the dependency set required by the selected target.
      case "$target" in
        win-*) required='winehq-stable wine-stable-amd64 wine-stable-i386 xvfb xauth openbox dbus-x11 x11-utils procps libasound2 libjack-jackd2-0 libgtk2.0-0 libgtk-3-0' ;;
        linux-*) required='xvfb xauth openbox dbus-x11 x11-utils procps libasound2 libjack-jackd2-0 libgtk2.0-0 libgtk-3-0' ;;
      esac
      missing=0
      for dependency in $required; do
        if ! chroot "$DEBIAN" /usr/bin/dpkg-query -W -f='${Status}' "$dependency" 2>/dev/null | grep -qx 'install ok installed'; then
          echo "CINTER_CREATIVE_SUITE_ERROR: missing installed dependency: $dependency (target=$target)" >&2
          missing=1
        fi
      done
      if [ "$missing" -ne 0 ]; then
        echo 'CINTER_CREATIVE_SUITE_ERROR: rebuild the shared Linux distribution; test-time package installation is prohibited.' >&2
        false
      fi
      chroot "$DEBIAN" /usr/bin/env CINTER_ARDOUR_TEST_TARGET="$target" \
        /bin/sh /mnt/cinter-apps/Ardour/TestInstall/${target%%-*}/test_guest.sh ;;
    build-cfaudio)
      # Ardour build scripts execute as child processes and require these
      # validated guest job values and persistent filesystem locations.
      export ROOT DEBIAN application
      /usr/local/lib/cinter-creative-suite/apps/cfaudio/win/build_guest.sh ;;
    smoke)
      # Smoke jobs must invoke real Linux application executables.
      # Never manufacture a native document or export to satisfy a test.
      TESTS="$ROOT/Tests"
      mkdir -p "$TESTS"
      case "$application" in
        Inkscape)
          # Inkscape is the authoring/export engine: it reads an SVG,
          # performs a native edit, saves SVG, then exports PDF.
          cat > "$TESTS/Inkscape.input.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200"><rect x="20" y="20" width="280" height="160" fill="#4682b4"/><text x="40" y="110" font-size="30">CinterForge</text></svg>
SVG
          # Debian's Inkscape 1.2 CLI does not expose document-save.
          # Apply the edit in Inkscape and write the edited SVG through
          # its export action, using a distinct input/output pathname.
          if ! chroot "$DEBIAN" /usr/bin/cfvector /mnt/cinter-tests/Inkscape.input.svg \
            --actions="select-all;object-to-path;export-filename:/mnt/cinter-tests/Inkscape.svg;export-do"; then
            echo CINTER_CREATIVE_SUITE_ERROR cfvector-save-failed
            false
          elif ! grep -q '<path' "$TESTS/Inkscape.svg"; then
            echo CINTER_CREATIVE_SUITE_ERROR cfvector-edit-not-saved
            false
          elif ! chroot "$DEBIAN" /usr/bin/cfvector /mnt/cinter-tests/Inkscape.svg \
            --export-filename=/mnt/cinter-tests/Inkscape.pdf; then
            echo CINTER_CREATIVE_SUITE_ERROR cfvector-pdf-export-failed
            false
          else
            test -s "$TESTS/Inkscape.pdf"
          fi ;;
        Blender)
          # Blender's own background Python API authors, saves, reopens,
          # and exports the scene. Never use host-generated GLB files.
          BLENDER=$(find "$ROOT/Blender" -type f -name cf3d -perm /111 | head -n 1)
          if [ -z "$BLENDER" ]; then
            echo CINTER_CREATIVE_SUITE_ERROR cf3d-executable-missing
            false
          else
            cat > "$TESTS/Blender.smoke.py" <<'PYBLENDER'
import bpy
import os
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2)
bpy.context.object.name = "CinterForge Smoke Cube"
bpy.ops.wm.save_as_mainfile(filepath="/mnt/cinter-tests/Blender.blend", compress=False)
bpy.ops.wm.open_mainfile(filepath="/mnt/cinter-tests/Blender.blend")
assert bpy.data.objects.get("CinterForge Smoke Cube") is not None
bpy.ops.export_scene.gltf(filepath="/mnt/cinter-tests/Blender.glb", export_format="GLB")
assert os.path.getsize("/mnt/cinter-tests/Blender.glb") > 100
PYBLENDER
            # Rosetta translates the CPU instructions, not Linux
            # libraries. Debian's ARM64 libraries cannot satisfy an
            # x86-64 executable. Install the complete GUI/OpenGL/audio
            # runtime as Debian amd64 multiarch packages, retaining
            # apt's dependency resolution instead of copying individual
            # libraries whenever the dynamic loader reports a miss.
            # The package database and libraries persist on ext4.
            if ! chroot "$DEBIAN" /usr/bin/dpkg --print-foreign-architectures | grep -qx amd64; then
              chroot "$DEBIAN" /usr/bin/dpkg --add-architecture amd64 || {
                echo CINTER_CREATIVE_SUITE_ERROR cf3d-multiarch-registration
                false
              }
            fi
            # Check every required package, not only libX11: a previous
            # successful install must not suppress newly added runtime
            # dependencies (such as Blender's libxkbcommon.so.0).
            BLENDER_AMD64_PACKAGES="libc6:amd64 libstdc++6:amd64 libgcc-s1:amd64
              libx11-6:amd64 libxext6:amd64 libxi6:amd64
              libxrender1:amd64 libxfixes3:amd64 libxrandr2:amd64
              libxcursor1:amd64 libxinerama1:amd64 libxxf86vm1:amd64
              libsm6:amd64 libice6:amd64 libgl1:amd64
              libegl1:amd64 libglib2.0-0:amd64 libdbus-1-3:amd64
              libfontconfig1:amd64 libfreetype6:amd64
              libasound2:amd64 libpulse0:amd64 libopenal1:amd64
              libxkbcommon0:amd64 libxkbcommon-x11-0:amd64
              libwayland-client0:amd64 libwayland-cursor0:amd64"
            BLENDER_AMD64_MISSING=""
            for package in $BLENDER_AMD64_PACKAGES; do
              if ! chroot "$DEBIAN" /usr/bin/dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
                BLENDER_AMD64_MISSING="$BLENDER_AMD64_MISSING $package"
              fi
            done
            if [ -n "$BLENDER_AMD64_MISSING" ]; then
              echo "CINTER_CREATIVE_SUITE_AMD64_INSTALL missing=$BLENDER_AMD64_MISSING"
              if ! refresh_debian_apt ||
                 ! chroot "$DEBIAN" /usr/bin/env DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get install -y --no-install-recommends $BLENDER_AMD64_MISSING; then
                echo CINTER_CREATIVE_SUITE_ERROR cf3d-amd64-runtime-install
                false
              fi
            fi
            # Blender's x86_64 executable needs Rosetta configured in
            # the Linux guest before this invocation can succeed.
            chroot "$DEBIAN" /usr/bin/env \
              LD_LIBRARY_PATH=/opt/cinter-glibc-x86_64 \
              "/mnt/cinter-apps/${BLENDER#"$ROOT/"}" -b --python /mnt/cinter-tests/Blender.smoke.py
          fi ;;
        Scribus)
          # Scribus's Python scripting extension creates the document,
          # reopens it, and exports PDF. Xvfb supplies the Qt display;
          # there are no simulated mouse or keyboard events.
          if ! refresh_debian_apt ||
             ! chroot "$DEBIAN" /usr/bin/apt-get install -y --no-install-recommends xvfb xauth; then
            echo CINTER_CREATIVE_SUITE_ERROR cfdocument-xvfb-install
            false
          else
            cat > "$TESTS/Scribus.smoke.py" <<'PYSCRIBUS'
import cfdocument
from cfdocument import *
native = '/mnt/cinter-tests/Scribus.sla'
export = '/mnt/cinter-tests/Scribus.pdf'
assert newDocument((595, 842), (36, 36, 36, 36), PORTRAIT, 1, UNIT_POINTS, NOFACINGPAGES, FIRSTPAGERIGHT, 1)
frame = createText(50, 50, 490, 100)
setText('CinterForge Scribus Smoke Test', frame)
saveDocAs(native)
closeDoc()
openDoc(native)
assert haveDoc()
pdf = PDFfile()
pdf.file = export
pdf.save()
closeDoc()
PYSCRIBUS
            chroot "$DEBIAN" /usr/bin/xvfb-run -a /usr/bin/cfdocument -g -py /mnt/cinter-tests/Scribus.smoke.py
          fi ;;
        GIMP|Krita|Kdenlive)
          # These tests previously substituted ARM64 Debian applications.
          # Never pass a smoke test without executing the installed AMD64
          # release. The AppImage execution/automation must be implemented.
          echo "CINTER_CREATIVE_SUITE_ERROR amd64-appimage-smoke-not-implemented app=$application"
          false ;;
        CinterForge\ Converter)
          # Actual FFmpeg-generated source and lossless transcoded output.
          chroot "$DEBIAN" /usr/bin/ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'sine=frequency=440:duration=2' -c:a pcm_s16le /mnt/cinter-tests/Converter.wav &&
          chroot "$DEBIAN" /usr/bin/ffmpeg -hide_banner -loglevel error -y -i /mnt/cinter-tests/Converter.wav -c:a flac /mnt/cinter-tests/Converter.flac &&
          chroot "$DEBIAN" /usr/bin/ffprobe -v error -show_entries stream=codec_name -of default=noprint_wrappers=1 /mnt/cinter-tests/Converter.flac | grep -qx codec_name=flac &&
          test -s "$TESTS/Converter.wav" && test -s "$TESTS/Converter.flac" &&
          chroot "$DEBIAN" /usr/bin/convert -size 32x32 xc:blue /mnt/cinter-tests/Converter.png &&
          chroot "$DEBIAN" /usr/bin/identify /mnt/cinter-tests/Converter.png | grep -q PNG &&
          # Validate Assimp by converting real geometry, rather than
          # depending on optional CLI inventory commands and their output.
          cat > "$TESTS/Assimp.source.obj" <<'ASSIMPOBJ'
o ConverterTriangle
v 0 0 0
v 1 0 0
v 0 1 0
f 1 2 3
ASSIMPOBJ
          chroot "$DEBIAN" /usr/bin/assimp export /mnt/cinter-tests/Assimp.source.obj /mnt/cinter-tests/Assimp.converted.ply -fply > "$TESTS/Assimp.conversion.log" 2>&1 || {
            cat "$TESTS/Assimp.conversion.log"
            echo CINTER_CREATIVE_SUITE_ERROR assimp-conversion
            false
          } &&
          test -s "$TESTS/Assimp.converted.ply" &&
          grep -aq 'ply' "$TESTS/Assimp.converted.ply" &&
          # Discovery is informational; a failed inventory query must
          # not invalidate a successful import/export conversion.
          (chroot "$DEBIAN" /usr/bin/assimp listext > "$TESTS/Assimp.imports.txt" 2> "$TESTS/Assimp.imports.err" || true) &&
          (chroot "$DEBIAN" /usr/bin/assimp listexport > "$TESTS/Assimp.exports.txt" 2> "$TESTS/Assimp.exports.err" || true) &&
          test -s "$TESTS/Converter.png" ;;
        Ardour)
          # Exercise the actual Ardour GUI in a virtual X11 session with
          # a dummy JACK server. This is an application test, not a
          # synthetic .cfaudio XML file or an FFmpeg-produced export.
          # No screen-coordinate mouse automation is used.
          refresh_debian_apt &&
          chroot "$DEBIAN" /usr/bin/apt-get install -y --no-install-recommends cfaudio xvfb xauth dbus-x11 xdotool openbox jackd2 ffmpeg python3-pyatspi &&
          # Standalone Ardour Lua runs libardour without the GUI. Probe
          # the actual installed 7.x interfaces, create a native session,
          # and invoke Ardour's own export engine. No GUI keystrokes.
          cat > "$TESTS/Ardour.headless.lua" <<'PYARDOUR'
local root = "/mnt/cinter-tests"
local session_path = root .. "/Ardour"
local session_name = "Ardour"
local function log(message)
  print("CINTER_ARDOUR_LUA " .. message)
  io.stdout:flush()
end

log("START")
assert(type(AudioEngine) == "userdata" or type(AudioEngine) == "table",
       "AudioEngine is not exposed by ardour7-lua")
assert(type(create_session) == "function",
       "Ardour Lua create_session API is unavailable")
assert(type(load_session) == "function",
       "Ardour Lua load_session API is unavailable")
log("API create_session=" .. type(create_session) ..
    " load_session=" .. type(load_session))

local backend = AudioEngine:set_backend("JACK", "", "")
assert(backend, "Could not select JACK backend")
log("JACK_SELECTED")

-- Ardour creates the project structure; no synthetic session XML.
local session = create_session(session_path, session_name, 48000)
assert(session, "Ardour create_session returned nil")
log("SESSION_CREATED")

local active_session = Session or session
assert(active_session, "No active Ardour session")
assert(type(active_session.save_state) == "function",
       "Session save_state is unavailable")
active_session:save_state("")
log("SESSION_SAVED")

-- The Converter smoke test runs first and supplies the real WAV.
-- Do not silently export an empty Ardour project.
local converter_wav = root .. "/Converter.wav"
local source = assert(io.open(converter_wav, "rb"),
                      "Converter WAV missing: " .. converter_wav)
assert(source:seek("end") > 1000, "Converter WAV is undersized")
source:close()
log("CONVERTER_WAV_FOUND " .. converter_wav)

-- Standalone ardour7-lua has no Editor:do_import interface.
-- Instead, record the Converter WAV through JACK onto an Ardour
-- audio track. The Python supervisor starts the JACK source, connects
-- it to this track, and signals when the source is ready.
local tracks = active_session:new_audio_track(
  1, 2, nil, 1, "Converter WAV", ARDOUR.PresentationInfo.max_order,
  ARDOUR.TrackMode.Normal, true)
assert(tracks, "Could not create Ardour audio track")
local track = tracks:front()
track:rec_enable_control():set_value(1, PBD.GroupControlDisposition.NoGroup)
active_session:save_state("")
log("TRACK_READY")
local ready = root .. "/Ardour.source-ready"
local connected = false
for attempt = 1, 200 do
  local file = io.open(ready, "r")
  if file then file:close(); connected = true; break end
  ARDOUR.LuaAPI.usleep(100000)
end
assert(connected, "JACK WAV source was not connected to Ardour")
active_session:goto_start()
active_session:maybe_enable_record()
active_session:request_roll(ARDOUR.TransportRequestSource.TRS_UI)
log("RECORDING")
ARDOUR.LuaAPI.usleep(6000000)
active_session:request_stop(false, false, ARDOUR.TransportRequestSource.TRS_UI)
ARDOUR.LuaAPI.usleep(500000)
active_session:save_state("")
log("RECORDED")
assert(type(active_session.simple_export) == "function",
       "Session simple_export is unavailable")
local exporter = active_session:simple_export()
assert(exporter and exporter:check_outputs(), "Ardour export is not configured")
exporter:run_export()
log("EXPORT_COMPLETED")
exporter = nil
close_session()
log("DONE")
PYARDOUR
          # A native JACK playback client replaces Ecasound. It reads the
          # Converter's actual WAV and streams float audio into JACK.
          cat > "$TESTS/Ardour.jack_source.py" <<'PYJACKSOURCE'
import ctypes
import ctypes.util
import pathlib
import struct
import sys
import time
import wave

root = pathlib.Path('/mnt/cinter-tests')
converter = root / 'Converter.wav'
ready = root / 'Ardour.source-ready'
with wave.open(str(converter), 'rb') as wav:
    channels = wav.getnchannels()
    rate = wav.getframerate()
    width = wav.getsampwidth()
    count = wav.getnframes()
    if wav.getcomptype() != 'NONE' or channels not in (1, 2):
        raise RuntimeError('JACK source requires mono/stereo PCM WAV')
    if rate < 8000 or width not in (1, 2, 3, 4) or count < 1000:
        raise RuntimeError(f'Unsupported Converter WAV: {channels}ch {rate}Hz {width}bytes {count}frames')
    raw = wav.readframes(count)

# Decode PCM once outside the real-time JACK callback.
if width == 1:
    samples = [(value - 128) / 128.0 for value in raw]
elif width == 2:
    samples = [value / 32768.0 for (value,) in struct.iter_unpack('<h', raw)]
elif width == 4:
    samples = [value / 2147483648.0 for (value,) in struct.iter_unpack('<i', raw)]
else:
    samples = []
    for offset in range(0, len(raw), 3):
        value = int.from_bytes(raw[offset:offset + 3], 'little', signed=True)
        samples.append(value / 8388608.0)
channel_data = [samples[channel::channels] for channel in range(channels)]
# Converter uses FFmpeg's default 44100 Hz. Resample in memory to
# match the 48000 Hz JACK/Ardour session without changing Converter.
if rate != 48000:
    output_frames = round(count * 48000 / rate)
    resampled = []
    for data in channel_data:
        output = []
        for frame in range(output_frames):
            source_position = frame * rate / 48000
            left = min(int(source_position), count - 1)
            right = min(left + 1, count - 1)
            fraction = source_position - left
            output.append(data[left] * (1 - fraction) + data[right] * fraction)
        resampled.append(output)
    channel_data = resampled
    count = output_frames

library = ctypes.util.find_library('jack')
if not library:
    raise RuntimeError('libjack is not installed')
jack = ctypes.CDLL(library)
jack.jack_client_open.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
jack.jack_client_open.restype = ctypes.c_void_p
jack.jack_port_register.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
jack.jack_port_register.restype = ctypes.c_void_p
jack.jack_port_get_buffer.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
jack.jack_port_get_buffer.restype = ctypes.c_void_p
jack.jack_set_process_callback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
jack.jack_set_process_callback.restype = ctypes.c_int
jack.jack_activate.argtypes = [ctypes.c_void_p]
jack.jack_activate.restype = ctypes.c_int
jack.jack_deactivate.argtypes = [ctypes.c_void_p]
jack.jack_client_close.argtypes = [ctypes.c_void_p]

status = ctypes.c_uint(0)
client = jack.jack_client_open(b'cinter-converter-source', 0, ctypes.byref(status))
if not client:
    raise RuntimeError(f'Cannot open JACK client: status={status.value}')
ports = []
for channel in range(channels):
    port = jack.jack_port_register(client, f'out_{channel + 1}'.encode(),
                                  b'32 bit float mono audio', 2, 0)
    if not port:
        raise RuntimeError(f'Cannot register JACK output {channel + 1}')
    ports.append(port)
position = 0
playing = False
CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p)

@CALLBACK
def process(frames, unused):
    global position, playing
    for channel, port in enumerate(ports):
        pointer = jack.jack_port_get_buffer(port, frames)
        if not pointer:
            return 1
        buffer = (ctypes.c_float * frames).from_address(pointer)
        data = channel_data[channel]
        for frame in range(frames):
            buffer[frame] = data[(position + frame) % count] if playing else 0.0
    if playing:
        position = (position + frames) % count
    return 0

if jack.jack_set_process_callback(client, process, None):
    raise RuntimeError('Cannot install JACK process callback')
if jack.jack_activate(client):
    raise RuntimeError('Cannot activate JACK playback client')
print(f'CINTER_JACK_SOURCE_READY channels={channels} frames={count}', flush=True)
try:
    deadline = time.monotonic() + 35
    while not ready.is_file():
        if time.monotonic() > deadline:
            raise RuntimeError('Ardour did not connect JACK source')
        time.sleep(0.05)
    playing = True
    print('CINTER_JACK_SOURCE_PLAYING', flush=True)
    time.sleep(9)
finally:
    playing = False
    jack.jack_deactivate(client)
    jack.jack_client_close(client)
PYJACKSOURCE
          cat > "$TESTS/Ardour.headless.py" <<'PYARDOURPY'
import pathlib
import shutil
import subprocess
import time
import wave

root = pathlib.Path('/mnt/cinter-tests')
project = root / 'Ardour' / 'Ardour.cfaudio'
export = root / 'Ardour.wav'
# Each smoke run must create its own native session. Ardour's
# create_session refuses to overwrite an existing project.
session_directory = project.parent
if session_directory.exists():
    if not session_directory.is_dir() or session_directory.is_symlink():
        raise RuntimeError(f'Unsafe Ardour session path: {session_directory}')
    shutil.rmtree(session_directory)
export.unlink(missing_ok=True)
log = root / 'Ardour.lua.log'
jack_log = root / 'Ardour.jack.log'
source_log = root / 'Ardour.jack_source.log'
executable = pathlib.Path('/usr/bin/ardour7-lua')
if not executable.is_file():
    raise RuntimeError('Ardour 7 standalone Lua executable is missing')

# The Converter WAV is streamed by our native JACK client and recorded
# into Ardour. The exported WAV must come from Ardour itself.
converter = root / 'Converter.wav'
if not converter.is_file():
    raise RuntimeError(f'Missing Converter WAV in guest: {converter}')
with wave.open(str(converter), 'rb') as audio:
    if audio.getnframes() < 1000:
        raise RuntimeError('Converter WAV contains insufficient audio')
ready_marker = root / 'Ardour.source-ready'
ready_marker.unlink(missing_ok=True)
with jack_log.open('w') as jack_output:
    jack = subprocess.Popen(
        ['/usr/bin/jackd', '-r', '-d', 'dummy', '-r', '48000', '-p', '1024'],
        stdout=jack_output, stderr=subprocess.STDOUT,
    )
    source = None
    try:
        for attempt in range(50):
            if jack.poll() is not None:
                raise RuntimeError('JACK dummy server exited during startup')
            probe = subprocess.run(['/usr/bin/jack_lsp'], capture_output=True, text=True)
            if probe.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('JACK dummy server did not become ready')
        lua = subprocess.Popen(
            [str(executable), str(root / 'Ardour.headless.lua')],
            stdout=log.open('w'), stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 40
            while 'TRACK_READY' not in log.read_text(errors='replace'):
                if lua.poll() is not None:
                    raise RuntimeError(f'Ardour Lua exited {lua.returncode} before track creation')
                if time.monotonic() > deadline:
                    raise RuntimeError('Ardour track creation timed out')
                time.sleep(0.1)
            # Discover JACK ports by their registered audio direction and
            # client identity, not by an assumed ecasound client prefix.
            # JACK client names may be renamed on registration.
            def audio_ports():
                listing = subprocess.run(
                    ['/usr/bin/jack_lsp', '-p'], capture_output=True,
                    text=True, check=True,
                ).stdout
                discovered = []
                for line in listing.splitlines():
                    if line and not line[0].isspace():
                        discovered.append({'name': line.strip(), 'properties': ''})
                    elif discovered and 'properties:' in line.lower():
                        discovered[-1]['properties'] += ' ' + line.lower()
                return discovered

            baseline = audio_ports()
            source_output = source_log.open('w')
            source = subprocess.Popen(
                ['/usr/bin/python3', str(root / 'Ardour.jack_source.py')],
                stdout=source_output, stderr=subprocess.STDOUT,
            )
            source_output.close()
            baseline_names = {port['name'] for port in baseline}
            deadline = time.monotonic() + 10
            while True:
                ports = audio_ports()
                # Prefer newly registered physical JACK output ports.
                # JACK source client registration is discovered dynamically.
                outputs = [
                    port['name'] for port in ports
                    if port['name'] not in baseline_names
                    and 'output' in port['properties']
                ]
                # Ardour track inputs are identified by their track name.
                inputs = [
                    port['name'] for port in ports
                    if 'Converter WAV' in port['name']
                    and 'input' in port['properties']
                ]
                if outputs and inputs:
                    break
                if source.poll() is not None:
                    stderr = source_log.read_text(errors='replace')
                    raise RuntimeError(
                        f'JACK source exited {source.returncode}; log={stderr}; ports={ports}'
                    )
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f'JACK audio ports unavailable; outputs={outputs}, '
                        f'Ardour inputs={inputs}, baseline={baseline}, ports={ports}'
                    )
                time.sleep(0.1)
            # Preserve channel order; never wire the first source
            # channel to every destination channel.
            for channel, target in enumerate(inputs):
                output = outputs[min(channel, len(outputs) - 1)]
                subprocess.run(
                    ['/usr/bin/jack_connect', output, target], check=True,
                )
                print(f'Ardour JACK connected {output} -> {target}', flush=True)
            ready_marker.touch()
            lua.wait(timeout=120)
            if lua.returncode:
                raise RuntimeError(f'Ardour Lua exited {lua.returncode}; see {log}')
        finally:
            ready_marker.unlink(missing_ok=True)
            if lua.poll() is None:
                lua.terminate()
                lua.wait(timeout=5)
            if source and source.poll() is None:
                source.terminate()
                source.wait(timeout=5)
    finally:
        jack.terminate()
        try:
            jack.wait(timeout=5)
        except subprocess.TimeoutExpired:
            jack.kill()
            jack.wait()
print(log.read_text(errors='replace'), flush=True)

if not project.is_file() or project.stat().st_size < 100:
    raise RuntimeError(f'Ardour did not create native session: {project}')
candidates = sorted((root / 'Ardour').rglob('*.wav'))
if not candidates:
    raise RuntimeError('Ardour simple_export produced no WAV')
valid = []
for candidate in candidates:
    try:
        with wave.open(str(candidate), 'rb') as audio:
            if audio.getnframes() >= 1000:
                valid.append(candidate)
    except (wave.Error, EOFError):
        continue
if not valid:
    raise RuntimeError('Ardour export WAV is missing or too short')
export.write_bytes(valid[-1].read_bytes())
(root / 'Ardour.cfaudio').write_bytes(project.read_bytes())
print('CINTER_ARDOUR_NATIVE_SESSION_AND_EXPORT_PASS', flush=True)
PYARDOURPY
          chroot "$DEBIAN" /usr/bin/python3 /mnt/cinter-tests/Ardour.headless.py &&
          test -s "$TESTS/Ardour/Ardour.cfaudio" &&
          test -s "$TESTS/Ardour.wav" ;;
        cfdocument)
          refresh_debian_apt || exit 1
          # cfdocument v1.6+ documents a headless export CLI. The
          # non-destructive .rrdata sidecar is its editable state.
          cat > "$TESTS/cfdocument.generate.py" <<'PYRAPIDRAW'
import pathlib
import struct
import zlib
base = pathlib.Path('/mnt/cinter-tests')
width = height = 32
raw = b''.join(b'\x00' + b''.join(bytes((x * 8, y * 8, 100)) for x in range(width)) for y in range(height))
def chunk(name, payload):
    return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload) & 0xffffffff)
(base / 'cfdocument.input.png').write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
PYRAPIDRAW
          chroot "$DEBIAN" /usr/bin/python3 /mnt/cinter-tests/cfdocument.generate.py || {
            echo CINTER_CREATIVE_SUITE_ERROR rapidraw-test-input-generation-failed
            false
          }
          # A Debian package is not required to expose its executable on PATH.
          # Discover the package-owned executable instead of guessing names.
          chroot "$DEBIAN" /usr/bin/dpkg-query -L rapid-raw > "$TESTS/cfdocument.package-files" || {
            echo CINTER_CREATIVE_SUITE_ERROR rapidraw-package-file-list-unavailable
            false
          }
          RAPIDRAW_BIN=
          while IFS= read -r candidate; do
            case "$candidate" in
              /usr/bin/*|/usr/local/bin/*|/opt/*/bin/*)
                if [ -f "$DEBIAN$candidate" ] && [ -x "$DEBIAN$candidate" ]; then
                  RAPIDRAW_BIN="$candidate"
                  break
                fi ;;
            esac
          done < "$TESTS/cfdocument.package-files"
          if [ -z "$RAPIDRAW_BIN" ]; then
            echo CINTER_CREATIVE_SUITE_ERROR rapidraw-package-executable-not-found
            cat "$TESTS/cfdocument.package-files"
            false
          else
            printf '%s\n' "$RAPIDRAW_BIN" > "$TESTS/cfdocument.executable"
            # Record actual CLI capabilities before invoking the export path.
            # GTK must have an X11 display, a session bus and a private
            # writable runtime directory.  A GUI application's --help
            # can initialize GTK and fail before argument parsing; do
            # not make --help a prerequisite for the actual export.
            mkdir -p "$DEBIAN/tmp/cinter-rapidraw-runtime" && chmod 700 "$DEBIAN/tmp/cinter-rapidraw-runtime" &&
            chroot "$DEBIAN" /usr/bin/apt-get install -y --no-install-recommends xvfb xauth dbus-x11 libgtk-3-0:amd64 libwebkit2gtk-4.1-0:amd64 &&
            chroot "$DEBIAN" /usr/bin/env GTK_USE_PORTAL=0 GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 \
              /usr/bin/dbus-run-session -- /usr/bin/xvfb-run -a -s '-screen 0 1280x800x24 -nolisten tcp' \
              /usr/bin/env XDG_RUNTIME_DIR=/tmp/cinter-rapidraw-runtime "$RAPIDRAW_BIN" export /mnt/cinter-tests/cfdocument.input.png --output /mnt/cinter-tests/cfdocument.png --format png &&
            test -s "$TESTS/cfdocument.png"
          fi ;;
        *)
          echo "CINTER_CREATIVE_SUITE_ERROR unsupported-smoke-application=$application"
          false ;;
      esac ;;
    uninstall)
      # Keep shared Debian dependencies intact for other applications.
      rm -rf "$ROOT/Installed/$application"
      case "$application" in
        Inkscape) chroot "$DEBIAN" /usr/bin/apt-get remove -y cfvector ;;
        Scribus) chroot "$DEBIAN" /usr/bin/apt-get remove -y cfdocument ;;
        Ardour) chroot "$DEBIAN" /usr/bin/apt-get remove -y cfaudio ;;
        FFmpeg) chroot "$DEBIAN" /usr/bin/apt-get remove -y ffmpeg ;;
        *) : ;;
      esac ;;
    *) echo "Unsupported operation: $operation"; false ;;
  esac
} > "$LOG" 2>&1
rc=$?
set -e
if [ "$operation" = install ]; then
  # Inspect even failed installations to capture partial filesystem changes.
  if ! chroot "$DEBIAN" /usr/bin/python3 /mnt/cinter-apps/.cinter/install_inventory.py --phase after --app "$application"; then
    echo "CINTER_INSTALL_INSPECTION_ERROR postscan=$application" >&2
    [ "$rc" -ne 0 ] || rc=95
  fi
fi
cat "$LOG"
finish "$rc"
