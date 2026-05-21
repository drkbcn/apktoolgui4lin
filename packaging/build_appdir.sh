#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APPDIR="$ROOT/dist/AppDir"
SHARE="$APPDIR/usr/share/apktoolgui4lin"

rm -rf "$APPDIR"
mkdir -p "$SHARE" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"

cp "$ROOT/apktoolgui4lin_gtk.py" "$SHARE/"
cp "$ROOT/README.md" "$SHARE/"
cp -R "$ROOT/assets" "$SHARE/assets"
cp -R "$ROOT/tools" "$SHARE/tools"

cp "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
cp "$ROOT/packaging/com.github.apktoolgui4lin.desktop" "$APPDIR/com.github.apktoolgui4lin.desktop"
cp "$ROOT/packaging/com.github.apktoolgui4lin.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT/packaging/com.github.apktoolgui4lin.svg" "$APPDIR/com.github.apktoolgui4lin.svg"
cp "$ROOT/packaging/com.github.apktoolgui4lin.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"

chmod +x "$APPDIR/AppRun"
chmod +x "$SHARE/apktoolgui4lin_gtk.py"
chmod +x "$SHARE/tools/jre/bin/java" "$SHARE/tools/jre/bin/keytool" "$SHARE/tools/android-build-tools/aapt" "$SHARE/tools/android-build-tools/aapt2" "$SHARE/tools/android-build-tools/zipalign" "$SHARE/tools/android-build-tools/apksigner"

echo "AppDir created at: $APPDIR"
echo "To generate the AppImage, install/download appimagetool and run:"
echo "  appimagetool $APPDIR"
