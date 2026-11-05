#!/bin/sh
# CinterForgeOS Cinter commands for the SteamOS/KDE Konsole.
#
# This file is installed once into the host Steam library. After installation,
# that copy belongs to the user: editing it changes Konsole commands without
# rebuilding or restarting CinterForgeOS.
#
# These commands inspect original upstream applications. They do not invoke
# the macOS Cinter build/stage tools or install CinterForge-branded applications.

# Stop when a command fails or an undefined variable is used.
set -eu

steam_applications_directory=/run/steamapps/common/CinterForge/Applications
installer_directory="$steam_applications_directory/UnforkedInstallers"
# Only this dispatcher is the CFOS guest's entry point. Commands run from
# macOS-editable scripts stored in the existing Steam library share.
test_scripts_directory="$steam_applications_directory/Test/Scripts"
windows_inspector="$test_scripts_directory/inspect/inspect-unforked-windows.py"
runtime_tester="$test_scripts_directory/test/test-x86-runtime.sh"
linux_runner="$test_scripts_directory/run/run-linux-amd64.sh"
baseline_collector="$test_scripts_directory/baseline/baseline-environments.py"
upstream_installer="$test_scripts_directory/installl/install-unforked.sh"
upstream_launcher="$test_scripts_directory/launch/launch-unforked.sh"

# Other test scripts use this same directory when calling their helpers.
export CINTERFORGE_TEST_SCRIPTS_DIRECTORY="$test_scripts_directory"


require_test_script() {
    script_path=$1

    if [ ! -f "$script_path" ]; then
        echo "[Cinter] Test script missing: $script_path" >&2
        echo '[Cinter] Install the test scripts into the persistent Steam library.' >&2
        exit 1
    fi

    if [ ! -x "$script_path" ]; then
        echo "[Cinter] Test script is not executable: $script_path" >&2
        exit 1
    fi
}


show_help() {
    cat <<'HELP'
CinterForgeOS / Konsole Cinter — original upstream application inspection

  Cinter help
  Cinter baseline [linux|win|all] # Save FEX/Wine baselines in steamapps
  Cinter installers [win|mac|linux|all]
  Cinter unforked install <Linux|Windows> <app> # Original publisher only
  Cinter unforked launch <Linux|Windows> <app>  # Installed upstream executable
  Cinter test runtime [--gui]        # FEX + Wine; optional Notepad GUI
  Cinter test linux <absolute-executable> [arguments...]
  Cinter test <app> <linux|win> Release # Launch installed branded GUI on KDE
  Cinter inspect <app> win before
  Cinter inspect <app> win install <publisher-installer.exe|.msi>
  Cinter inspect <app> win after
  Cinter inspect <app> win launch <absolute-Windows-exe-path>
  Cinter inspect <app> win launchafter

Baselines: /run/steamapps/common/CinterForge/Applications/Logs/EnvironmentBaselines/{Linux,Windows}/Baseline.json
Run from your home directory in Konsole. Windows inspection reports use the host's
writable /run/steamapps/common/CinterForge/Applications/Logs/UnforkedInspection/Windows.
Publisher downloads are shared read-only at /run/steamapps/common/CinterForge/Applications/UnforkedInstallers.
Installation uses an isolated per-application Wine prefix under your guest home.
No CinterForge-built application distributions are installed by these commands.
Linux upstream installation snapshots run in the separate Ubuntu AMD64 reference
environment, not in this SteamOS/KDE guest.
HELP
}


show_usage_error() {
    show_help >&2
    exit 2
}


# Replace this dispatcher process with the tool that performs the work.
# "exec" preserves the tool's exit code and avoids an extra shell process.
collect_environment_baseline() {
    if [ "$#" -gt 1 ]; then
        show_usage_error
    fi

    requested_platform=${1:-all}
    require_test_script "$baseline_collector"
    exec "$baseline_collector" "$requested_platform"
}


# Convert a user-facing platform name to its on-disk directory name.
# The special "all" value deliberately maintains the existing display order.
resolve_installer_platforms() {
    requested_platform=$1

    case "$requested_platform" in
        win|windows|Windows)
            installer_platforms=Windows
            ;;

        mac|macOS)
            installer_platforms=macOS
            ;;

        linux|Linux)
            installer_platforms=Linux
            ;;

        all)
            installer_platforms='macOS Linux Windows'
            ;;

        *)
            echo "Unknown platform: $requested_platform" >&2
            exit 2
            ;;
    esac
}


show_installer_files() {
    if [ "$#" -gt 1 ]; then
        show_usage_error
    fi

    resolve_installer_platforms "${1:-all}"

    # Platform names contain no spaces, so this list can be iterated safely.
    for installer_platform in $installer_platforms; do
        platform_directory="$installer_directory/$installer_platform"
        echo "[unforked] $installer_platform installers: $platform_directory"

        if [ ! -d "$platform_directory" ]; then
            echo '  Not downloaded/mount unavailable'
            continue
        fi

        # Ignore unfinished downloads and keep the listing alphabetical.
        find "$platform_directory" -maxdepth 2 -type f ! -name '*.part' -print | sort
    done
}


run_runtime_test() {
    # Pass every remaining argument unchanged to the FEX/Wine test utility.
    require_test_script "$runtime_tester"
    exec "$runtime_tester" "$@"
}


run_linux_test() {
    if [ "$#" -lt 1 ]; then
        show_usage_error
    fi

    # The first argument is the absolute executable path; later arguments
    # belong to that executable and must retain their original quoting.
    require_test_script "$linux_runner"
    exec "$linux_runner" "$@"
}


# Launch CinterForge-built GUI applications using the existing installed-app
# launcher. Do not substitute upstream applications or invent executable paths.
run_installed_app_test() {
    application=$1
    shift
    if [ "$#" -ne 2 ]; then
        echo 'Usage: Cinter test <app> <linux|win> Release' >&2
        exit 2
    fi
    requested_platform=$1
    configuration=$2
    case "$requested_platform" in
        linux|Linux) platform=Linux ;;
        win|windows|Windows) platform=Windows ;;
        *) echo "Unsupported installed-app test platform: $requested_platform" >&2; exit 2 ;;
    esac
    if [ "$configuration" != Release ]; then
        echo "Installed GUI test requires Release (received: $configuration)" >&2
        exit 2
    fi
    case "$application" in
        cfaudio) app=Ardour ;; cf3d) app=Blender ;; cfimage) app=GIMP ;;
        cfpaint) app=Krita ;; cfvector) app=Inkscape ;;
        cfdocument) app=Scribus ;; cfvideo) app=Kdenlive ;;
        rapidraw) app=cfdocument ;;
        *) echo "Unsupported installed-app test target: $application" >&2; exit 2 ;;
    esac
    # External Cinter resolves the already-staged launcher; it performs version,
    # executable and runtime checks and exits nonzero on missing installations.
    # Audio GUI tests use the persistent diagnostic launcher, including failures
    # before Proton/FEX starts. Other apps retain the generic launcher.
    if [ "$app" = Ardour ]; then
        case "$platform" in
            Linux) exec Cinter launch-with-log linux ;;
            Windows) exec Cinter launch-with-log windows ;;
        esac
    fi
    exec Cinter launch-cinterforge "$platform" "$app"
}


run_test_command() {
    if [ "$#" -lt 1 ]; then
        show_usage_error
    fi

    test_target=$1
    shift  # Remove the test type; the rest belongs to the chosen test tool.

    case "$test_target" in
        runtime)
            run_runtime_test "$@"
            ;;

        linux)
            run_linux_test "$@"
            ;;

        cfaudio|cf3d|cfimage|cfpaint|cfvector|cfdocument|cfvideo|rapidraw)
            run_installed_app_test "$test_target" "$@"
            ;;

        *)
            echo "Unsupported test target: $test_target" >&2
            exit 2
            ;;
    esac
}


# "before", "after", and "launchafter" take no extra arguments.
run_windows_snapshot() {
    application_name=$1
    snapshot_phase=$2
    shift 2

    if [ "$#" -ne 0 ]; then
        show_usage_error
    fi

    exec "$windows_inspector" "$application_name" "$snapshot_phase"
}


# "install" requires exactly one path to an upstream installer.
run_windows_install() {
    application_name=$1
    shift

    if [ "$#" -ne 1 ]; then
        show_usage_error
    fi

    installer_path=$1
    exec "$windows_inspector" "$application_name" install --installer "$installer_path"
}


# "launch" requires exactly one path to a Windows executable.
run_windows_launch() {
    application_name=$1
    shift

    if [ "$#" -ne 1 ]; then
        show_usage_error
    fi

    executable_path=$1
    exec "$windows_inspector" "$application_name" launch --exe "$executable_path"
}


inspect_windows_application() {
    application_name=$1
    inspection_phase=$2
    shift 2

    require_test_script "$windows_inspector"

    case "$inspection_phase" in
        before|after|launchafter)
            run_windows_snapshot "$application_name" "$inspection_phase" "$@"
            ;;

        install)
            run_windows_install "$application_name" "$@"
            ;;

        launch)
            run_windows_launch "$application_name" "$@"
            ;;

        *)
            echo "Unsupported Windows phase: $inspection_phase" >&2
            exit 2
            ;;
    esac
}


inspect_application() {
    if [ "$#" -lt 3 ]; then
        show_usage_error
    fi

    application_name=$1
    inspection_platform=$2
    inspection_phase=$3
    shift 3  # Leave only the optional installer/executable argument.

    case "$inspection_platform" in
        win|Windows|windows)
            inspect_windows_application "$application_name" "$inspection_phase" "$@"
            ;;

        *)
            echo 'This guest currently supports Windows install snapshots; Linux reference snapshots run in Ubuntu AMD64.' >&2
            exit 2
            ;;
    esac
}


main() {
    if [ "$#" -eq 0 ]; then
        show_help
        return 0
    fi

    command_name=$1
    shift  # Remove the command name; pass its arguments to its handler.

    case "$command_name" in
        help|-h|--help)
            show_help
            ;;

        unforked)
            [ "$#" -eq 3 ] || show_usage_error
            upstream_action=$1
            shift
            case "$upstream_action" in
                install) require_test_script "$upstream_installer"; exec "$upstream_installer" "$@" ;;
                launch) require_test_script "$upstream_launcher"; exec "$upstream_launcher" "$@" ;;
                *) show_usage_error ;;
            esac
            ;;

        baseline)
            collect_environment_baseline "$@"
            ;;

        installers)
            show_installer_files "$@"
            ;;

        test)
            run_test_command "$@"
            ;;

        inspect)
            inspect_application "$@"
            ;;

        *)
            echo "Unknown Cinter command: $command_name" >&2
            show_usage_error
            ;;
    esac
}


main "$@"
