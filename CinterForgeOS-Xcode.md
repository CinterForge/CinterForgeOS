# Native CinterForgeOS macOS application

The canonical application target is `CinterForgeOS.xcodeproj` (`CinterForgeOS` scheme).
Xcode owns Swift/C compilation, icon and localization compilation, declared
`Copy Bundle Resources`, splash `Copy Files`, each signed native dylib in
`Copy Files → Frameworks` with `Code Sign on Copy`, application entitlements,
and macOS application signing. Do not run `bundle.sh` or use SwiftPM output
as the distributable app.

`./Hope setup cinterforge-os mac` provisions the build tools/native dependencies.
Guest image generation is separate because Xcode cannot natively produce the
SteamOS EROFS artifacts. `./Hope build cinterforge-os mac Release` invokes
native `xcodebuild` after guest preparation and copies the *unchanged* completed
signed app to the versioned Build Output. Stage must not modify the .app.

The only retained application-target shell phase exports the KDE-ready test
hook outside the app, to the external Built/Test directory. It is not used
for bundle resource packing, signing, or staging.
