#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TOOLS="$ROOT/tools"
DOWNLOADS="$ROOT/downloads"

APKTOOL_VERSION="3.0.2"
APKTOOL_URL="https://github.com/iBotPeaches/Apktool/releases/download/v${APKTOOL_VERSION}/apktool_${APKTOOL_VERSION}.jar"
BUILD_TOOLS_VERSION="35"

case "$(uname -s)" in
  Darwin)
    OS_NAME="mac"
    BUILD_TOOLS_OS="macosx"
    ;;
  Linux)
    OS_NAME="linux"
    BUILD_TOOLS_OS="linux"
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac
case "$(uname -m)" in
  arm64|aarch64) JRE_ARCH="aarch64" ;;
  *) JRE_ARCH="x64" ;;
esac
JRE_URL="https://api.adoptium.net/v3/binary/latest/21/ga/${OS_NAME}/${JRE_ARCH}/jre/hotspot/normal/eclipse"
BUILD_TOOLS_URL="https://dl.google.com/android/repository/build-tools_r${BUILD_TOOLS_VERSION}_${BUILD_TOOLS_OS}.zip"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

mkdir -p "$DOWNLOADS" "$TOOLS/apktool" "$TOOLS/android-build-tools" "$TOOLS/jre" "$ROOT/bin"
chmod -R u+w "$TOOLS/apktool" "$TOOLS/android-build-tools" "$TOOLS/jre" "$ROOT/bin" 2>/dev/null || true

echo "Downloading Apktool ${APKTOOL_VERSION}..."
curl -L --fail --retry 3 -o "$TOOLS/apktool/apktool.jar" "$APKTOOL_URL"

echo "Downloading Temurin JRE 21..."
curl -L --fail --retry 3 -o "$DOWNLOADS/temurin-jre21-${OS_NAME}-${JRE_ARCH}.tar.gz" "$JRE_URL"
mkdir -p "$DOWNLOADS/jre-extract"
tar -xzf "$DOWNLOADS/temurin-jre21-${OS_NAME}-${JRE_ARCH}.tar.gz" -C "$DOWNLOADS/jre-extract"
JRE_DIR="$(find "$DOWNLOADS/jre-extract" -maxdepth 1 -type d -name 'jdk-*' | head -n 1)"
if [ -z "$JRE_DIR" ]; then
  echo "Extracted JRE directory was not found" >&2
  exit 1
fi
# macOS Temurin archives nest the runtime under Contents/Home.
if [ -d "$JRE_DIR/Contents/Home" ]; then
  JRE_DIR="$JRE_DIR/Contents/Home"
fi
cp -R "$JRE_DIR/." "$TOOLS/jre/"

echo "Downloading Android Build Tools ${BUILD_TOOLS_VERSION}..."
curl -L --fail --retry 3 -o "$DOWNLOADS/build-tools-r${BUILD_TOOLS_VERSION}-${BUILD_TOOLS_OS}.zip" "$BUILD_TOOLS_URL"
mkdir -p "$DOWNLOADS/build-tools-extract"
unzip -oq "$DOWNLOADS/build-tools-r${BUILD_TOOLS_VERSION}-${BUILD_TOOLS_OS}.zip" -d "$DOWNLOADS/build-tools-extract"
BUILD_TOOLS_DIR="$(find "$DOWNLOADS/build-tools-extract" -maxdepth 1 -type d \( -name '[0-9]*' -o -name 'android-*' \) | head -n 1)"
if [ -z "$BUILD_TOOLS_DIR" ]; then
  echo "Extracted Android Build Tools directory was not found" >&2
  exit 1
fi
cp -R "$BUILD_TOOLS_DIR/." "$TOOLS/android-build-tools/"

if [ "$OS_NAME" = "linux" ]; then
  echo "Downloading appimagetool..."
  curl -L --fail --retry 3 -o "$ROOT/bin/appimagetool-x86_64.AppImage" "$APPIMAGETOOL_URL"
  chmod +x "$ROOT/bin/appimagetool-x86_64.AppImage"
fi

chmod +x "$TOOLS/jre/bin/java" "$TOOLS/jre/bin/keytool" "$TOOLS/android-build-tools/aapt" "$TOOLS/android-build-tools/aapt2" "$TOOLS/android-build-tools/zipalign" "$TOOLS/android-build-tools/apksigner"
export PATH="$TOOLS/jre/bin:$TOOLS/android-build-tools:$PATH"
export JAVA_HOME="$TOOLS/jre"

if [ ! -f "$TOOLS/debug.keystore" ]; then
  echo "Generating bundled debug keystore..."
  "$TOOLS/jre/bin/keytool" -genkeypair -v \
    -keystore "$TOOLS/debug.keystore" \
    -storepass android \
    -keypass android \
    -alias androiddebugkey \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US"
fi

echo "Detected versions:"
"$TOOLS/jre/bin/java" -version
"$TOOLS/jre/bin/keytool" -help >/dev/null 2>&1 || true
"$TOOLS/jre/bin/java" -jar "$TOOLS/apktool/apktool.jar" --version
file "$TOOLS/android-build-tools/zipalign"
"$TOOLS/android-build-tools/apksigner" --version

echo "Tools are ready in: $TOOLS"
if [ "$OS_NAME" = "linux" ]; then
  echo "appimagetool is ready in: $ROOT/bin/appimagetool-x86_64.AppImage"
fi
