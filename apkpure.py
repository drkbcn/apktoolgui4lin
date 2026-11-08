"""Minimal APKPure client (stdlib only, no GTK).

APKPure's website sits behind a Cloudflare challenge that blocks automated
clients, but the JSON-less "mobile" API used by its Android app does not. It
answers with protobuf, so this module contains a tiny wire-format decoder and
extracts only the handful of fields the GUI needs.

This is an unofficial, best-effort integration: if APKPure changes the API the
functions raise ``ApkPureError`` and the GUI falls back to opening the website.
"""
import os
import platform
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_BASE = "https://api.pureapk.com/m/v3"
WEB_BASE = "https://apkpure.com"
USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SQ3A.220705.004)"
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


class ApkPureError(Exception):
    pass


def default_abis():
    override = os.environ.get("APKPURE_ABIS")
    if override:
        return override
    # APKPure only needs a hint to pick split APK/XAPK variants; Android ABIs
    # are what its catalogue knows about, not the host CPU architecture.
    return "arm64-v8a,armeabi-v7a" if platform.machine().lower() in ("arm64", "aarch64") else "arm64-v8a,armeabi-v7a,x86_64,x86"


def _headers():
    return {
        "User-Agent": USER_AGENT,
        "x-sv": "29",
        "x-abis": default_abis(),
        "x-gp": "1",
    }


# --- protobuf wire format -------------------------------------------------

def _varint(data, pos):
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def _fields(data):
    """Decode one message level into {field: [values]} (bytes or int)."""
    out = {}
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire == 2:
            length, pos = _varint(data, pos)
            value = data[pos:pos + length]
            if len(value) != length:
                raise ValueError("truncated")
            pos += length
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError("bad wire type")
        out.setdefault(number, []).append(value)
    return out


def _text(fields, number, default=""):
    values = fields.get(number)
    if values and isinstance(values[0], bytes):
        try:
            return values[0].decode("utf-8")
        except UnicodeDecodeError:
            pass
    return default


def _sub(fields, number):
    values = fields.get(number)
    if values and isinstance(values[0], bytes):
        try:
            return _fields(values[0])
        except (ValueError, IndexError):
            return {}
    return {}


def _app_from_fields(fields):
    """Return an app dict if this message looks like an APKPure app record."""
    package = _text(fields, 4)
    if not PACKAGE_RE.match(package) or 24 not in fields:
        return None
    asset = _sub(fields, 24)
    url = _text(asset, 9)
    if not url.startswith("http"):
        return None
    icon = ""
    for entry in fields.get(27, []):
        try:
            icon = _text(_sub(_fields(entry), 1), 1)
        except (ValueError, IndexError):
            icon = ""
        if icon:
            break
    size = asset.get(4, [0])[0]
    return {
        "name": _text(fields, 1) or package,
        "package": package,
        "version_code": _text(fields, 5),
        "version": _text(fields, 6) or "Unknown",
        "description": _text(fields, 10),
        "developer": _text(fields, 13),
        "category": _text(fields, 31),
        "kind": (_text(asset, 8) or "APK").lower(),
        "size": size if isinstance(size, int) else 0,
        "sha1": _text(asset, 3),
        "date": _text(asset, 1),
        "download_url": url,
        "icon_url": icon,
    }


def _collect_apps(data, depth=0, found=None):
    found = [] if found is None else found
    try:
        fields = _fields(data)
    except (ValueError, IndexError):
        return found
    app = _app_from_fields(fields)
    if app:
        found.append(app)
        return found
    if depth > 8:
        return found
    for values in fields.values():
        for value in values:
            if isinstance(value, bytes) and len(value) > 8:
                _collect_apps(value, depth + 1, found)
    return found


# --- network --------------------------------------------------------------

def _get(path, timeout=30):
    request = Request(f"{API_BASE}/{path}", headers=_headers())
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise ApkPureError(f"APKPure API returned HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        raise ApkPureError(f"Could not reach APKPure: {exc}") from exc


def search(query):
    """Search by app name or package name. Returns a list of app dicts."""
    data = _get(f"cms/search_query?hl=en-US&key={quote(query)}&type=1")
    apps, seen = [], set()
    for app in _collect_apps(data):
        if app["package"] in seen:
            continue
        seen.add(app["package"])
        apps.append(app)
    return apps


def versions(package):
    """All versions APKPure knows for a package, newest first."""
    data = _get(f"cms/app_version?hl=en-US&package_name={quote(package)}")
    items = _collect_apps(data)
    items = [item for item in items if item["package"] == package]
    names = [item["version"] for item in items]
    for item in items:
        label = item["version"]
        if names.count(label) > 1 and item["version_code"]:
            label = f"{label} ({item['version_code']})"
        item["label"] = label
    return items


def download(url, destination, progress=None, cancelled=None, chunk=256 * 1024):
    """Stream ``url`` to ``destination``; ``progress(done, total)`` is optional.

    Writes to ``<destination>.part`` and renames on success so an interrupted
    download never looks like a finished APK.
    """
    part = f"{destination}.part"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response, open(part, "wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise ApkPureError("Download cancelled")
                block = response.read(chunk)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if os.path.getsize(part) == 0:
            raise ApkPureError("Downloaded file is empty")
        os.replace(part, destination)
    except HTTPError as exc:
        _discard(part)
        raise ApkPureError(f"Download failed: HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        _discard(part)
        raise ApkPureError(f"Download failed: {exc}") from exc
    except ApkPureError:
        _discard(part)
        raise


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def web_search_url(query):
    return f"{WEB_BASE}/search?q={quote(query)}"
