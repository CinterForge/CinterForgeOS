#!/bin/sh
# Acquire a real prebuilt artifact. This is NOT source compilation.
set -eu
app=$1 platform=$2
: "${CINTER_APPLICATIONS_ROOT:?}" "${CINTER_APP_ARCHIVE:=}" "${CINTER_APP_URL:=}"
case "$platform" in mac|win|linux) ;; *) echo "Unsupported platform: $platform" >&2; exit 2 ;; esac
output="$CINTER_APPLICATIONS_ROOT/Acquired/$platform/$app"
mkdir -p "$output"
if [ -n "$CINTER_APP_ARCHIVE" ]; then
  [ -f "$CINTER_APP_ARCHIVE" ] || { echo "Missing archive: $CINTER_APP_ARCHIVE" >&2; exit 2; }
  input="$CINTER_APP_ARCHIVE"
elif [ -n "$CINTER_APP_URL" ]; then
  case "$CINTER_APP_URL" in https://*) ;; *) echo 'HTTPS URL required' >&2; exit 2 ;; esac
  : "${CINTER_APP_SHA256:?SHA256 required for URL download}"
  : "${CINTER_APP_FORMAT:?Format required for URL download}"
  command -v curl >/dev/null || { echo 'curl missing' >&2; exit 2; }
  input="$output/download.$CINTER_APP_FORMAT"
  curl --fail --location --retry 4 --output "$input.tmp" "$CINTER_APP_URL"
  printf '%s  %s\n' "$CINTER_APP_SHA256" "$input.tmp" | sha256sum -c -
  mv "$input.tmp" "$input"
else
  echo "CINTER_CREATIVE_SUITE_ERROR no-prebuilt-input app=$app platform=$platform" >&2
  exit 2
fi
format=${CINTER_APP_FORMAT:-}
if [ -z "$format" ]; then
  case "$input" in *.tar.gz) format=tar.gz ;; *.tgz) format=tgz ;; *.tar.xz) format=tar.xz ;; *.zip) format=zip ;; *.dmg) format=dmg ;; *.pkg) format=pkg ;; *.msi) format=msi ;; *.exe) format=exe ;; *.AppImage) format=AppImage ;; *) echo "Unknown archive format: $input" >&2; exit 2 ;; esac
fi
case "$format" in
  tar.gz|tgz|tar.xz) tar -xf "$input" -C "$output" ;;
  zip) command -v unzip >/dev/null || { echo 'unzip missing' >&2; exit 2; }; unzip -oq "$input" -d "$output" ;;
  dmg|pkg|msi|exe|AppImage)
    # Preserve the original installer. Native installation is a platform-specific later step.
    cp "$input" "$output/$(basename "$input")" ;;
  *) echo "Unsupported format: $format" >&2; exit 2 ;;
esac
printf 'CINTER_CREATIVE_SUITE_PREBUILT_ACQUIRED app=%s platform=%s format=%s\n' "$app" "$platform" "$format"
