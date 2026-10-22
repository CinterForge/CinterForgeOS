#!/usr/bin/env bash
# Compute stable source-tree identity without modifying authoritative checkouts.
# Exclude VCS and generated build/cache products; filenames participate in the hash.
cfos_source_identity() {
    local source="$1"
    [[ -d "$source" ]] || { echo "error: missing source tree: $source" >&2; return 1; }
    ( cd "$source" && find . -type f \
        ! -path './.git/*' ! -path '*/.git/*' \
        ! -path './build/*' ! -path './target/*' \
        ! -path './External/*' ! -path './Package/*' \
        ! -path '*/__pycache__/*' ! -name '.DS_Store' \
        -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256 | awk '{print $1}' )
}
cfos_combine_identity() { printf '%s\n' "$@" | shasum -a 256 | awk '{print $1}'; }
