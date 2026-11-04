#!/bin/sh
refresh_debian_apt() {
  apt_lists="$DEBIAN/var/lib/apt/lists"
  if [ ! -d "$apt_lists" ]; then
    echo "CINTER_CREATIVE_SUITE_ERROR apt-lists-directory-missing path=$apt_lists"
    return 1
  fi
  find "$apt_lists" -maxdepth 1 -type f -name '*_Packages.lz4' -exec rm -f -- '{}' + || return 1
  chroot "$DEBIAN" /usr/bin/apt-get -o APT::Update::Error-Mode=any update
}
