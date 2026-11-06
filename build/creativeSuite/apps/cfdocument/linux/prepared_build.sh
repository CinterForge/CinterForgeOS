#!/bin/sh
# Scribus: acquire a distributable build. NOT a source compilation.
set -eu
: "${ROOT:?}" "${DEBIAN:?}"
DEST="$ROOT/Installed/Scribus"
mkdir -p "$DEST"
# A provided archive or URL is an explicit prebuilt input, never a fabricated build.
# The guest's persistent Debian filesystem is the package manager's authority.
if [ -n "${CINTER_APP_ARCHIVE:-}" ]; then
  case "$CINTER_APP_ARCHIVE" in
    *.tar.gz|*.tgz|*.tar.xz|*.tar.bz2) tar -xf "$CINTER_APP_ARCHIVE" -C "$DEST" ;;
    *.AppImage|*.appimage) cp "$CINTER_APP_ARCHIVE" "$DEST/application.AppImage"; chmod 755 "$DEST/application.AppImage" ;;
    *.deb) cp "$CINTER_APP_ARCHIVE" "$DEBIAN/tmp/cinter-app-package.deb"; chroot "$DEBIAN" apt-get install -y /tmp/cinter-app-package.deb ;;
    *) echo "CINTER_CREATIVE_SUITE_ERROR unsupported-archive $CINTER_APP_ARCHIVE" >&2; exit 2 ;;
  esac
  echo 'CINTER_CREATIVE_SUITE_PREBUILT_STAGED app=Scribus'
elif [ -n "${CINTER_APP_URL:-}" ]; then
  case "$CINTER_APP_URL" in https://*) ;; *) echo 'CINTER_CREATIVE_SUITE_ERROR https-url-required' >&2; exit 2 ;; esac
  archive="$DEBIAN/tmp/cinter-cfdocument-download"
  # Downloaded artifact must have an explicit type and SHA256; never silently trust it.
  : "${CINTER_APP_SHA256:?CINTER_APP_SHA256 required for downloads}"
  : "${CINTER_APP_FORMAT:?CINTER_APP_FORMAT required for downloads}"
  chroot "$DEBIAN" /usr/bin/curl --fail --location --retry 4 --output /tmp/cinter-cfdocument-download "$CINTER_APP_URL"
  printf '%s  %s\n' "$CINTER_APP_SHA256" "$archive" | sha256sum -c -
  case "$CINTER_APP_FORMAT" in
    tar.gz|tgz|tar.xz|tar.bz2) tar -xf "$archive" -C "$DEST" ;;
    AppImage) cp "$archive" "$DEST/application.AppImage"; chmod 755 "$DEST/application.AppImage" ;;
    deb) chroot "$DEBIAN" apt-get install -y /tmp/cinter-cfdocument-download ;;
    *) echo 'CINTER_CREATIVE_SUITE_ERROR unsupported-download-format' >&2; exit 2 ;;
  esac
  echo 'CINTER_CREATIVE_SUITE_PREBUILT_DOWNLOADED app=Scribus'
else
  chroot "$DEBIAN" apt-get update
  chroot "$DEBIAN" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cfdocument
  echo 'CINTER_CREATIVE_SUITE_DISTRO_PACKAGE_INSTALLED app=Scribus'
fi
