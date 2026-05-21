#!/usr/bin/env python3
import html
import json
import os
import re
import shutil
import subprocess
import threading
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import quote, urljoin

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk


APP_ID = "com.github.apktoolgui4lin"
APP_NAME = "APKTool GUI 4 Linux"
APKCOMBO_BASE_URL = "https://apkcombo.com"
APKCOMBO_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
APP_ROOT = Path(os.environ.get("APKTOOLGUI_HOME", Path(__file__).resolve().parent))
TOOLS_DIR = APP_ROOT / "tools"
ASSETS_DIR = APP_ROOT / "assets"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "apktoolgui4lin"
CONFIG_FILE = CONFIG_DIR / "config.json"
EXEC_CACHE_DIR = CONFIG_DIR / "exec-tools"
APKCOMBO_ICON_CACHE_DIR = CONFIG_DIR / "apkcombo-icons"


def bundled_path(*parts):
    return str(TOOLS_DIR.joinpath(*parts))


def asset_path(*parts):
    return str(ASSETS_DIR.joinpath(*parts))


def default_download_directory():
    xdg_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    if xdg_dir:
        return xdg_dir
    return str(Path.home() / "Downloads")


DEFAULT_SETTINGS = {
    "prefer_bundled": True,
    "java": bundled_path("jre", "bin", "java"),
    "apktool": bundled_path("apktool", "apktool.jar"),
    "zipalign": bundled_path("android-build-tools", "zipalign"),
    "apksigner": bundled_path("android-build-tools", "apksigner"),
    "keytool": bundled_path("jre", "bin", "keytool"),
    "aapt": bundled_path("android-build-tools", "aapt"),
    "aapt2": bundled_path("android-build-tools", "aapt2"),
    "debug_keystore": bundled_path("debug.keystore"),
    "debug_alias": "androiddebugkey",
    "debug_password": "android",
}


BUNDLED_TOOLS = {
    "java": bundled_path("jre", "bin", "java"),
    "apktool": bundled_path("apktool", "apktool.jar"),
    "zipalign": bundled_path("android-build-tools", "zipalign"),
    "apksigner": bundled_path("android-build-tools", "apksigner"),
    "keytool": bundled_path("jre", "bin", "keytool"),
    "aapt": bundled_path("android-build-tools", "aapt"),
    "aapt2": bundled_path("android-build-tools", "aapt2"),
}


def load_settings():
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                return {**DEFAULT_SETTINGS, **json.load(handle)}
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_SETTINGS, **settings}
    with CONFIG_FILE.open("w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2)
    return merged


def expand(value):
    return os.path.expanduser(value.strip())


def tool_exists(command):
    command = expand(command or "")
    if not command:
        return False
    if "/" in command:
        return Path(command).exists()
    return shutil.which(command) is not None


def resolve_tool(settings, key):
    bundled = BUNDLED_TOOLS.get(key)
    if settings.get("prefer_bundled", True) and bundled and Path(bundled).exists():
        return bundled
    return settings.get(key) or DEFAULT_SETTINGS[key]


def apktool_command(settings, general_options=None):
    apktool = expand(resolve_tool(settings, "apktool"))
    general_options = general_options or []
    if apktool.endswith(".jar"):
        return [resolve_tool(settings, "java"), "-jar", apktool] + general_options
    return [apktool or "apktool"] + general_options


def tool_environment():
    env = os.environ.copy()
    bundled_paths = [
        str(TOOLS_DIR / "jre" / "bin"),
        str(TOOLS_DIR / "android-build-tools"),
    ]
    library_paths = [
        str(TOOLS_DIR / "android-build-tools" / "lib64"),
        str(TOOLS_DIR / "android-build-tools" / "lib"),
        str(EXEC_CACHE_DIR / "android-build-tools" / "lib64"),
        str(EXEC_CACHE_DIR / "android-build-tools" / "lib"),
        str(EXEC_CACHE_DIR / "lib64"),
        str(EXEC_CACHE_DIR / "lib"),
        str(EXEC_CACHE_DIR),
    ]
    env["PATH"] = os.pathsep.join(bundled_paths + [env.get("PATH", "")])
    env["LD_LIBRARY_PATH"] = os.pathsep.join(library_paths + [env.get("LD_LIBRARY_PATH", "")])
    env.setdefault("JAVA_HOME", str(TOOLS_DIR / "jre"))
    return env


def executable_tool_path(path, name):
    source = Path(expand(path))
    if not source.exists():
        return str(source)
    if os.access(source, os.X_OK) and "/.mount_" not in str(source):
        return str(source)
    EXEC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = EXEC_CACHE_DIR / name
    try:
        if not target.exists() or source.stat().st_mtime > target.stat().st_mtime or source.stat().st_size != target.stat().st_size:
            shutil.copy2(source, target)
        for library_dir in ("lib64", "lib"):
            source_library_dir = source.parent / library_dir
            if source_library_dir.exists():
                shutil.copytree(source_library_dir, EXEC_CACHE_DIR / library_dir, dirs_exist_ok=True)
        target.chmod(0o755)
        return str(target)
    except OSError:
        return str(source)


def executable_android_build_tool(path, name):
    source = Path(expand(path))
    if not source.exists():
        return str(source)
    if "/.mount_" not in str(source) and os.access(source, os.X_OK):
        return str(source)
    source_dir = source.parent
    target_dir = EXEC_CACHE_DIR / "android-build-tools"
    try:
        if not target_dir.exists():
            shutil.copytree(source_dir, target_dir)
        else:
            for item in source_dir.iterdir():
                target = target_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    if not target.exists() or item.stat().st_size != target.stat().st_size or item.stat().st_mtime > target.stat().st_mtime:
                        shutil.copy2(item, target)
        target_tool = target_dir / name
        target_tool.chmod(0o755)
        for executable in ("aapt", "aapt2", "zipalign", "apksigner"):
            candidate = target_dir / executable
            if candidate.exists():
                candidate.chmod(0o755)
        return str(target_tool)
    except OSError:
        return executable_tool_path(path, name)


class ApkToolGui(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.settings = load_settings()
        self.process = None
        self.log_buffer = None
        self.widgets = {}

    def do_activate(self):
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title(APP_NAME)
        self.window.set_default_size(1120, 740)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.set_child(root)

        header = Gtk.HeaderBar()
        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("title-2")
        header.set_title_widget(title)
        tools_button = Gtk.Button(label="Check tools")
        tools_button.connect("clicked", self.on_check_tools)
        header.pack_end(tools_button)
        self.window.set_titlebar(header)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_resize_start_child(True)
        paned.set_shrink_start_child(False)
        paned.set_position(620)
        root.append(paned)

        notebook = Gtk.Notebook()
        notebook.set_hexpand(True)
        notebook.set_vexpand(True)
        paned.set_start_child(notebook)

        notebook.append_page(self.decompile_page(), Gtk.Label(label="Decode"))
        notebook.append_page(self.info_page(), Gtk.Label(label="Info APK"))
        notebook.append_page(self.apk_search_page(), Gtk.Label(label="APK Search"))
        notebook.append_page(self.build_page(), Gtk.Label(label="Build"))
        notebook.append_page(self.finish_page(), Gtk.Label(label="Align / Sign"))
        notebook.append_page(self.frameworks_page(), Gtk.Label(label="Frameworks"))
        notebook.append_page(self.tools_page(), Gtk.Label(label="Tools"))
        notebook.append_page(self.about_page(), Gtk.Label(label="About"))

        log_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        log_panel.set_margin_top(12)
        log_panel.set_margin_bottom(12)
        log_panel.set_margin_start(12)
        log_panel.set_margin_end(12)
        paned.set_end_child(log_panel)

        log_actions = Gtk.Box(spacing=8)
        log_panel.append(log_actions)
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.set_hexpand(True)
        log_actions.append(self.status_label)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self.on_cancel)
        log_actions.append(cancel)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda _button: self.log_buffer.set_text(""))
        log_actions.append(clear)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_width(500)
        scroller.set_vexpand(True)
        log_panel.append(scroller)
        log_view = Gtk.TextView()
        log_view.set_editable(False)
        log_view.set_monospace(True)
        log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buffer = log_view.get_buffer()
        self.log_tags = {
            "command": self.log_buffer.create_tag("command", foreground="#8ab4f8"),
            "success": self.log_buffer.create_tag("success", foreground="#81c995"),
            "error": self.log_buffer.create_tag("error", foreground="#f28b82"),
            "warning": self.log_buffer.create_tag("warning", foreground="#fdd663"),
            "section": self.log_buffer.create_tag("section", foreground="#c58af9", weight=700),
            "info": self.log_buffer.create_tag("info", foreground="#c9d1d9"),
        }
        scroller.set_child(log_view)

        self.load_settings_into_ui()
        self.window.present()

    def page_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        return box

    def scrolled_page(self, box):
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(box)
        return scroller

    def entry(self, key, placeholder="", password=False):
        item = Gtk.Entry()
        item.set_placeholder_text(placeholder)
        item.set_visibility(not password)
        self.widgets[key] = item
        return item

    def row(self, label, entry, browse=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(label=label, xalign=0))
        line = Gtk.Box(spacing=8)
        line.append(entry)
        if hasattr(entry, "set_hexpand"):
            entry.set_hexpand(True)
        if browse:
            button = Gtk.Button(label="Browse")
            button.connect("clicked", browse)
            line.append(button)
        box.append(line)
        return box

    def info_value(self, key, label):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.add_css_class("card")
        name = Gtk.Label(label=label, xalign=0)
        name.add_css_class("caption")
        value = Gtk.Label(label="-", xalign=0)
        value.set_selectable(True)
        value.set_wrap(True)
        value.add_css_class("title-4")
        self.widgets[key] = value
        box.append(name)
        box.append(value)
        return box

    def set_info_value(self, key, value):
        widget = self.widgets.get(key)
        if widget:
            widget.set_text(value or "-")
        return False

    def check(self, key, label, active=False):
        widget = Gtk.CheckButton(label=label)
        widget.set_active(active)
        self.widgets[key] = widget
        return widget

    def spin(self, key, lower, upper, default):
        widget = Gtk.SpinButton()
        widget.set_range(lower, upper)
        widget.set_increments(1, 10)
        widget.set_value(default)
        self.widgets[key] = widget
        return widget

    def combo(self, key, values, active=0):
        widget = Gtk.ComboBoxText()
        for value, label in values:
            widget.append(value, label)
        widget.set_active(active)
        self.widgets[key] = widget
        return widget

    def checks_row(self, *checks):
        box = Gtk.Box(spacing=14)
        for check in checks:
            box.append(check)
        return box

    def decompile_page(self):
        box = self.page_box()
        apk = self.entry("dec_apk", "/home/user/app.apk")
        output = self.entry("dec_output", "/home/user/app_src")
        frame_path = self.entry("dec_frame_path", "~/.local/share/apktool/framework")
        frame_tag = self.entry("dec_frame_tag", "optional tag")
        shared_lib = self.entry("dec_lib", "package:/path/framework.apk")
        box.append(self.row("Source APK", apk, lambda _b: self.choose_file(apk, "Select APK", self.on_decompile_apk_selected)))
        box.append(self.row("Output folder", output, lambda _b: self.choose_folder(output, "Select output folder")))
        box.append(self.row("Framework path (-p)", frame_path, lambda _b: self.choose_folder(frame_path, "Select framework path")))
        box.append(self.row("Framework tag (-t)", frame_tag))
        box.append(self.row("Shared library (-l)", shared_lib))
        box.append(self.row("Jobs (-j)", self.spin("dec_jobs", 1, 64, os.cpu_count() or 4)))
        box.append(self.row("Resource resolve mode", self.combo("dec_res_mode", [
            ("", "default"),
            ("greedy", "greedy"),
            ("lazy", "lazy"),
        ])))
        box.append(self.checks_row(
            self.check("dec_force", "Overwrite", True),
            self.check("dec_no_res", "No resources"),
            self.check("dec_no_src", "No smali"),
            self.check("dec_all_src", "All src"),
            self.check("dec_no_assets", "No assets"),
        ))
        box.append(self.checks_row(
            self.check("dec_no_debug_info", "No debug info"),
            self.check("dec_only_manifest", "Only manifest"),
            self.check("dec_match_original", "Match original"),
            self.check("dec_ignore_raw", "Ignorar raw values"),
            self.check("dec_broken", "Keep broken res"),
        ))
        box.append(self.checks_row(
            self.check("dec_quiet", "Quiet"),
            self.check("dec_verbose", "Verbose"),
        ))
        run = Gtk.Button(label="Decode")
        run.add_css_class("suggested-action")
        run.connect("clicked", self.on_decompile)
        box.append(run)
        return self.scrolled_page(box)

    def info_page(self):
        box = self.page_box()
        apk = self.entry("info_apk", "/home/user/app.apk")
        box.append(self.row("APK", apk, lambda _b: self.choose_file(apk, "Select APK", self.on_info_apk_selected)))

        summary = Gtk.Grid(column_spacing=10, row_spacing=10)
        summary.attach(self.info_value("info_package", "Package"), 0, 0, 1, 1)
        summary.attach(self.info_value("info_app_label", "App label"), 1, 0, 1, 1)
        summary.attach(self.info_value("info_version_name", "Version name"), 2, 0, 1, 1)
        summary.attach(self.info_value("info_version_code", "Version code"), 3, 0, 1, 1)
        summary.attach(self.info_value("info_sdk", "SDK"), 0, 1, 1, 1)
        summary.attach(self.info_value("info_target_sdk", "Target SDK"), 1, 1, 1, 1)
        summary.attach(self.info_value("info_abis", "ABIs"), 2, 1, 1, 1)
        summary.attach(self.info_value("info_densities", "Densities"), 3, 1, 1, 1)
        box.append(summary)

        perms_frame = Gtk.Frame(label="Permissions")
        perms_scroller = Gtk.ScrolledWindow()
        perms_scroller.set_min_content_height(120)
        self.info_permissions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        perms_scroller.set_child(self.info_permissions_box)
        perms_frame.set_child(perms_scroller)
        box.append(perms_frame)

        actions = Gtk.Box(spacing=8)
        for label, callback in [
            ("Refresh summary", self.on_apk_info_summary),
            ("Full output", self.on_apk_info_all),
            ("Permissions", self.on_apk_info_permissions),
            ("Manifest XML", self.on_apk_info_manifest),
            ("Resources", self.on_apk_info_resources),
            ("Configurations", self.on_apk_info_configurations),
            ("List contents", self.on_apk_info_list),
        ]:
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            actions.append(button)
        box.append(actions)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        self.info_buffer = Gtk.TextBuffer()
        info_view = Gtk.TextView(buffer=self.info_buffer)
        info_view.set_editable(False)
        info_view.set_monospace(True)
        info_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroller.set_child(info_view)
        box.append(scroller)
        return box

    def apk_search_page(self):
        box = self.page_box()
        query = self.entry("apkcombo_query", "Telegram or org.telegram.messenger")
        download_dir = self.entry("apkcombo_download_dir", default_download_directory())
        query_row = Gtk.Box(spacing=8)
        query.set_hexpand(True)
        query.connect("activate", self.on_apkcombo_search)
        query_row.append(query)
        search = Gtk.Button(label="Search")
        search.add_css_class("suggested-action")
        search.connect("clicked", self.on_apkcombo_search)
        query_row.append(search)
        box.append(self.row("Search APKCombo", query_row))
        box.append(self.row("Download folder", download_dir, lambda _b: self.choose_folder(download_dir, "Select download folder")))

        mode = self.combo("apkcombo_mode", [
            ("auto", "Auto"),
            ("name", "App name"),
            ("package", "Package name"),
        ])
        box.append(self.row("Match mode", mode))

        actions = Gtk.Box(spacing=8)
        open_search = Gtk.Button(label="Open search in browser")
        open_search.connect("clicked", self.on_apkcombo_open_search)
        actions.append(open_search)
        box.append(actions)

        notice = Gtk.Label(
            label="APKCombo integration parses public result pages on a best-effort basis. Download only APKs you are legally allowed to obtain.",
            xalign=0,
        )
        notice.set_wrap(True)
        box.append(notice)

        self.apkcombo_progress = Gtk.ProgressBar()
        self.apkcombo_progress.set_show_text(True)
        self.apkcombo_progress.set_text("Idle")
        box.append(self.apkcombo_progress)

        self.apkcombo_results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.apkcombo_results_box)
        box.append(scroller)
        return box

    def build_page(self):
        box = self.page_box()
        project = self.entry("build_project", "/home/user/app_src")
        output = self.entry("build_output", "/home/user/app-unsigned.apk")
        aapt = self.entry("build_aapt", "")
        frame_path = self.entry("build_frame_path", "~/.local/share/apktool/framework")
        shared_lib = self.entry("build_lib", "package:/path/framework.apk")
        box.append(self.row("Decoded project folder", project, lambda _b: self.choose_folder(project, "Select project", self.on_build_project_selected)))
        box.append(self.row("Unsigned output APK", output, lambda _b: self.choose_save(output, "Output APK")))
        box.append(self.row("AAPT backend", self.combo("build_aapt_backend", [
            ("aapt2", "aapt2 (default, stricter)"),
            ("aapt", "aapt (legacy fallback)"),
            ("custom", "Custom path"),
        ])))
        box.append(self.row("Custom aapt/aapt2 (--aapt)", aapt, lambda _b: self.choose_file(aapt, "Select aapt/aapt2")))
        box.append(self.row("Framework path (-p)", frame_path, lambda _b: self.choose_folder(frame_path, "Select framework path")))
        box.append(self.row("Shared library (-l)", shared_lib))
        box.append(self.row("Jobs (-j)", self.spin("build_jobs", 1, 64, os.cpu_count() or 4)))
        box.append(self.checks_row(
            self.check("build_force", "Force all"),
            self.check("build_debuggable", "Debuggable"),
            self.check("build_copy_original", "Copy original"),
            self.check("build_net_sec", "Network security config"),
        ))
        box.append(self.checks_row(
            self.check("build_no_apk", "No APK"),
            self.check("build_no_crunch", "No crunch"),
            self.check("build_quiet", "Quiet"),
            self.check("build_verbose", "Verbose"),
        ))
        run = Gtk.Button(label="Build")
        run.add_css_class("suggested-action")
        run.connect("clicked", self.on_build)
        pipeline = Gtk.Button(label="Build + align + sign")
        pipeline.add_css_class("suggested-action")
        pipeline.connect("clicked", self.on_build_align_sign)
        actions = Gtk.Box(spacing=8)
        actions.append(run)
        actions.append(pipeline)
        box.append(actions)
        return self.scrolled_page(box)

    def finish_page(self):
        box = self.page_box()
        source = self.entry("fin_source", "/home/user/app-unsigned.apk")
        aligned = self.entry("fin_aligned", "/home/user/app-aligned.apk")
        signed = self.entry("fin_signed", "/home/user/app-signed.apk")
        keystore = self.entry("fin_keystore")
        alias = self.entry("fin_alias")
        password = self.entry("fin_password", password=True)

        box.append(self.row("Unsigned APK", source, lambda _b: self.choose_file(source, "Select APK")))
        box.append(self.row("Aligned APK", aligned, lambda _b: self.choose_save(aligned, "Save aligned APK")))
        box.append(self.row("Signed APK", signed, lambda _b: self.choose_save(signed, "Save signed APK")))
        box.append(self.row("Keystore", keystore, lambda _b: self.choose_file(keystore, "Select keystore")))
        box.append(self.row("Alias", alias))
        box.append(self.row("Password", password))
        box.append(self.row("Zipalign alignment", self.spin("zip_alignment", 1, 4096, 4)))
        box.append(self.row("Page align libs", self.combo("zip_page_mode", [
            ("none", "No"),
            ("p", "-p: 4 KiB"),
            ("P4", "-P 4 KiB"),
            ("P16", "-P 16 KiB"),
            ("P64", "-P 64 KiB"),
        ])))
        box.append(self.checks_row(
            self.check("zip_force", "Overwrite", True),
            self.check("zip_verbose", "Zipalign verbose"),
            self.check("zip_zopfli", "Zopfli"),
        ))
        box.append(self.row("Min SDK", self.entry("sign_min_sdk", "optional")))
        box.append(self.row("Max SDK", self.entry("sign_max_sdk", "optional")))
        box.append(self.row("Lib page alignment", self.spin("sign_lib_page_alignment", 4096, 65536, 16384)))
        for key, label in [
            ("sign_v1", "V1/JAR signing"),
            ("sign_v2", "V2 signing"),
            ("sign_v3", "V3 signing"),
            ("sign_v4", "V4 signing"),
        ]:
            box.append(self.row(label, self.combo(key, [
                ("", "Automatico"),
                ("true", "Activado"),
                ("false", "Desactivado"),
            ])))
        box.append(self.checks_row(
            self.check("sign_verbose", "Firma verbose"),
            self.check("sign_align_file_size", "Align file size"),
            self.check("sign_verity", "Verity"),
            self.check("sign_preserve_alignment", "Preservar alineacion"),
        ))
        box.append(self.row("Verify min SDK", self.entry("verify_min_sdk", "optional")))
        box.append(self.row("Verify max SDK", self.entry("verify_max_sdk", "optional")))
        box.append(self.checks_row(
            self.check("verify_verbose", "Verify verbose", True),
            self.check("verify_print_certs", "Print certs"),
            self.check("verify_print_certs_pem", "Print certs PEM"),
            self.check("verify_werr", "Warnings as errors"),
        ))

        actions = Gtk.Box(spacing=8)
        for label, callback in [
            ("Zipalign", self.on_zipalign),
            ("Sign", self.on_sign),
            ("Verify signature", self.on_verify),
            ("Create debug keystore", self.on_create_keystore),
        ]:
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            actions.append(button)
        box.append(actions)
        return self.scrolled_page(box)

    def frameworks_page(self):
        box = self.page_box()
        fw_apk = self.entry("fw_apk", "/home/user/framework-res.apk")
        fw_path = self.entry("fw_path", "~/.local/share/apktool/framework")
        fw_tag = self.entry("fw_tag", "optional tag")
        arsc = self.entry("fw_arsc", "/home/user/resources.arsc")
        box.append(self.row("Framework APK", fw_apk, lambda _b: self.choose_file(fw_apk, "Select framework APK")))
        box.append(self.row("Framework path (-p)", fw_path, lambda _b: self.choose_folder(fw_path, "Select framework path")))
        box.append(self.row("Framework tag (-t)", fw_tag))
        box.append(self.row("resources.arsc for publicize-resources", arsc, lambda _b: self.choose_file(arsc, "Select resources.arsc")))
        box.append(self.checks_row(
            self.check("fw_all", "All frameworks (-a)"),
            self.check("fw_quiet", "Quiet"),
            self.check("fw_verbose", "Verbose"),
        ))
        actions = Gtk.Box(spacing=8)
        for label, callback in [
            ("Install framework", self.on_install_framework),
            ("List frameworks", self.on_list_frameworks),
            ("Clean frameworks", self.on_clean_frameworks),
            ("Publicize resources", self.on_publicize_resources),
        ]:
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            actions.append(button)
        box.append(actions)
        return self.scrolled_page(box)

    def tools_page(self):
        box = self.page_box()
        for key, label, placeholder in [
            ("set_java", "Java", "java"),
            ("set_apktool", "Apktool jar o comando", "/opt/apktool/apktool_3.0.2.jar"),
            ("set_zipalign", "zipalign", "zipalign"),
            ("set_apksigner", "apksigner", "apksigner"),
            ("set_keytool", "keytool", "keytool"),
            ("set_aapt", "aapt", bundled_path("android-build-tools", "aapt")),
            ("set_aapt2", "aapt2", bundled_path("android-build-tools", "aapt2")),
            ("set_debug_keystore", "Default debug keystore", bundled_path("debug.keystore")),
            ("set_debug_alias", "Debug alias", "androiddebugkey"),
        ]:
            box.append(self.row(label, self.entry(key, placeholder)))
        box.append(self.row("Debug password", self.entry("set_debug_password", password=True)))
        prefer = self.check("set_prefer_bundled", "Use tools bundled with the app", True)
        box.append(prefer)

        actions = Gtk.Box(spacing=8)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.on_save_settings)
        actions.append(save)
        check = Gtk.Button(label="Check tools")
        check.connect("clicked", self.on_check_tools)
        actions.append(check)
        box.append(actions)
        self.tools_status = Gtk.Label(label="", xalign=0)
        self.tools_status.set_wrap(True)
        box.append(self.tools_status)
        root = Gtk.Label(label=f"Portable directory: {TOOLS_DIR}", xalign=0)
        root.set_wrap(True)
        box.append(root)
        return box

    def link_action(self, icon_name, label, url):
        button = Gtk.Button()
        button.connect("clicked", lambda _b, target=url: webbrowser.open(target))
        content = Gtk.Box(spacing=10)
        picture = Gtk.Picture.new_for_filename(asset_path(icon_name))
        picture.set_size_request(24, 24)
        picture.set_can_shrink(True)
        content.append(picture)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=label, xalign=0)
        title.add_css_class("title-5")
        title.set_halign(Gtk.Align.START)
        subtitle = Gtk.Label(label=url, xalign=0)
        subtitle.add_css_class("caption")
        subtitle.set_wrap(True)
        subtitle.set_halign(Gtk.Align.START)
        text_box.append(title)
        text_box.append(subtitle)
        content.append(text_box)
        button.set_child(content)
        return button

    def about_page(self):
        box = self.page_box()

        title = Gtk.Label(label=APP_NAME, xalign=0)
        title.add_css_class("title-1")
        box.append(title)

        intro = Gtk.Label(
            label="A native Linux desktop frontend for Apktool workflows: decode, rebuild, align, sign, verify, inspect APK metadata, and manage framework files without living in a terminal all day.",
            xalign=0,
        )
        intro.set_wrap(True)
        box.append(intro)

        workflow = Gtk.Label(
            label="Workflow: APK -> decode -> edit -> build -> align -> sign -> verify",
            xalign=0,
        )
        workflow.add_css_class("title-4")
        workflow.set_wrap(True)
        box.append(workflow)

        stack_title = Gtk.Label(label="What powers it", xalign=0)
        stack_title.add_css_class("title-4")
        box.append(stack_title)

        stack = Gtk.Label(
            label="Python 3, GTK 4 / PyGObject, Apktool 3.x, Android Build Tools, Temurin JRE, and AppImage packaging.",
            xalign=0,
        )
        stack.set_wrap(True)
        box.append(stack)

        notes_title = Gtk.Label(label="Why it exists", xalign=0)
        notes_title.add_css_class("title-4")
        box.append(notes_title)

        notes = Gtk.Label(
            label="This project was born from the idea of having a real Linux counterpart to APK Tools GUI on Windows: the same kind of practical desktop workflow, but for Linux users who want the convenience of these tools without opening a terminal, remembering commands, or manually chaining every step together.",
            xalign=0,
        )
        notes.set_wrap(True)
        box.append(notes)

        notes_more = Gtk.Label(
            label="It wraps established Android tooling behind a straightforward GTK interface, keeps the command output visible for transparency, and makes common tasks like decode, rebuild, align, sign, verify, and framework management accessible from one place.",
            xalign=0,
        )
        notes_more.set_wrap(True)
        box.append(notes_more)

        support_title = Gtk.Label(label="Links", xalign=0)
        support_title.add_css_class("title-4")
        box.append(support_title)

        links = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        links.append(self.link_action("github.svg", "GitHub / drkbcn", "https://github.com/drkbcn"))
        links.append(self.link_action("kofi.svg", "Ko-fi / drkbcn", "https://ko-fi.com/drkbcn"))
        box.append(links)

        footer = Gtk.Label(
            label="APKTool GUI 4 Linux wraps established Android tooling rather than reimplementing it. The goal is a clear, portable desktop workflow for Linux users.",
            xalign=0,
        )
        footer.set_wrap(True)
        box.append(footer)

        return self.scrolled_page(box)

    def load_settings_into_ui(self):
        mapping = {
            "set_java": "java",
            "set_apktool": "apktool",
            "set_zipalign": "zipalign",
            "set_apksigner": "apksigner",
            "set_keytool": "keytool",
            "set_aapt": "aapt",
            "set_aapt2": "aapt2",
            "set_debug_keystore": "debug_keystore",
            "set_debug_alias": "debug_alias",
            "set_debug_password": "debug_password",
            "fin_keystore": "debug_keystore",
            "fin_alias": "debug_alias",
            "fin_password": "debug_password",
        }
        for widget_key, settings_key in mapping.items():
            self.widgets[widget_key].set_text(self.settings.get(settings_key, ""))
        self.widgets["set_prefer_bundled"].set_active(bool(self.settings.get("prefer_bundled", True)))

    def collect_settings(self):
        self.settings = save_settings({
            "prefer_bundled": self.widgets["set_prefer_bundled"].get_active(),
            "java": self.widgets["set_java"].get_text(),
            "apktool": self.widgets["set_apktool"].get_text(),
            "zipalign": self.widgets["set_zipalign"].get_text(),
            "apksigner": self.widgets["set_apksigner"].get_text(),
            "keytool": self.widgets["set_keytool"].get_text(),
            "aapt": self.widgets["set_aapt"].get_text(),
            "aapt2": self.widgets["set_aapt2"].get_text(),
            "debug_keystore": self.widgets["set_debug_keystore"].get_text(),
            "debug_alias": self.widgets["set_debug_alias"].get_text(),
            "debug_password": self.widgets["set_debug_password"].get_text(),
        })
        self.load_settings_into_ui()

    def choose_file(self, entry, title, callback=None):
        self.open_file_dialog("file", entry, title, callback)

    def choose_folder(self, entry, title, callback=None):
        self.open_file_dialog("folder", entry, title, callback)

    def choose_save(self, entry, title, callback=None):
        self.open_file_dialog("save", entry, title, callback)

    def open_file_dialog(self, mode, entry, title, callback=None):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog()
            dialog.set_title(title)
            try:
                if mode == "folder":
                    dialog.select_folder(self.window, None, self.on_file_dialog_result, (dialog, entry, mode, title, callback))
                elif mode == "save":
                    dialog.save(self.window, None, self.on_file_dialog_result, (dialog, entry, mode, title, callback))
                else:
                    dialog.open(self.window, None, self.on_file_dialog_result, (dialog, entry, mode, title, callback))
                return
            except Exception as exc:
                self.append_log(f"Gtk.FileDialog failed: {exc}")
        self.open_zenity_dialog(mode, entry, title, callback)

    def on_file_dialog_result(self, _dialog, result, payload):
        dialog, entry, mode, title, callback = payload
        try:
            if mode == "folder":
                file_obj = dialog.select_folder_finish(result)
            elif mode == "save":
                file_obj = dialog.save_finish(result)
            else:
                file_obj = dialog.open_finish(result)
            if file_obj:
                path = file_obj.get_path()
                if path:
                    entry.set_text(path)
                    if callback:
                        callback(path)
        except GLib.Error as exc:
            if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.append_log(f"GTK file picker failed: {exc.message}")
                self.open_zenity_dialog(mode, entry, title, callback)
        except Exception as exc:
            self.append_log(f"GTK file picker failed: {exc}")
            self.open_zenity_dialog(mode, entry, title, callback)

    def open_zenity_dialog(self, mode, entry, title, callback=None):
        zenity = shutil.which("zenity")
        if not zenity:
            self.append_log("Could not open the file picker. Type the path manually.")
            return
        command = [zenity, "--file-selection", f"--title={title}"]
        if mode == "folder":
            command.append("--directory")
        elif mode == "save":
            command.append("--save")
            command.append("--confirm-overwrite")

        def worker():
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
                if result.returncode == 0:
                    path = result.stdout.strip()
                    if path:
                        GLib.idle_add(entry.set_text, path)
                        if callback:
                            GLib.idle_add(callback, path)
                elif result.returncode not in (1,):
                    GLib.idle_add(self.append_log, result.stderr.strip() or "Zenity failed to open the file picker.")
            except Exception as exc:
                GLib.idle_add(self.append_log, f"Zenity failed: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def field(self, key):
        return expand(self.widgets[key].get_text())

    def active(self, key):
        return self.widgets[key].get_active()

    def combo_value(self, key):
        return self.widgets[key].get_active_id() or ""

    def spin_value(self, key):
        return int(self.widgets[key].get_value())

    def default_decode_output(self, apk_path):
        path = Path(apk_path)
        suffix = "_src"
        if path.suffix.lower() == ".apk":
            return str(path.with_suffix("").with_name(path.stem + suffix))
        return str(path.parent / (path.name + suffix))

    def on_decompile_apk_selected(self, path):
        output = self.widgets["dec_output"]
        current = output.get_text().strip()
        if not current or current.endswith("_src") or current.endswith(".out"):
            output.set_text(self.default_decode_output(path))

    def on_info_apk_selected(self, _path):
        self.on_apk_info_summary(None)

    def default_build_outputs(self, project_path):
        project = Path(project_path)
        base = project.name[:-4] if project.name.endswith("_src") else project.name
        directory = project.parent
        unsigned = directory / f"{base}-unsigned.apk"
        aligned = directory / f"{base}-aligned.apk"
        signed = directory / f"{base}-signed.apk"
        return str(unsigned), str(aligned), str(signed)

    def on_build_project_selected(self, path):
        unsigned, aligned, signed = self.default_build_outputs(path)
        if not self.widgets["build_output"].get_text().strip():
            self.widgets["build_output"].set_text(unsigned)
        self.widgets["fin_source"].set_text(self.widgets["build_output"].get_text().strip() or unsigned)
        self.widgets["fin_aligned"].set_text(aligned)
        self.widgets["fin_signed"].set_text(signed)

    def ensure_build_outputs(self):
        project = self.field("build_project")
        if not project:
            return
        unsigned, aligned, signed = self.default_build_outputs(project)
        if not self.field("build_output"):
            self.widgets["build_output"].set_text(unsigned)
        if not self.field("fin_source"):
            self.widgets["fin_source"].set_text(self.field("build_output") or unsigned)
        if not self.field("fin_aligned"):
            self.widgets["fin_aligned"].set_text(aligned)
        if not self.field("fin_signed"):
            self.widgets["fin_signed"].set_text(signed)

    def log_tag_for(self, text):
        lowered = text.lower()
        if text.startswith("$ "):
            return "command"
        if text.startswith("## "):
            return "section"
        if "error" in lowered or "failed" in lowered or "not found" in lowered or "[exit 1]" in lowered:
            return "error"
        if "warning" in lowered or "best-effort" in lowered:
            return "warning"
        if "completed" in lowered or "downloaded:" in lowered or "[exit 0]" in lowered or "ok" in lowered:
            return "success"
        return "info"

    def maybe_log_build_hint(self, text):
        lowered = text.lower()
        if (
            "failed to read png signature" in lowered
            or "invalid entry name" in lowered
            or "aapt2, compile" in lowered
        ):
            self.append_log("Hint: this looks like an aapt2 resource compile failure. If it says a PNG signature is invalid, the file may be a JPEG/WebP with a .png name; convert it to a real PNG or replace it, then rebuild. For other aapt2-only failures, try Build -> AAPT backend -> aapt (legacy fallback).", "warning")
        return False

    def append_log(self, text, tag=None):
        end = self.log_buffer.get_end_iter()
        tag = tag or self.log_tag_for(text)
        if tag in self.log_tags:
            self.log_buffer.insert_with_tags(end, text + "\n", self.log_tags[tag])
        else:
            self.log_buffer.insert(end, text + "\n")
        return False

    def set_info_text(self, text):
        self.info_buffer.set_text(text)
        return False

    def append_info(self, text):
        end = self.info_buffer.get_end_iter()
        self.info_buffer.insert(end, text + "\n")
        return False

    def set_status(self, text):
        self.status_label.set_text(text)
        return False

    def run_command(self, label, command, cwd=None):
        if self.process:
            self.append_log("A task is already running.")
            return

        def worker():
            GLib.idle_add(self.set_status, label)
            GLib.idle_add(self.append_log, "$ " + " ".join(command))
            try:
                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=tool_environment(),
                    cwd=cwd,
                    text=True,
                    bufsize=1,
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    clean_line = line.rstrip("\n")
                    GLib.idle_add(self.append_log, clean_line)
                    GLib.idle_add(self.maybe_log_build_hint, clean_line)
                code = self.process.wait()
                GLib.idle_add(self.append_log, f"[exit {code}]")
                GLib.idle_add(self.set_status, "Completed" if code == 0 else "Error")
            except FileNotFoundError as exc:
                GLib.idle_add(self.append_log, f"Tool not found: {exc}")
                GLib.idle_add(self.set_status, "Error")
            except Exception as exc:
                GLib.idle_add(self.append_log, f"Error: {exc}")
                GLib.idle_add(self.set_status, "Error")
            finally:
                self.process = None

        threading.Thread(target=worker, daemon=True).start()

    def run_capture(self, label, commands, target="log"):
        def write(text):
            if target == "info":
                GLib.idle_add(self.append_info, text)
            else:
                GLib.idle_add(self.append_log, text)

        def clear():
            if target == "info":
                GLib.idle_add(self.set_info_text, "")

        def worker():
            GLib.idle_add(self.set_status, label)
            clear()
            for heading, command in commands:
                write(f"## {heading}")
                write("$ " + " ".join(command))
                try:
                    result = subprocess.run(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=tool_environment(),
                        text=True,
                        check=False,
                    )
                    output = result.stdout.rstrip()
                    if output:
                        write(output)
                    write(f"[exit {result.returncode}]")
                except Exception as exc:
                    write(f"Error: {exc}")
                write("")
            GLib.idle_add(self.set_status, "Completed")

        threading.Thread(target=worker, daemon=True).start()

    def run_pipeline(self, label, commands):
        if self.process:
            self.append_log("A task is already running.")
            return

        def worker():
            GLib.idle_add(self.set_status, label)
            ok = True
            for step, command in commands:
                GLib.idle_add(self.append_log, f"## {step}")
                GLib.idle_add(self.append_log, "$ " + " ".join(command))
                try:
                    self.process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=tool_environment(),
                        text=True,
                        bufsize=1,
                    )
                    assert self.process.stdout is not None
                    for line in self.process.stdout:
                        clean_line = line.rstrip("\n")
                        GLib.idle_add(self.append_log, clean_line)
                        GLib.idle_add(self.maybe_log_build_hint, clean_line)
                    code = self.process.wait()
                    GLib.idle_add(self.append_log, f"[exit {code}]")
                    if code != 0:
                        ok = False
                        break
                except Exception as exc:
                    ok = False
                    GLib.idle_add(self.append_log, f"Error: {exc}")
                    break
                finally:
                    self.process = None
            GLib.idle_add(self.set_status, "Completed" if ok else "Error")

        threading.Thread(target=worker, daemon=True).start()

    def run_build_align_sign_pipeline(self, commands, cleanup_paths, final_path):
        if self.process:
            self.append_log("A task is already running.")
            return

        def worker():
            GLib.idle_add(self.set_status, "Build + align + sign")
            ok = True
            for step, command in commands:
                GLib.idle_add(self.append_log, f"## {step}")
                GLib.idle_add(self.append_log, "$ " + " ".join(command))
                try:
                    self.process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=tool_environment(),
                        text=True,
                        bufsize=1,
                    )
                    assert self.process.stdout is not None
                    for line in self.process.stdout:
                        clean_line = line.rstrip("\n")
                        GLib.idle_add(self.append_log, clean_line)
                        GLib.idle_add(self.maybe_log_build_hint, clean_line)
                    code = self.process.wait()
                    GLib.idle_add(self.append_log, f"[exit {code}]")
                    if code != 0:
                        ok = False
                        break
                except Exception as exc:
                    ok = False
                    GLib.idle_add(self.append_log, f"Error: {exc}")
                    break
                finally:
                    self.process = None

            if ok:
                final_file = Path(final_path)
                for path in cleanup_paths:
                    candidate = Path(path)
                    if candidate == final_file or not candidate.exists():
                        continue
                    try:
                        candidate.unlink()
                        GLib.idle_add(self.append_log, f"Removed intermediate file: {candidate}", "info")
                    except OSError as exc:
                        GLib.idle_add(self.append_log, f"Could not remove intermediate file {candidate}: {exc}", "warning")

            GLib.idle_add(self.set_status, "Completed" if ok else "Error")

        threading.Thread(target=worker, daemon=True).start()

    def require(self, key, label):
        value = self.field(key)
        if not value:
            self.append_log(f"Missing required field: {label}")
            return None
        return value

    def on_save_settings(self, _button):
        self.collect_settings()
        self.append_log("Settings saved.")

    def on_check_tools(self, _button):
        self.collect_settings()
        checks = {
            "java": tool_exists(resolve_tool(self.settings, "java")),
            "apktool": tool_exists(resolve_tool(self.settings, "apktool")),
            "zipalign": tool_exists(resolve_tool(self.settings, "zipalign")),
            "apksigner": tool_exists(resolve_tool(self.settings, "apksigner")),
            "keytool": tool_exists(resolve_tool(self.settings, "keytool")),
            "aapt": tool_exists(resolve_tool(self.settings, "aapt")),
            "aapt2": tool_exists(resolve_tool(self.settings, "aapt2")),
        }
        text = " | ".join(f"{name}: {'OK' if ok else 'No encontrado'}" for name, ok in checks.items())
        self.tools_status.set_text(text)
        self.append_log(text)
        self.show_tool_versions()

    def show_tool_versions(self):
        commands = [
            ("Java", [resolve_tool(self.settings, "java"), "-version"]),
            ("Apktool", apktool_command(self.settings) + ["--version"]),
            ("aapt", [resolve_tool(self.settings, "aapt"), "version"]),
            ("aapt2", [resolve_tool(self.settings, "aapt2"), "version"]),
            ("apksigner", [resolve_tool(self.settings, "apksigner"), "--version"]),
        ]
        self.run_capture("Tool versions", commands)

    def apktool_general_options(self, prefix):
        options = []
        if self.active(f"{prefix}_quiet"):
            options.append("-q")
        if self.active(f"{prefix}_verbose"):
            options.append("-v")
        return options

    def add_entry_option(self, command, flag, key):
        value = self.field(key)
        if value:
            command.extend([flag, value])

    def info_apk(self):
        apk = self.require("info_apk", "APK para info")
        return apk

    def clear_permissions(self):
        child = self.info_permissions_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.info_permissions_box.remove(child)
            child = next_child
        return False

    def add_permission_chip(self, permission):
        label = Gtk.Label(label=permission, xalign=0)
        label.set_selectable(True)
        self.info_permissions_box.append(label)
        return False

    def clear_box(self, box):
        child = box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            box.remove(child)
            child = next_child
        return False

    def strip_html(self, value):
        value = re.sub(r"<[^>]+>", " ", value)
        value = re.sub(r"\s+", " ", value)
        return html.unescape(value).strip()

    def apkcombo_search_url(self):
        query = self.field("apkcombo_query")
        return f"{APKCOMBO_BASE_URL}/search/{quote(query)}"

    def apkcombo_score(self, query, mode, name, package, developer, category):
        query_norm = query.strip().lower()
        name_norm = name.lower()
        package_norm = package.lower()
        developer_norm = developer.lower()
        if not query_norm:
            return 0, "empty query"
        if mode in ("auto", "package"):
            if query_norm == package_norm:
                return 100, "exact package"
            if query_norm in package_norm:
                return 92, "package contains query"
        if mode in ("auto", "name"):
            if query_norm == name_norm:
                return 96, "exact name"
            if query_norm in name_norm:
                return 82, "name contains query"
        tokens = [token for token in re.split(r"[\s._-]+", query_norm) if token]
        haystack = " ".join([name_norm, package_norm, developer_norm, category.lower()])
        if tokens:
            matched = sum(1 for token in tokens if token in haystack)
            if matched:
                return int(45 + 35 * (matched / len(tokens))), f"{matched}/{len(tokens)} query tokens"
        return 10, "weak related result"

    def parse_apkcombo_results(self, query, mode, html_text):
        pattern = re.compile(
            r'<a\s+class="l_item"\s+href="(?P<href>[^"]+)"\s+title="(?P<title>[^"]*)".*?'
            r'<span\s+class="name">(?P<name>.*?)</span>.*?'
            r'<span\s+class="author">(?P<author>.*?)</span>.*?'
            r'<span\s+class="description">(?P<description>.*?)</span>',
            re.S,
        )
        results = []
        seen = set()
        for match in pattern.finditer(html_text):
            href = html.unescape(match.group("href"))
            url = urljoin(APKCOMBO_BASE_URL, href)
            if url in seen:
                continue
            seen.add(url)
            path_parts = [part for part in href.split("/") if part]
            package = path_parts[-1] if path_parts else ""
            author = self.strip_html(match.group("author"))
            if " · " in author:
                developer, category = author.split(" · ", 1)
            else:
                developer, category = author, ""
            description = self.strip_html(match.group("description"))
            metrics = re.findall(r"<span[^>]*>(.*?)</span>", match.group("description"), re.S)
            metrics = [self.strip_html(item) for item in metrics]
            icon_match = re.search(r'<img[^>]+src="(?P<src>[^"]+)"', match.group(0), re.S)
            icon_url = urljoin(APKCOMBO_BASE_URL, html.unescape(icon_match.group("src"))) if icon_match else ""
            score, reason = self.apkcombo_score(query, mode, self.strip_html(match.group("name")), package, developer, category)
            download_url = urljoin(APKCOMBO_BASE_URL, href.rstrip("/") + "/download/apk")
            results.append({
                "name": self.strip_html(match.group("name")),
                "package": package,
                "developer": developer,
                "category": category,
                "description": description,
                "metrics": " | ".join(metrics) if metrics else description,
                "latest_version": "Unknown",
                "versions": [],
                "url": url,
                "download_url": download_url,
                "icon_url": icon_url,
                "score": score,
                "reason": reason,
            })
        results.sort(key=lambda item: item["score"], reverse=True)
        return results

    def parse_apkcombo_versions(self, html_text):
        versions = []
        current = re.search(r'<div\s+class="version">(?P<value>.*?)</div>', html_text, re.S)
        if current:
            value = self.strip_html(current.group("value"))
            if value:
                versions.append({"name": value, "href": ""})
        for match in re.finditer(r'<a\s+class="ver-item"[^>]*href="(?P<href>[^"]+)"[^>]*>.*?<span\s+class="vername">(?P<value>.*?)</span>', html_text, re.S):
            value = self.strip_html(match.group("value"))
            value = re.sub(r"^.*?\s+(\d+(?:[._-]\d+)*.*)$", r"\1", value)
            if value:
                versions.append({"name": value, "href": html.unescape(match.group("href"))})
        if not versions:
            meta = re.search(r"Latest Version:\s*([^-\"]+)", html.unescape(html_text))
            if meta:
                versions.append({"name": meta.group(1).strip(), "href": ""})
        clean = []
        seen = set()
        for version in versions:
            name = version.get("name", "").strip()
            href = version.get("href", "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            clean.append({
                "name": name,
                "url": urljoin(APKCOMBO_BASE_URL, href) if href else "",
                "download_url": urljoin(APKCOMBO_BASE_URL, href.rstrip("/") + "/download/apk") if href else "",
            })
        return clean

    def enrich_apkcombo_results(self, curl, results, limit=12):
        for item in results[:limit]:
            try:
                detail = subprocess.run(
                    [curl, "-L", "-s", "-A", APKCOMBO_USER_AGENT, item["url"]],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=20,
                )
                if detail.returncode == 0:
                    versions = self.parse_apkcombo_versions(detail.stdout)
                    item["versions"] = versions[:8]
                    item["latest_version"] = versions[0]["name"] if versions else "Unknown"
            except Exception:
                pass
        return results

    def update_apkcombo_icon(self, picture, icon_path):
        try:
            texture = Gdk.Texture.new_from_filename(str(icon_path))
            picture.set_paintable(texture)
        except Exception:
            pass
        return False

    def load_apkcombo_icon(self, picture, result):
        icon_url = result.get("icon_url")
        if not icon_url:
            return
        curl = shutil.which("curl")
        if not curl:
            return

        def worker():
            APKCOMBO_ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            package = re.sub(r"[^A-Za-z0-9._-]+", "_", result.get("package") or result.get("name") or "apkcombo")
            suffix = Path(icon_url.split("?", 1)[0]).suffix or ".png"
            cached = APKCOMBO_ICON_CACHE_DIR / f"{package}{suffix}"
            if not cached.exists():
                temp = None
                try:
                    handle = tempfile.NamedTemporaryFile(delete=False, dir=APKCOMBO_ICON_CACHE_DIR, suffix=suffix)
                    handle.close()
                    temp = Path(handle.name)
                    command = [curl, "-L", "--fail", "-s", "-A", APKCOMBO_USER_AGENT, "-o", str(temp), icon_url]
                    result_proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=30)
                    if result_proc.returncode != 0 or temp.stat().st_size == 0:
                        if temp.exists():
                            temp.unlink()
                        return
                    temp.replace(cached)
                except Exception:
                    if temp and temp.exists():
                        temp.unlink()
                    return
            GLib.idle_add(self.update_apkcombo_icon, picture, cached)

        threading.Thread(target=worker, daemon=True).start()

    def selected_apkcombo_version(self, result, combo):
        active = combo.get_active_id() or ""
        for version in result.get("versions", []):
            if version.get("name") == active:
                return version
        if result.get("versions"):
            return result["versions"][0]
        return {
            "name": result.get("latest_version") or "Latest",
            "url": result.get("url", ""),
            "download_url": result.get("download_url", ""),
        }

    def add_apkcombo_result(self, result):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        header = Gtk.Box(spacing=10)
        icon = Gtk.Picture()
        icon.set_size_request(48, 48)
        icon.set_can_shrink(True)
        header.append(icon)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(label=f"{result['name']}  -  {result['score']}% match", xalign=0)
        title.add_css_class("title-4")
        title.set_selectable(True)
        title_box.append(title)
        header.append(title_box)
        card.append(header)
        self.load_apkcombo_icon(icon, result)
        meta = Gtk.Label(
            label=f"{result['package']} | {result['developer']} | {result['category']} | {result['metrics']}",
            xalign=0,
        )
        meta.set_wrap(True)
        meta.set_selectable(True)
        card.append(meta)
        version_text = f"Latest: {result.get('latest_version') or 'Unknown'}"
        if result.get("versions"):
            version_text += " | Available: " + ", ".join(version["name"] for version in result["versions"][:6])
        versions = Gtk.Label(label=version_text, xalign=0)
        versions.set_wrap(True)
        versions.set_selectable(True)
        card.append(versions)
        version_row = Gtk.Box(spacing=8)
        version_label = Gtk.Label(label="Download version", xalign=0)
        version_row.append(version_label)
        version_combo = Gtk.ComboBoxText()
        if result.get("versions"):
            for version in result["versions"]:
                version_combo.append(version["name"], version["name"])
            version_combo.set_active(0)
        else:
            fallback = result.get("latest_version") or "Latest"
            version_combo.append(fallback, fallback)
            version_combo.set_active(0)
        version_row.append(version_combo)
        card.append(version_row)
        reason = Gtk.Label(label=f"Match: {result['reason']}", xalign=0)
        reason.add_css_class("caption")
        card.append(reason)
        actions = Gtk.Box(spacing=8)
        open_button = Gtk.Button(label="Open page")
        open_button.connect("clicked", lambda _b, url=result["url"]: webbrowser.open(url))
        actions.append(open_button)
        download_page = Gtk.Button(label="Open download page")
        download_page.connect("clicked", lambda _b, item=result, combo=version_combo: webbrowser.open(self.selected_apkcombo_version(item, combo).get("download_url") or item["download_url"]))
        actions.append(download_page)
        download = Gtk.Button(label="Download")
        download.connect("clicked", lambda _b, item=result, combo=version_combo: self.on_apkcombo_download(item, self.selected_apkcombo_version(item, combo)))
        actions.append(download)
        card.append(actions)
        self.apkcombo_results_box.append(card)
        return False

    def set_apkcombo_progress(self, fraction=None, text=None, pulse=False):
        if pulse:
            self.apkcombo_progress.pulse()
        elif fraction is not None:
            self.apkcombo_progress.set_fraction(max(0.0, min(1.0, fraction)))
        if text is not None:
            self.apkcombo_progress.set_text(text)
        return False

    def on_apkcombo_open_search(self, _button):
        webbrowser.open(self.apkcombo_search_url())

    def on_apkcombo_search(self, _button):
        query = self.field("apkcombo_query")
        if not query:
            self.append_log("Missing required field: APKCombo query")
            return
        curl = shutil.which("curl")
        if not curl:
            self.append_log("curl is required for APKCombo search because urllib is commonly blocked by APKCombo.")
            return
        mode = self.combo_value("apkcombo_mode")
        url = self.apkcombo_search_url()

        def worker():
            GLib.idle_add(self.set_status, "Searching APKCombo")
            GLib.idle_add(self.clear_box, self.apkcombo_results_box)
            command = [curl, "-L", "-s", "-A", APKCOMBO_USER_AGENT, url]
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=45)
                if result.returncode != 0:
                    GLib.idle_add(self.append_log, result.stdout.strip() or f"APKCombo search failed with exit {result.returncode}")
                    GLib.idle_add(self.set_status, "Error")
                    return
                results = self.parse_apkcombo_results(query, mode, result.stdout)
                if not results:
                    GLib.idle_add(self.append_log, "No APKCombo results were parsed. The page structure may have changed or access may be blocked.")
                else:
                    GLib.idle_add(self.append_log, "Fetching APKCombo version metadata for top results...")
                    results = self.enrich_apkcombo_results(curl, results)
                for item in results[:30]:
                    GLib.idle_add(self.add_apkcombo_result, item)
                GLib.idle_add(self.append_log, f"APKCombo results parsed: {len(results)} from {url}")
                GLib.idle_add(self.set_status, "Completed")
            except Exception as exc:
                GLib.idle_add(self.append_log, f"APKCombo search failed: {exc}")
                GLib.idle_add(self.set_status, "Error")

        threading.Thread(target=worker, daemon=True).start()

    def on_apkcombo_download(self, result, version=None):
        curl = shutil.which("curl")
        if not curl:
            self.append_log("curl is required for APKCombo downloads.")
            return
        directory = Path(self.field("apkcombo_download_dir") or default_download_directory())
        directory.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self.download_apkcombo_item, args=(curl, result, version, directory), daemon=True).start()

    def parse_apkcombo_variants(self, html_text):
        variants = []
        seen = set()
        pattern = re.compile(r'<a\s+href="(?P<href>[^"]+)"\s+class="variant"(?P<body>.*?)</a>', re.S)
        for match in pattern.finditer(html_text):
            href = html.unescape(match.group("href"))
            if href in seen:
                continue
            seen.add(href)
            body = match.group("body")
            name = self.strip_html(re.search(r'<span\s+class="vername">(?P<value>.*?)</span>', body, re.S).group("value")) if re.search(r'<span\s+class="vername">(?P<value>.*?)</span>', body, re.S) else "APKCombo package"
            size = self.strip_html(re.search(r'<span\s+class="spec ltr">(?P<value>.*?)</span>', body, re.S).group("value")) if re.search(r'<span\s+class="spec ltr">(?P<value>.*?)</span>', body, re.S) else ""
            kind_match = re.search(r'<span\s+class="type-[^"]+">(?P<value>.*?)</span>', body, re.S)
            kind = self.strip_html(kind_match.group("value")).lower() if kind_match else "apk"
            arch_match = re.search(r'<code>(?P<value>[^<]+)</code>', html_text[:match.start()])
            arch = self.strip_html(arch_match.group("value")) if arch_match else ""
            variants.append({
                "url": href,
                "name": name,
                "size": size,
                "kind": kind if kind in ("apk", "xapk", "apks", "zip") else "apk",
                "arch": arch,
            })
        return variants

    def safe_download_name(self, result, variant, version_name=""):
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{result['package']}-{version_name or variant['name']}".strip("-"))
        base = base.strip("._-") or result["package"] or "apkcombo-download"
        ext = variant.get("kind") or "apk"
        if not base.lower().endswith(f".{ext}"):
            base = f"{base}.{ext}"
        return base

    def download_apkcombo_item(self, curl, result, version, directory):
        if self.process:
            GLib.idle_add(self.append_log, "A task is already running.")
            return
        version = version or {
            "name": result.get("latest_version") or "Latest",
            "download_url": result.get("download_url", ""),
        }
        GLib.idle_add(self.set_status, f"Resolving {result['name']}")
        GLib.idle_add(self.set_apkcombo_progress, 0.0, "Resolving download page")
        page_cmd = [curl, "-L", "-s", "-A", APKCOMBO_USER_AGENT, version.get("download_url") or result["download_url"]]
        try:
            page = subprocess.run(page_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=45)
            if page.returncode != 0:
                GLib.idle_add(self.append_log, page.stdout.strip() or f"Could not load APKCombo download page: exit {page.returncode}")
                GLib.idle_add(self.set_status, "Error")
                return
            variants = self.parse_apkcombo_variants(page.stdout)
            if not variants:
                GLib.idle_add(self.append_log, "No APKCombo variant links were found. Open the download page in a browser.")
                GLib.idle_add(self.set_status, "Error")
                return
            variant = variants[0]
            if len(variants) > 1:
                GLib.idle_add(self.append_log, f"Found {len(variants)} variants. Downloading first variant: {variant['name']} {variant['arch']} {variant['size']}".strip())
            download_url = urljoin(APKCOMBO_BASE_URL, variant["url"])
            output = directory / self.safe_download_name(result, variant, version.get("name", ""))
            command = [curl, "-L", "--fail", "-A", APKCOMBO_USER_AGENT, "-o", str(output), download_url]
            GLib.idle_add(self.append_log, "$ " + " ".join(command))
            GLib.idle_add(self.set_status, f"Downloading {result['name']}")
            GLib.idle_add(self.set_apkcombo_progress, 0.0, f"Downloading {output.name}")
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            progress_buffer = ""
            assert self.process.stdout is not None
            while True:
                char = self.process.stdout.read(1)
                if char == "" and self.process.poll() is not None:
                    break
                if not char:
                    continue
                if char in ("\r", "\n"):
                    text = progress_buffer.strip()
                    progress_buffer = ""
                    percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)%", text) or re.search(r"^\s*(\d{1,3})\s+", text)
                    if percent_match:
                        percent = min(100.0, float(percent_match.group(1)))
                        GLib.idle_add(self.set_apkcombo_progress, percent / 100.0, f"{percent:.0f}% - {output.name}")
                    elif text:
                        GLib.idle_add(self.append_log, text)
                    else:
                        GLib.idle_add(self.set_apkcombo_progress, None, None, True)
                else:
                    progress_buffer += char
            code = self.process.wait()
            if code == 0 and output.exists() and output.stat().st_size > 0:
                GLib.idle_add(self.set_apkcombo_progress, 1.0, f"Downloaded {output.name}")
                GLib.idle_add(self.append_log, f"Downloaded: {output} ({output.stat().st_size} bytes)")
                GLib.idle_add(self.set_status, "Completed")
            else:
                if output.exists() and output.stat().st_size == 0:
                    output.unlink()
                GLib.idle_add(self.append_log, f"Download failed with exit {code}. Open the download page in a browser.")
                GLib.idle_add(self.set_status, "Error")
        except Exception as exc:
            GLib.idle_add(self.append_log, f"APKCombo download failed: {exc}")
            GLib.idle_add(self.set_status, "Error")
        finally:
            self.process = None

    def parse_badging(self, text):
        def single(pattern):
            match = re.search(pattern, text)
            return match.group(1) if match else "-"

        package = single(r"package: name='([^']+)'")
        version_code = single(r"versionCode='([^']+)'")
        version_name = single(r"versionName='([^']*)'")
        sdk = single(r"sdkVersion:'([^']+)'")
        target_sdk = single(r"targetSdkVersion:'([^']+)'")
        label = single(r"application-label(?:-[^:]+)?:'([^']*)'")
        abis = ", ".join(re.findall(r"native-code: '([^']+)'", text)) or "-"
        densities_match = re.search(r"densities:((?: '[^']+')+)", text)
        densities = ", ".join(re.findall(r"'([^']+)'", densities_match.group(1))) if densities_match else "-"
        return {
            "info_package": package,
            "info_app_label": label,
            "info_version_name": version_name,
            "info_version_code": version_code,
            "info_sdk": sdk,
            "info_target_sdk": target_sdk,
            "info_abis": abis,
            "info_densities": densities,
        }

    def parse_permissions(self, text):
        permissions = []
        for line in text.splitlines():
            match = re.search(r"(?:uses-)?permission(?:-sdk-\d+)?: name='([^']+)'", line)
            if match:
                permissions.append(match.group(1))
        return sorted(set(permissions))

    def on_apk_info_summary(self, _button):
        apk = self.info_apk()
        if not apk:
            return

        def worker():
            GLib.idle_add(self.set_status, "Reading APK info")
            badging_cmd = [resolve_tool(self.settings, "aapt2"), "dump", "badging", apk]
            permissions_cmd = [resolve_tool(self.settings, "aapt2"), "dump", "permissions", apk]
            try:
                badging = subprocess.run(badging_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=tool_environment(), text=True, check=False).stdout
                permissions = subprocess.run(permissions_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=tool_environment(), text=True, check=False).stdout
                for key, value in self.parse_badging(badging).items():
                    GLib.idle_add(self.set_info_value, key, value)
                GLib.idle_add(self.clear_permissions)
                parsed_permissions = self.parse_permissions(permissions)
                if parsed_permissions:
                    for permission in parsed_permissions:
                        GLib.idle_add(self.add_permission_chip, permission)
                else:
                    GLib.idle_add(self.add_permission_chip, "No declared permissions or none detected")
                GLib.idle_add(self.set_info_text, badging.strip())
                GLib.idle_add(self.set_status, "Completed")
            except Exception as exc:
                GLib.idle_add(self.append_log, f"Error reading APK info: {exc}")
                GLib.idle_add(self.set_status, "Error")

        threading.Thread(target=worker, daemon=True).start()

    def run_apk_info(self, title, commands):
        apk = self.info_apk()
        if not apk:
            return
        resolved = []
        for heading, template in commands:
            resolved.append((heading, [part if part != "{apk}" else apk for part in template]))
        self.run_capture(title, resolved, target="info")

    def on_apk_info_all(self, _button):
        self.run_apk_info("Info APK", [
            ("Badging", [resolve_tool(self.settings, "aapt2"), "dump", "badging", "{apk}"]),
            ("Package name", [resolve_tool(self.settings, "aapt2"), "dump", "packagename", "{apk}"]),
            ("Permissions", [resolve_tool(self.settings, "aapt2"), "dump", "permissions", "{apk}"]),
            ("Configurations", [resolve_tool(self.settings, "aapt2"), "dump", "configurations", "{apk}"]),
            ("Manifest XML", [resolve_tool(self.settings, "aapt"), "dump", "xmltree", "{apk}", "AndroidManifest.xml"]),
            ("APK contents", [resolve_tool(self.settings, "aapt"), "list", "-a", "{apk}"]),
        ])

    def on_apk_info_badging(self, _button):
        self.run_apk_info("Badging", [
            ("Badging", [resolve_tool(self.settings, "aapt2"), "dump", "badging", "{apk}"]),
        ])

    def on_apk_info_permissions(self, _button):
        self.run_apk_info("Permissions", [
            ("Permissions", [resolve_tool(self.settings, "aapt2"), "dump", "permissions", "{apk}"]),
        ])

    def on_apk_info_manifest(self, _button):
        self.run_apk_info("Manifest XML", [
            ("Manifest XML", [resolve_tool(self.settings, "aapt"), "dump", "xmltree", "{apk}", "AndroidManifest.xml"]),
        ])

    def on_apk_info_resources(self, _button):
        self.run_apk_info("Resources", [
            ("Resources", [resolve_tool(self.settings, "aapt2"), "dump", "resources", "{apk}"]),
        ])

    def on_apk_info_configurations(self, _button):
        self.run_apk_info("Configurations", [
            ("Configurations", [resolve_tool(self.settings, "aapt2"), "dump", "configurations", "{apk}"]),
        ])

    def on_apk_info_list(self, _button):
        self.run_apk_info("APK contents", [
            ("APK contents", [resolve_tool(self.settings, "aapt"), "list", "-a", "{apk}"]),
        ])

    def on_decompile(self, _button):
        apk = self.require("dec_apk", "source APK")
        output = self.require("dec_output", "output folder")
        if not apk or not output:
            return
        command = apktool_command(self.settings, self.apktool_general_options("dec")) + ["d", apk, "-o", output]
        if self.active("dec_force"):
            command.append("-f")
        if self.active("dec_no_res"):
            command.append("-r")
        if self.active("dec_no_src"):
            command.append("-s")
        if self.active("dec_all_src"):
            command.append("-a")
        if self.active("dec_no_assets"):
            command.append("--no-assets")
        if self.active("dec_no_debug_info"):
            command.append("--no-debug-info")
        if self.active("dec_only_manifest"):
            command.append("--only-manifest")
        if self.active("dec_match_original"):
            command.append("--match-original")
        if self.active("dec_ignore_raw"):
            command.append("--ignore-raw-values")
        if self.active("dec_broken"):
            command.append("--keep-broken-res")
        command.extend(["-j", str(self.spin_value("dec_jobs"))])
        self.add_entry_option(command, "-p", "dec_frame_path")
        self.add_entry_option(command, "-t", "dec_frame_tag")
        self.add_entry_option(command, "-l", "dec_lib")
        res_mode = self.combo_value("dec_res_mode")
        if res_mode:
            command.extend(["--res-resolve-mode", res_mode])
        self.run_command("Decoding", command)

    def build_command(self):
        self.ensure_build_outputs()
        project = self.require("build_project", "project")
        output = self.require("build_output", "output APK")
        if not project or not output:
            return None
        command = apktool_command(self.settings, self.apktool_general_options("build")) + ["b", project, "-o", output]
        if self.active("build_force"):
            command.append("-f")
        if self.active("build_debuggable"):
            command.append("--debuggable")
        if self.active("build_copy_original"):
            command.append("--copy-original")
        if self.active("build_net_sec"):
            command.append("--net-sec-conf")
        if self.active("build_no_apk"):
            command.append("--no-apk")
        if self.active("build_no_crunch"):
            command.append("--no-crunch")
        command.extend(["-j", str(self.spin_value("build_jobs"))])
        aapt = self.selected_aapt_tool()
        if aapt:
            command.extend(["--aapt", aapt])
        self.add_entry_option(command, "-p", "build_frame_path")
        self.add_entry_option(command, "-l", "build_lib")
        return command

    def zipalign_command(self, source=None, output=None):
        source = source or self.require("fin_source", "unsigned APK")
        output = output or self.require("fin_aligned", "aligned APK")
        if not source or not output:
            return None
        command = [resolve_tool(self.settings, "zipalign")]
        if self.active("zip_force"):
            command.append("-f")
        if self.active("zip_verbose"):
            command.append("-v")
        if self.active("zip_zopfli"):
            command.append("-z")
        page_mode = self.combo_value("zip_page_mode")
        if page_mode == "p":
            command.append("-p")
        elif page_mode.startswith("P"):
            command.extend(["-P", page_mode[1:]])
        command.extend([str(self.spin_value("zip_alignment")), source, output])
        return command

    def sign_command(self, source=None, output=None):
        source = source or self.field("fin_aligned") or self.field("fin_source")
        output = output or self.require("fin_signed", "signed APK")
        keystore = self.require("fin_keystore", "Keystore")
        alias = self.require("fin_alias", "Alias")
        password = self.require("fin_password", "Password")
        if not source:
            self.append_log("Missing required field: APK to sign")
            return None
        if not output or not keystore or not alias or not password:
            return None
        command = [
            resolve_tool(self.settings, "apksigner"),
            "sign",
            "--ks",
            keystore,
            "--ks-key-alias",
            alias,
            "--ks-pass",
            f"pass:{password}",
            "--key-pass",
            f"pass:{password}",
        ]
        for key, flag in [
            ("sign_v1", "--v1-signing-enabled"),
            ("sign_v2", "--v2-signing-enabled"),
            ("sign_v3", "--v3-signing-enabled"),
            ("sign_v4", "--v4-signing-enabled"),
        ]:
            value = self.combo_value(key)
            if value:
                command.extend([flag, value])
        for key, flag in [
            ("sign_min_sdk", "--min-sdk-version"),
            ("sign_max_sdk", "--max-sdk-version"),
        ]:
            value = self.field(key)
            if value:
                command.extend([flag, value])
        if self.active("sign_verbose"):
            command.append("--verbose")
        if self.active("sign_align_file_size"):
            command.append("--align-file-size")
        if self.active("sign_verity"):
            command.append("--verity-enabled")
        if self.active("sign_preserve_alignment"):
            command.append("--alignment-preserved")
        command.extend(["--lib-page-alignment", str(self.spin_value("sign_lib_page_alignment"))])
        command.extend(["--out", output, source])
        return command

    def create_keystore_command(self, keystore, alias, password):
        return [
            resolve_tool(self.settings, "keytool"),
            "-genkeypair",
            "-v",
            "-keystore",
            keystore,
            "-storepass",
            password,
            "-keypass",
            password,
            "-alias",
            alias,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-dname",
            "CN=Android Debug,O=Android,C=US",
        ]

    def missing_debug_keystore_command(self):
        keystore = self.field("fin_keystore")
        alias = self.field("fin_alias")
        password = self.field("fin_password")
        if not keystore or not alias or not password:
            return None
        if Path(keystore).exists():
            return None
        if (
            keystore == self.settings.get("debug_keystore", DEFAULT_SETTINGS["debug_keystore"])
            and alias == self.settings.get("debug_alias", DEFAULT_SETTINGS["debug_alias"])
            and password == self.settings.get("debug_password", DEFAULT_SETTINGS["debug_password"])
        ):
            parent = Path(keystore).parent
            if not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
            if not os.access(parent, os.W_OK):
                self.append_log(f"Bundled debug keystore is missing and cannot be created here: {keystore}", "error")
                return None
            self.append_log("Debug keystore not found. It will be created automatically before signing.", "warning")
            return self.create_keystore_command(keystore, alias, password)
        self.append_log(f"Keystore not found: {keystore}. Select another keystore or create one before signing.", "error")
        return None

    def selected_aapt_tool(self):
        backend = self.combo_value("build_aapt_backend") or "aapt2"
        if backend == "custom":
            custom = self.field("build_aapt")
            if custom:
                name = Path(custom).name or "aapt"
                if name in ("aapt", "aapt2"):
                    return executable_android_build_tool(custom, name)
                return executable_tool_path(custom, name)
            self.append_log("Custom AAPT backend selected but no custom path was provided. Falling back to aapt2.", "warning")
            backend = "aapt2"
        if backend == "aapt":
            return executable_android_build_tool(resolve_tool(self.settings, "aapt"), "aapt")
        return executable_android_build_tool(resolve_tool(self.settings, "aapt2"), "aapt2")

    def on_build(self, _button):
        command = self.build_command()
        if command:
            self.run_command("Building", command)

    def on_zipalign(self, _button):
        command = self.zipalign_command()
        if command:
            self.run_command("Aligning", command)

    def on_sign(self, _button):
        command = self.sign_command()
        if not command:
            return
        create_debug = self.missing_debug_keystore_command()
        if create_debug:
            self.run_pipeline("Create debug keystore + sign", [
                ("Create debug keystore", create_debug),
                ("Sign", command),
            ])
            return
        if self.field("fin_keystore") and not Path(self.field("fin_keystore")).exists():
            return
        self.run_command("Signing", command)

    def on_build_align_sign(self, _button):
        build = self.build_command()
        if not build:
            return
        unsigned = self.field("build_output")
        if not unsigned:
            self.append_log("Missing required field: output APK")
            return
        if not self.field("fin_source"):
            self.widgets["fin_source"].set_text(unsigned)
        if not self.field("fin_aligned"):
            self.widgets["fin_aligned"].set_text(str(Path(unsigned).with_name(Path(unsigned).stem.replace("-unsigned", "") + "-aligned.apk")))
        if not self.field("fin_signed"):
            self.widgets["fin_signed"].set_text(str(Path(unsigned).with_name(Path(unsigned).stem.replace("-unsigned", "") + "-signed.apk")))
        align = self.zipalign_command(source=unsigned, output=self.field("fin_aligned"))
        sign = self.sign_command(source=self.field("fin_aligned"), output=self.field("fin_signed"))
        if not (build and align and sign):
            return
        commands = [("Build", build), ("Align", align)]
        create_debug = self.missing_debug_keystore_command()
        if create_debug:
            commands.append(("Create debug keystore", create_debug))
        elif self.field("fin_keystore") and not Path(self.field("fin_keystore")).exists():
            return
        commands.append(("Sign", sign))
        self.run_build_align_sign_pipeline(commands, [unsigned, self.field("fin_aligned")], self.field("fin_signed"))

    def on_verify(self, _button):
        source = self.field("fin_signed") or self.field("fin_aligned")
        if not source:
            self.append_log("Missing required field: APK to verify")
            return
        command = [resolve_tool(self.settings, "apksigner"), "verify"]
        if self.active("verify_verbose"):
            command.append("--verbose")
        if self.active("verify_print_certs"):
            command.append("--print-certs")
        if self.active("verify_print_certs_pem"):
            command.append("--print-certs-pem")
        if self.active("verify_werr"):
            command.append("-Werr")
        for key, flag in [
            ("verify_min_sdk", "--min-sdk-version"),
            ("verify_max_sdk", "--max-sdk-version"),
        ]:
            value = self.field(key)
            if value:
                command.extend([flag, value])
        command.append(source)
        self.run_command("Verifying", command)

    def on_create_keystore(self, _button):
        keystore = self.require("fin_keystore", "Keystore")
        alias = self.require("fin_alias", "Alias")
        password = self.require("fin_password", "Password")
        if not keystore or not alias or not password:
            return
        Path(keystore).parent.mkdir(parents=True, exist_ok=True)
        command = self.create_keystore_command(keystore, alias, password)
        self.run_command("Creating keystore", command)

    def framework_options(self, include_all=False):
        options = []
        frame_path = self.field("fw_path")
        frame_tag = self.field("fw_tag")
        if frame_path:
            options.extend(["-p", frame_path])
        if frame_tag:
            options.extend(["-t", frame_tag])
        if include_all and self.active("fw_all"):
            options.append("-a")
        return options

    def on_install_framework(self, _button):
        apk = self.require("fw_apk", "Framework APK")
        if not apk:
            return
        command = apktool_command(self.settings, self.apktool_general_options("fw")) + ["if", apk]
        frame_path = self.field("fw_path")
        frame_tag = self.field("fw_tag")
        if frame_path:
            command.extend(["-p", frame_path])
        if frame_tag:
            command.extend(["-t", frame_tag])
        self.run_command("Installing framework", command)

    def on_list_frameworks(self, _button):
        command = apktool_command(self.settings, self.apktool_general_options("fw")) + ["lf"] + self.framework_options(include_all=True)
        self.run_command("Listing frameworks", command)

    def on_clean_frameworks(self, _button):
        command = apktool_command(self.settings, self.apktool_general_options("fw")) + ["cf"] + self.framework_options(include_all=True)
        self.run_command("Cleaning frameworks", command)

    def on_publicize_resources(self, _button):
        arsc = self.require("fw_arsc", "resources.arsc")
        if not arsc:
            return
        command = apktool_command(self.settings, self.apktool_general_options("fw")) + ["pr", arsc]
        self.run_command("Publicize resources", command)

    def on_cancel(self, _button):
        if self.process:
            self.process.terminate()
            self.append_log("[cancelled]")
            self.set_status("Cancelado")


if __name__ == "__main__":
    app = ApkToolGui()
    raise SystemExit(app.run())
