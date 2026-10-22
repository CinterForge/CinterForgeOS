#!/usr/bin/env bash
# Compatibility entry point: invoke the native Xcode target, never bundle.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec xcodebuild -project "$ROOT/CinterForgeOS.xcodeproj" -scheme CinterForgeOS -configuration "${1:-Release}" build
