import AppKit
import Darwin
import Foundation
import UniformTypeIdentifiers

/// Running inside "CinterForgeOS.app" (build.sh / bundle.sh): the kernel, initramfs, layer
/// disk, gvproxy, desync and Valve's RAUC CA are bundled in Contents/Resources; the SteamOS disk
/// comes from Settings > Advanced "Disk image", else the default locations below. Never copied
/// anywhere: the user picks an existing image or creates a new one (DiskCreator).
enum AppBundle {
    /// Contents/Resources when this executable lives in an .app bundle.
    static var resources: String? {
        Bundle.main.bundlePath.hasSuffix(".app") ? Bundle.main.resourcePath : nil
    }

    /// Release defaults (Info.plist `SteamacReleaseDefaults`, set by bundle.sh for the .app):
    /// SSH off and no default guest password on created disks. The dev launcher has neither.
    static var releaseDefaults: Bool {
        Bundle.main.object(forInfoDictionaryKey: "SteamacReleaseDefaults") as? Bool ?? false
    }

    static var appSupportDir: String {
        NSHomeDirectory() + "/Library/Application Support/" + LauncherSettings.defaultDomain
    }

    static var logPath: String {
        NSHomeDirectory() + "/Library/Logs/" + LauncherSettings.defaultDomain + "/steamac-vm.log"
    }

    /// Steam is the authority for the CinterForge installation, regardless of which
    /// volume the user selected for the Steam library. This also works when Xcode
    /// launches a disposable Debug .app outside that installed version directory.
    private static func steamLibraryCandidates() -> [String] {
        let env = ProcessInfo.processInfo.environment
        let primary = NSHomeDirectory() + "/Library/Application Support/Steam/steamapps"
        var roots = [String]()
        if let override = env["CINTER_STEAM_APPS_ROOT"], !override.isEmpty {
            roots.append((override as NSString).expandingTildeInPath)
        } else {
            roots.append(primary)
            let vdf = primary + "/libraryfolders.vdf"
            if let contents = try? String(contentsOfFile: vdf, encoding: .utf8),
               let pattern = try? NSRegularExpression(pattern: #""path"\s*"((?:\\.|[^"\\])*)""#, options: [.caseInsensitive]) {
                let range = NSRange(contents.startIndex..<contents.endIndex, in: contents)
                for match in pattern.matches(in: contents, range: range) {
                    guard let capture = Range(match.range(at: 1), in: contents) else { continue }
                    let escaped = String(contents[capture])
                    let location = escaped.replacingOccurrences(of: "\\\\", with: "\\")
                        .replacingOccurrences(of: "\\\"", with: "\"")
                    if (location as NSString).isAbsolutePath {
                        roots.append(location + "/steamapps")
                    }
                }
            }
        }
        var seen = Set<String>()
        return roots.map { URL(fileURLWithPath: $0).standardizedFileURL.path }
            .filter { seen.insert($0).inserted }
            .filter { ($0 as NSString).lastPathComponent == "steamapps" }
            .filter { FileManager.default.fileExists(atPath: $0 + "/common/CinterForge") }
    }

    /// First try the authoritative installed Steam library's versioned disk.
    /// The currently running bundle may instead be Xcode's disposable Debug copy.
    static func defaultDiskCandidates() -> [String] {
        let installedVersion = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        let resolvedBundle = URL(fileURLWithPath: Bundle.main.bundlePath).resolvingSymlinksInPath().path
        let resolvedVersion = (resolvedBundle as NSString).deletingLastPathComponent
        var candidates = [resolvedVersion + "/SteamOS/steamos.img"]
        if installedVersion != resolvedVersion {
            candidates.append(installedVersion + "/SteamOS/steamos.img")
        }
        for steamapps in steamLibraryCandidates() {
            let product = steamapps + "/common/CinterForge/Applications/macOS/CinterForgeOS"
            // The installed product's CURRENT_VERSION identifies the active version.
            // In a direct Xcode build, the current managed version remains authoritative.
            let marker = product + "/CURRENT_VERSION"
            let version = (try? String(contentsOfFile: marker, encoding: .utf8))?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let version, !version.isEmpty, !version.contains("/"), version != ".", version != ".." {
                candidates.append(product + "/" + version + "/SteamOS/steamos.img")
            }
            // Older installations have no marker. Prefer known existing version
            // directories rather than inventing a new destination or disk.
            if let entries = try? FileManager.default.contentsOfDirectory(atPath: product) {
                for version in entries.sorted().reversed()
                    where version.first?.isNumber == true && version != "." && version != ".." {
                    candidates.append(product + "/" + version + "/SteamOS/steamos.img")
                }
            }
        }
        var seen = Set<String>()
        return candidates.filter { seen.insert($0).inserted }
    }

    static func defaultDisk() -> String? {
        defaultDiskCandidates().first(where: isDiskImage)
    }

    /// A disk image libkrun can attach: a readable file, not a directory. The open panels let a
    /// package through as a file: two 1.9 users chose an app (/System/Applications/Apps.app,
    /// Steam.app) as their disk, and every start failed in krun_start_enter with -22 (STEAMAC-15).
    static func isDiskImage(_ path: String) -> Bool {
        var directory: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &directory) && !directory.boolValue
            && FileManager.default.isReadableFile(atPath: path)
    }

    /// "Choose a SteamOS disk image" panels (first run, Settings > Advanced): files only, no
    /// packages (public.data excludes bundles and folders; a raw image of any name conforms).
    static func configureDiskPanel(_ panel: NSOpenPanel) {
        panel.title = String(localized: "Choose a SteamOS disk image")
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.treatsFilePackagesAsDirectories = false
        panel.allowedContentTypes = [.data]
    }

    /// The disk the next start uses (Settings value or default), nil if none is usable.
    static func configuredDisk(_ settings: LauncherSettings) -> String? {
        // The installed GUI test has already resolved and validated its disk.
        // Use that exact path rather than guessing which Steam library is active.
        if let requested = ProcessInfo.processInfo.environment["CINTER_STEAMOS_DISK"], !requested.isEmpty {
            if isDiskImage(requested) { return requested }
            log("configured SteamOS disk is missing or unreadable: \(requested)")
            return nil
        }
        if !settings.diskImage.isEmpty {
            if isDiskImage(settings.diskImage) { return settings.diskImage }
            // A previous Setup used the unversioned SteamOS directory. If Setup
            // moved that exact managed disk, respect the new versioned location.
            if settings.diskImage == appSupportDir + "/SteamOS/steamos.img" ||
               settings.diskImage == appSupportDir + "/2026/steamos.img" ||
               settings.diskImage == (Bundle.main.bundlePath as NSString).deletingLastPathComponent + "/steamos.img" {
                return defaultDisk()
            }
            return nil
        }
        return defaultDisk()
    }

    /// Bundle defaults for what the command line did not give.
    static func fill(_ o: inout Options, settings: LauncherSettings, overrides: inout [LauncherSettings.Key: String]) {
        guard let res = resources else { return }
        if o.kernel.isEmpty { o.kernel = res + "/Image" }
        if o.initrd == nil && !o.explicit.contains("--kernel") { o.initrd = res + "/initramfs.cpio.gz" }
        if o.gvproxyPath == nil, FileManager.default.isExecutableFile(atPath: res + "/gvproxy") {
            o.gvproxyPath = res + "/gvproxy"
        }
        // SIGUSR1 frame dumps: the app's working directory is / (not writable).
        if !o.explicit.contains("--frame-dump") {
            let dir = (logPath as NSString).deletingLastPathComponent
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            o.frameDumpPath = dir + "/steamac-frame.png"
        }
        guard o.disks.isEmpty else { return }
        guard let disk = configuredDisk(settings) else {
            o.needsDisk = true
            return
        }
        o.disks = [DiskSpec(path: disk, readOnly: false), DiskSpec(path: res + "/steamac-layer.img", readOnly: true)]
    }

    /// Finder / `open` launch: stdout and stderr are /dev/null, so the guest console and the
    /// launcher log are appended to ~/Library/Logs/es.fxgam.steamac/steamac-vm.log instead
    /// (rotated at 64 MiB). Running Contents/MacOS/cfos-vm from a shell keeps the terminal/pipes.
    static var outputDiscarded: Bool {
        var null = stat(), out = stat(), err = stat()
        guard stat("/dev/null", &null) == 0, fstat(STDOUT_FILENO, &out) == 0, fstat(STDERR_FILENO, &err) == 0 else { return false }
        return (out.st_rdev, err.st_rdev) == (null.st_rdev, null.st_rdev)
            && (out.st_mode & S_IFMT) == S_IFCHR && (err.st_mode & S_IFMT) == S_IFCHR
    }

    static func redirectOutputIfLaunchedFromFinder() {
        guard resources != nil, outputDiscarded else { return }
        let path = logPath
        try? FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent,
                                                 withIntermediateDirectories: true)
        var st = stat()
        if stat(path, &st) == 0, st.st_size > 64 << 20 {
            rename(path, (path as NSString).deletingPathExtension + ".old.log")
        }
        let fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
        guard fd >= 0 else { return }
        dup2(fd, STDOUT_FILENO)
        dup2(fd, STDERR_FILENO)
        close(fd)
        let stamp = ISO8601DateFormatter().string(from: Date())
        log("---- \(stamp) CinterForgeOS started (pid \(getpid()), \(Bundle.main.bundlePath))")
    }

    /// A launch error the user must see: an alert when launched from Finder / `open` (the log is not
    /// on screen; a shell launch has the message on stderr).
    @MainActor
    static func alertIfLaunchedFromFinder(_ title: LocalizedStringResource, _ message: String) {
        guard resources != nil && getppid() == 1 else { return }
        let alert = NSAlert()
        alert.messageText = String(localized: title)
        alert.informativeText = message
        NSApplication.shared.setActivationPolicy(.regular)
        NSApp.activate()
        alert.runModal()
    }
}

/// First start of the app without a usable disk image: explain, then create a new disk
/// (CreateDiskWindowController, no Docker) or let the user pick an existing image (Settings >
/// Advanced "Disk image"); the supervisor then boots it.
enum FirstRun {
    static func run(settings: LauncherSettings) -> Never {
        let app = NSApplication.shared
        log("first run: no usable disk image (\(settings.diskImage.isEmpty ? "none found" : settings.diskImage)); asking the user")
        app.setActivationPolicy(.regular)
        MainMenu.installMinimal()
        app.activate()
        let alert = makeAlert(settings: settings)
        while true {
            let choice = alert.runModal()
            var chosen: String?
            if choice == .alertFirstButtonReturn {
                chosen = CreateDiskWindowController.runModal(settings: settings)
            } else if choice == .alertSecondButtonReturn {
                let panel = NSOpenPanel()
                AppBundle.configureDiskPanel(panel)
                panel.message = String(localized: "Raw GPT disk image (e.g. steamos.img). It stays where it is.")
                if panel.runModal() == .OK, let url = panel.url, AppBundle.isDiskImage(url.path) {
                    chosen = url.path
                }
            } else {
                log("first run: no disk chosen; quitting")
                exit(0)
            }
            guard let path = chosen else { continue }
            settings.diskImage = path
            log("first run: disk image \(path)")
            // Tell the supervisor to start the VM now (same marker as a guest reboot).
            if let dir = Supervisor.runDir {
                FileManager.default.createFile(atPath: Supervisor.rebootMarker(dir), contents: Data(Supervisor.firstRunMarker.utf8))
            }
            exit(0)
        }
    }

    /// The first-run alert (also captured by --selftest-settings).
    static func makeAlert(settings: LauncherSettings) -> NSAlert {
        let alert = NSAlert()
        alert.messageText = String(localized: "No SteamOS disk image")
        let looked = AppBundle.defaultDiskCandidates().map { "• " + ($0 as NSString).abbreviatingWithTildeInPath }
        var info = String(localized: "CinterForgeOS needs a SteamOS disk image (steamos.img, ~87 GB sparse).\n\n")
        if !settings.diskImage.isEmpty {
            let path = (settings.diskImage as NSString).abbreviatingWithTildeInPath
            info += String(localized: "The image chosen in Settings is not readable:\n\(path)\n\n")
        }
        let paths = looked.joined(separator: "\n")
        info += String(localized: "Looked in:\n\(paths)\n\n")
        info += String(localized: "Create a new one: the official SteamOS image is downloaded from Valve and verified (about 4.5 GB; the disk uses ~10 GB on your Mac at first). Or choose an existing image, which is used in place and never copied.")
        alert.informativeText = info
        alert.accessoryView = SteamClientPicker.alertAccessoryView(settings: settings)
        alert.addButton(withTitle: String(localized: "Create New Disk…"))
        alert.addButton(withTitle: String(localized: "Use Existing Disk…"))
        alert.addButton(withTitle: String(localized: "Quit"))
        return alert
    }
}
