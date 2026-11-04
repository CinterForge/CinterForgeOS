# steamac image builder: every tool needed to fetch/verify the SteamOS bundle,
# reconstruct the rootfs and assemble the GPT disk, initramfs and layer.
# Runs as linux/arm64 (native on Apple Silicon via OrbStack). Package set is
# pinned through snapshot.debian.org; desync is pinned by version + sha256.
ARG BUILDER_BASE
FROM ${BUILDER_BASE}
ARG DEBIAN_SNAPSHOT
ARG DESYNC_VERSION
ARG DESYNC_SHA256

# Debian trixie builder images do not guarantee a populated CA trust store.
# Bootstrap ca-certificates from the *signed* pinned Debian archive over HTTP,
# then switch to HTTPS. APT verifies the InRelease and package hashes during
# bootstrap; do not disable signature checks or TLS verification.
# APT 3.0.3 can loop on delayed downloads when Acquire::Retries is nonzero.
# Use bounded retries around whole operations instead of APT's retry queue.
RUN set -eux; \
    test -s /usr/share/keyrings/debian-archive-keyring.gpg; \
    rm -f /etc/apt/sources.list.d/debian.sources; \
    printf 'Acquire::Retries "0";\nAcquire::http::Pipeline-Depth "0";\nAcquire::http::Timeout "45";\nAcquire::https::Timeout "45";\n' > /etc/apt/apt.conf.d/80snapshot; \
    apt_retry() { \
        description="$1"; shift; \
        for attempt in 1 2 3; do \
            if "$@"; then return 0; fi; \
            if [ "$attempt" = 3 ]; then echo "ERROR: $description failed after 3 attempts" >&2; return 1; fi; \
            sleep "$((attempt * 5))"; \
        done; \
    }; \
    printf 'deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://snapshot.debian.org/archive/debian/%s trixie main\n' "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list; \
    apt_retry 'pinned Debian snapshot bootstrap index' apt-get -o APT::Update::Error-Mode=any update; \
    apt_retry 'pinned Debian CA bootstrap' env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates; \
    test -s /etc/ssl/certs/ca-certificates.crt; \
    printf 'deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://snapshot.debian.org/archive/debian/%s trixie main\n' "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list; \
    rm -rf /var/lib/apt/lists/*; \
    apt_retry 'pinned Debian snapshot HTTPS index' apt-get -o APT::Update::Error-Mode=any update; \
    apt_retry 'pinned Debian snapshot packages' env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl openssl jq xxd file binutils python3 \
        squashfs-tools btrfs-progs e2fsprogs dosfstools mtools gdisk fdisk util-linux \
        erofs-utils busybox-static cpio gzip zstd xz-utils rsync kmod \
        gcc make libc6-dev pkgconf \
        uuid-dev libblkid-dev zlib1g-dev; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl -fsSL -o /tmp/desync.tgz "https://github.com/folbricht/desync/releases/download/v${DESYNC_VERSION}/desync_${DESYNC_VERSION}_linux_arm64.tar.gz"; \
    echo "${DESYNC_SHA256}  /tmp/desync.tgz" | sha256sum -c -; \
    tar -xzf /tmp/desync.tgz -C /usr/local/bin desync; \
    rm /tmp/desync.tgz; \
    desync --help >/dev/null
