# CinterForgeOS FEX build toolchain. Host is macOS; execution is Linux/aarch64.
# Keep compiler/build dependencies out of the installed SteamOS guest.
ARG BASE=debian:trixie
FROM ${BASE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    bash ca-certificates git cmake ninja-build pkg-config ccache clang llvm lld \
    python3 python3-setuptools python3-clang \
    libssl-dev libfmt-dev libxxhash-dev libzstd-dev \
    g++-x86-64-linux-gnu gcc-x86-64-linux-gnu \
    nasm squashfs-tools squashfuse erofs-utils erofsfuse \
    libstdc++-12-dev-amd64-cross libstdc++-12-dev-i386-cross \
    libgcc-12-dev-amd64-cross libgcc-12-dev-i386-cross \
    libc6-dev-amd64-cross libc6-dev-i386-cross \
    qt6-declarative-dev qml6-module-qtquick-controls qml6-module-qtquick-dialogs \
    qml6-module-qtquick-layouts qml6-module-qtqml-workerscript \
    qml6-module-qtquick-templates qml6-module-qtquick-window \
    file binutils rsync xz-utils && rm -rf /var/lib/apt/lists/*
