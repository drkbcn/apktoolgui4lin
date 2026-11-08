# APKTool GUI 4 Linux

> A native Linux desktop GUI for Apktool workflows: decode, rebuild, align, sign, verify, inspect, and manage APK framework files.

APKTool GUI 4 Linux is a GTK desktop application inspired by the Windows APKTool GUI experience, but built for Ubuntu/Linux workflows. It wraps proven Android reverse-engineering and packaging tools behind a practical interface, with a strong focus on portability through bundled binaries.

The core workflow is simple:

```text
APK -> decode -> edit project -> build -> zipalign -> sign -> verify
```

## What It Is Based On

This project does not reimplement APK decoding or signing logic. It orchestrates established tools:

- [iBotPeaches/Apktool](https://github.com/iBotPeaches/Apktool) for decoding and rebuilding APK projects.
- Android SDK Build Tools for `aapt`, `aapt2`, `zipalign`, and `apksigner`.
- Eclipse Temurin/OpenJDK runtime for Java-based tooling.
- AppImage packaging for portable Linux distribution.

The goal is to provide a comfortable Linux desktop frontend around those tools, similar in spirit to Windows APKTool GUI, while keeping the underlying commands transparent in the log panel.

## Features

- Native GTK desktop UI for Linux.
- Decode APKs with Apktool.
- Rebuild decoded projects.
- Build, align, and sign in one pipeline.
- Create and use debug keystores.
- Verify APK signatures.
- Visual APK information panel with package name, label, version, SDK data, ABIs, densities, and permissions.
- Raw APK inspection using `aapt`/`aapt2` dumps.
- APKPure search integration with package/name matching, full version list, and direct downloads (best effort).
- Framework management:
  - install framework
  - list frameworks
  - clean frameworks
  - publicize resources
- Bundled tool support through `tools/`.
- Tool availability and version checks.
- Live command output log.
- Automatic output naming:
  - `MyApp.apk` -> `MyApp_src`
  - `MyApp_src` -> `MyApp-unsigned.apk`, `MyApp-aligned.apk`, `MyApp-signed.apk`

## Screens And Workflows

### Decode

Select an APK and the app suggests an output folder automatically. Decode options include:

- force overwrite
- no resources
- no smali
- all sources
- no assets
- no debug info
- only manifest
- keep broken resources
- match original
- ignore raw values
- framework path/tag
- shared library
- parallel jobs
- resource resolve mode

### APK Info

Select an APK to inspect it without decoding. The visual summary shows the most useful metadata first, while the action buttons expose detailed dumps:

- badging
- permissions
- manifest XML tree
- resources
- configurations
- archive contents

### APK Search

Search APKPure by app name or package name from inside the app. Results are shown as cards with:

- app name, package name, developer and category
- size and package type (`APK` or `XAPK`)
- latest version, plus a `Load all versions` button to pick an older release
- match score and match reason
- `Open in APKPure` link
- direct download with a progress bar

The integration talks to APKPure's unofficial mobile API (`api.pureapk.com`) with plain Python (`apkpure.py`, standard library only), because the APKPure website sits behind a Cloudflare challenge that blocks automated clients. Many apps are served as `XAPK` bundles (a zip of split APKs); unzip them and use the base `.apk` with Apktool. If APKPure changes the API, use `Open in APKPure` and download from the browser.

> APKCombo, used by earlier versions, shut down and is no longer supported.

### Build

Select a decoded project folder and the app suggests output filenames. Build options include:

- selectable AAPT backend: `aapt2`, legacy `aapt`, or custom path
- force rebuild
- debuggable manifest patch
- copy original manifest/META-INF
- network security config
- no APK repack
- no crunch
- framework path
- shared libraries
- parallel jobs

You can either build only, or run `Build + align + sign` in one go.

### Align / Sign

Use zipalign and apksigner from one screen. Options include:

- alignment size
- native library page alignment
- Zopfli recompression
- V1/V2/V3/V4 signing scheme selection
- min/max SDK
- verity
- file size alignment
- preserve existing alignment
- signature verification with certificate output

### Frameworks

Manage Apktool framework files from the GUI:

- install framework APK
- list installed frameworks
- clean frameworks
- publicize `resources.arsc`

### Tools

The app checks and displays availability and versions for:

- Java
- Apktool
- aapt
- aapt2
- zipalign
- apksigner
- keytool

## Portable Tool Layout

The app prefers bundled tools when `Use tools bundled with the app` is enabled.

Expected layout:

```text
tools/
  apktool/
    apktool.jar
  android-build-tools/
    aapt
    aapt2
    zipalign
    apksigner
    lib/
    lib64/
  debug.keystore
  jre/
    bin/
      java
      keytool
    lib/
```

This repository workspace is prepared for:

- Apktool `3.0.2`
- Eclipse Temurin JRE `21.0.11+10`
- Android Build Tools `35`
- appimagetool continuous build

Use the helper script to download and arrange tools:

```bash
chmod +x scripts/download_tools.sh
./scripts/download_tools.sh
```

## Running From Source

Requirements for running the GTK frontend from source:

- Linux desktop session
- Python 3
- PyGObject with GTK 4

Run:

```bash
python3 apktoolgui4lin_gtk.py
```

Or:

```bash
chmod +x apktoolgui4lin_gtk.py
./apktoolgui4lin_gtk.py
```

## Building The AppImage

Create the AppDir:

```bash
chmod +x packaging/build_appdir.sh
./packaging/build_appdir.sh
```

Generate the AppImage with a system `appimagetool`:

```bash
appimagetool dist/AppDir
```

Or use the downloaded appimagetool:

```bash
ARCH=x86_64 ./bin/appimagetool-x86_64.AppImage \
  dist/AppDir \
  dist/APKTool_GUI_4_Linux-x86_64.AppImage
```

Current generated AppImage:

```bash
sha256sum dist/APKTool_GUI_4_Linux-x86_64.AppImage
```

## Running On macOS (experimental)

The GUI is plain GTK 4 + PyGObject, so it can run on macOS through Homebrew. It is not packaged as an `.app`; run it from source:

```bash
brew install gtk4 pygobject3 adwaita-icon-theme
./scripts/download_tools.sh        # fetches macOS JRE, Apktool and Build Tools into tools/
python3 apktoolgui4lin_gtk.py
```

`download_tools.sh` detects macOS and Apple Silicon/Intel automatically. On Apple Silicon the Android Build Tools (`aapt`, `aapt2`, `zipalign`) are x86_64 binaries and need Rosetta 2. The AppImage packaging is Linux-only.

## Project Structure

```text
apktoolgui4lin_gtk.py        Main GTK application
apkpure.py                   APKPure client (stdlib only, no GTK)
tools/                       Bundled runtime tools
scripts/download_tools.sh    Download helper for portable tools
packaging/                   AppImage/AppDir files
dist/                        Generated AppDir and AppImage output
bin/                         Downloaded appimagetool
```

## Notes On Portability

The generated AppImage includes Java, Apktool, and Android Build Tools. The current UI still relies on the host Ubuntu Python/GTK runtime. For complete zero-dependency portability, the next packaging milestone is to bundle Python+GTK or migrate the UI to a compiled desktop stack.

When running from an AppImage, Android Build Tools are copied to a user-writable executable cache before being passed to Apktool. This keeps sibling libraries such as `libc++.so` available and avoids failures caused by Apktool trying to adjust executable permissions inside the read-only AppImage mount.

The default debug signing keystore is bundled as `tools/debug.keystore` so align/sign workflows remain portable inside the AppImage without relying on `~/.android/debug.keystore`. You can still select any other keystore manually when signing.

Some APKs contain resources that `aapt2` rejects, such as PNG files with invalid signatures or resource names that are invalid for modern aapt2 compilation. In those cases, switch `Build -> AAPT backend` to `aapt (legacy fallback)` and rebuild.

## Roadmap

- Bundle the Python/GTK runtime for a fully self-contained AppImage.
- Add APK/APKS/XAPK split handling.
- Add ADB install/pull helpers.
- Improve APK Info and APKPure Search with icons and manifest component summaries.
- Add presets for common decode/build/sign workflows.
- Add project history and recent APKs.

## Legal And Safety Notes

This project is intended for legitimate APK analysis, localization, compatibility work, research, and modification of applications you own or are allowed to inspect. The app executes local commands and file paths configured through the UI, so use trusted tools and review logs when working with unknown APKs.

Apktool, Android SDK Build Tools, Java runtimes, and appimagetool are third-party projects with their own licenses and distribution terms. This project is a GUI wrapper around those tools, not a replacement for them.
