/* Native relocatable Krita launcher for Homebrew SDL2-compat's SDL3 dlopen. */
#include <mach-o/dyld.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], lib[PATH_MAX], krita[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) {
        fputs("cfpaint: executable path too long\n", stderr);
        return 127;
    }
    char *slash = strrchr(executable, '/');
    if (!slash) return 127;
    *slash = '\0';
    if (snprintf(lib, sizeof(lib), "%s/../lib/libSDL3.0.dylib", executable) >= sizeof(lib) ||
        snprintf(krita, sizeof(krita), "%s/krita-real", executable) >= sizeof(krita)) return 127;
    if (access(lib, R_OK) != 0 || access(krita, X_OK) != 0) {
        perror("cfpaint: bundled SDL3 or Krita missing");
        return 127;
    }
    /* SDL2-compat loads SDL3 dynamically, so dyld cannot verify it at
       launch. Resolve it now, before Krita's SDL2 static initializer runs,
       and report the actual loader error rather than SDL2's generic alert. */
    if (setenv("SDL2COMPAT_SDL3_PATH", lib, 1) != 0) return 127;
    void *sdl3 = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
    if (!sdl3) {
        fprintf(stderr, "cfpaint: cannot load bundled SDL3 %s: %s\n",
                lib, dlerror());
        return 127;
    }
    /* SDL2-compat looks up the SDL3 runtime by path; keep the image loaded
       across exec only for validation (exec starts a fresh dyld process). */
    dlclose(sdl3);
    argv[0] = krita;
    execv(krita, argv);
    perror("cfpaint: exec Krita");
    return 127;
}
