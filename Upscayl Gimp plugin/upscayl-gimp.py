#!/usr/bin/env python3
"""
Upscayl for GIMP — GIMP 3.0 Python-Fu plugin
Runs the Upscayl CLI binary (upscayl-bin) against the active layer and
imports the result as a new layer.

Install: copy this whole folder to your GIMP 3.0 plug-ins directory, e.g.
  ~/.config/GIMP/3.0/plug-ins/upscayl-gimp/upscayl-gimp.py
Make the .py executable: chmod +x upscayl-gimp.py
Restart GIMP. Filters > Enhance > Upscayl...
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gimp, GimpUi, Gtk, GLib, Gio

PLUGIN_ID = "plug-in-upscayl"
PLUGIN_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------
# Each entry: (label, binary_glob_candidates, models_dir_candidates)
# Paths are checked in order; first binary that exists+is executable wins
# per source. All found sources are offered; the best is preselected.

FLATPAK_APP_ID = "org.upscayl.Upscayl"

def _expand(paths):
    return [os.path.expanduser(os.path.expandvars(p)) for p in paths]


def candidate_sources():
    """Return a list of dicts describing every Upscayl install we can find.

    Each dict: {source, binary, models_dir, extra_model_dirs}
    'source' is one of: system, flatpak, appimage(=apk on some distros'
    slang, but really AppImage/zip-extracted), snap.
    """
    found = []

    # --- system package (deb/rpm/AUR upscayl-bin, or 'upscayl-cli'/'upscayl-ncnn' symlink) ---
    # Confirmed directly from a real Arch/paru install of AUR upscayl-bin
    # 2.15.0-10 (pacman -Ql upscayl-bin): CLI command on PATH is
    # `upscayl-ncnn`, wrapping the actual binary at
    # /usr/share/upscayl/bin/upscayl-bin, with models at
    # /usr/share/upscayl/models. Prior AUR comments/ebuilds referenced
    # upscayl-cli and /opt/Upscayl paths — the packaging has since changed,
    # so all of these are kept as candidates rather than replaced.
    system_bin_names = ["upscayl-ncnn", "upscayl-cli", "upscayl-bin", "upscayl-realesrgan"]
    system_bin_dirs = _expand([
        "/usr/bin", "/usr/local/bin", "/usr/share/upscayl/bin",
        "/opt/Upscayl/resources/bin", "/opt/upscayl/resources/bin",
    ])
    system_model_dirs = _expand([
        "/usr/share/upscayl/models", "/usr/bin/models",
        "/opt/Upscayl/resources/models", "/opt/upscayl/resources/models",
    ])
    for d in system_bin_dirs:
        for name in system_bin_names:
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                models = next((m for m in system_model_dirs if os.path.isdir(m)), None)
                found.append({
                    "source": "system",
                    "label": f"System install ({p})",
                    "binary": p,
                    "models_dir": models,
                })
                break

    # Also trust a PATH lookup, in case it's installed somewhere non-standard.
    for name in system_bin_names:
        p = shutil.which(name)
        if p and not any(f["binary"] == p for f in found):
            models = next((m for m in system_model_dirs if os.path.isdir(m)), None)
            found.append({
                "source": "system",
                "label": f"System install (PATH: {p})",
                "binary": p,
                "models_dir": models,
            })

    # --- Flatpak ---
    flatpak_roots = _expand([
        "/var/lib/flatpak/app/" + FLATPAK_APP_ID,
        "~/.local/share/flatpak/app/" + FLATPAK_APP_ID,
    ])
    for root in flatpak_roots:
        if not os.path.isdir(root):
            continue
        # Layout: <root>/<arch>/<branch>/active/files/...
        active_links = glob.glob(os.path.join(root, "*", "*", "active"))
        for active in active_links:
            files_dir = os.path.join(active, "files")
            bin_candidates = glob.glob(os.path.join(files_dir, "**", "upscayl-bin"), recursive=True) \
                + glob.glob(os.path.join(files_dir, "**", "upscayl-cli"), recursive=True)
            if not bin_candidates:
                continue
            b = bin_candidates[0]
            model_candidates = glob.glob(os.path.join(files_dir, "**", "models"), recursive=True)

            # `flatpak run --command=NAME` execs NAME as found on the
            # SANDBOX's own PATH (typically /app/bin inside the sandbox) —
            # it is NOT the basename of wherever we happened to find the
            # file when scanning the host filesystem, and there is no
            # guarantee those two names match. The authoritative source
            # for the real in-sandbox command is the app's own metadata
            # file (always present for an installed Flatpak app), which
            # has a plain `command=NAME` line under [Application].
            run_command = None
            metadata_path = os.path.join(active, "metadata")
            if os.path.isfile(metadata_path):
                try:
                    with open(metadata_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("command="):
                                run_command = line.split("=", 1)[1].strip()
                                break
                except Exception:
                    pass
            if not run_command:
                # Fall back to the basename we found, but flag it as a
                # guess — this may still fail with the same bwrap error
                # if the sandbox's PATH name differs, and the person
                # should be told why rather than hit a silent failure.
                run_command = os.path.basename(b)

            found.append({
                "source": "flatpak",
                "label": f"Flatpak ({FLATPAK_APP_ID})",
                "binary": b,
                "models_dir": model_candidates[0] if model_candidates else None,
                # Flatpak apps must be launched via `flatpak run`, not the
                # raw binary path (sandboxed), unless we can bind-mount.
                "flatpak_run": True,
                "flatpak_run_command": run_command,
            })

    # --- AppImage / manually extracted zip ("apk" in the user's shorthand —
    #     Linux doesn't have .apk packages; this covers portable installs) ---
    portable_dirs = _expand([
        "~/Applications", "~/.local/opt/Upscayl", "~/upscayl", "~/Downloads",
    ])
    for d in portable_dirs:
        if not os.path.isdir(d):
            continue
        appimages = glob.glob(os.path.join(d, "*[Uu]pscayl*.AppImage"))
        for ai in appimages:
            if os.access(ai, os.X_OK):
                found.append({
                    "source": "appimage",
                    "label": f"AppImage ({os.path.basename(ai)})",
                    "binary": ai,
                    "models_dir": None,  # AppImages self-extract; user must
                                          # point to a models dir manually.
                    "is_appimage": True,
                })

    # --- Snap ---
    snap_bin = shutil.which("upscayl")
    if snap_bin and "/snap/" in snap_bin:
        found.append({
            "source": "snap",
            "label": f"Snap ({snap_bin})",
            "binary": snap_bin,
            "models_dir": None,
        })

    return found


def list_models(models_dir):
    """Return sorted unique model base-names found in a directory.
    Upscayl models are NCNN pairs: <name>.param + <name>.bin
    """
    if not models_dir or not os.path.isdir(models_dir):
        return []
    names = set()
    for f in os.listdir(models_dir):
        base, ext = os.path.splitext(f)
        if ext == ".param":
            if os.path.isfile(os.path.join(models_dir, base + ".bin")):
                names.add(base)
    return sorted(names)


# ---------------------------------------------------------------------------
# Settings persistence (so the dialog remembers your last picks)
# ---------------------------------------------------------------------------

def config_path():
    conf_dir = os.path.join(GLib.get_user_config_dir(), "upscayl-gimp")
    os.makedirs(conf_dir, exist_ok=True)
    return os.path.join(conf_dir, "settings.txt")


def load_settings():
    path = config_path()
    data = {}
    if os.path.isfile(path):
        with open(path, "r") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    data[k] = v
    return data


def save_settings(d):
    with open(config_path(), "w") as f:
        for k, v in d.items():
            f.write(f"{k}={v}\n")


# ---------------------------------------------------------------------------
# CPU / GPU live monitoring (best-effort, degrades silently where a
# metric isn't available on this system/vendor/driver).
# ---------------------------------------------------------------------------

def read_cpu_percent(_state={"prev_total": None, "prev_idle": None}):
    """Instantaneous CPU% via /proc/stat deltas. No extra dependencies.
    Returns None if /proc/stat isn't readable (e.g. non-Linux)."""
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()
        if parts[0] != "cpu":
            return None
        vals = list(map(int, parts[1:8]))
        idle = vals[3] + vals[4]  # idle + iowait
        total = sum(vals)
        prev_total, prev_idle = _state["prev_total"], _state["prev_idle"]
        _state["prev_total"], _state["prev_idle"] = total, idle
        if prev_total is None:
            return None  # first sample has no delta yet
        dt = total - prev_total
        di = idle - prev_idle
        if dt <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (dt - di) / dt))
    except Exception:
        return None


def detect_gpu_monitor(preferred_vendor=None):
    """Best-effort detection of a GPU we can name and poll usage for.
    Checks ALL known sources (AMD sysfs, Nvidia) rather than stopping at
    the first hit — on hybrid laptops with both an AMD iGPU and an Nvidia
    dGPU, the AMD sysfs file is essentially always readable, so returning
    on first match meant the monitor could show the iGPU even when Upscayl
    itself was told to run on the Nvidia card via -g.

    preferred_vendor: 'nvidia' or 'amd', from the user's GPU id selection
    (GPU id 1 conventionally maps to the discrete/Nvidia card on hybrid
    systems, though this isn't guaranteed) — if given and that vendor was
    found, it's preferred over whichever was found first.

    Returns a dict {name, kind, poll_fn} or None if nothing usable is found.
    Never raises — any failure here just means no GPU line on the graph.
    """
    candidates = []

    # --- AMD via amdgpu sysfs ---
    try:
        drm = "/sys/class/drm"
        if os.path.isdir(drm):
            for entry in sorted(os.listdir(drm)):
                if not re.match(r"^card\d+$", entry):
                    continue
                busy_path = f"{drm}/{entry}/device/gpu_busy_percent"
                name_path = f"{drm}/{entry}/device/product_name"
                uevent_path = f"{drm}/{entry}/device/uevent"
                if os.path.isfile(busy_path):
                    name = "AMD GPU"
                    if os.path.isfile(name_path):
                        try:
                            with open(name_path) as f:
                                name = f.read().strip() or name
                        except Exception:
                            pass
                    elif os.path.isfile(uevent_path):
                        try:
                            with open(uevent_path) as f:
                                for line in f:
                                    if line.startswith("DRIVER=amdgpu"):
                                        name = "AMD GPU (amdgpu)"
                        except Exception:
                            pass

                    def poll(path=busy_path):
                        try:
                            with open(path) as f:
                                v = f.read().strip()
                            return max(0.0, min(100.0, float(v)))
                        except Exception:
                            return None

                    # Confirm it actually returns a number before committing
                    # to this source (some drivers expose the file but error
                    # on read, or always report a stuck value — still shown,
                    # just noted as best-effort in the tooltip).
                    if poll() is not None:
                        candidates.append({"name": name, "kind": "amd-sysfs", "poll": poll, "vendor": "amd"})
    except Exception:
        pass

    # --- Nvidia via nvidia-smi ---
    try:
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            out = subprocess.run(
                [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3,
            )
            name = out.stdout.strip().splitlines()[0].strip() if out.returncode == 0 and out.stdout.strip() else "NVIDIA GPU"

            def poll(smi=nvidia_smi):
                try:
                    r = subprocess.run(
                        [smi, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=3,
                    )
                    if r.returncode != 0:
                        return None
                    return max(0.0, min(100.0, float(r.stdout.strip().splitlines()[0])))
                except Exception:
                    return None

            if poll() is not None:
                candidates.append({"name": name, "kind": "nvidia-smi", "poll": poll, "vendor": "nvidia"})
    except Exception:
        pass

    if not candidates:
        # Intel and others: no standard cross-driver usage-percent sysfs
        # file exists at present, so we deliberately don't guess.
        return None

    if preferred_vendor:
        for c in candidates:
            if c["vendor"] == preferred_vendor:
                return c

    return candidates[0]


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class UpscaylDialog(Gtk.Dialog):
    def __init__(self, parent_title="Upscayl"):
        super().__init__(title="Upscayl", modal=True, use_header_bar=True)
        self.set_default_size(460, -1)

        self.sources = candidate_sources()
        self.settings = load_settings()
        self.result = None  # filled on OK

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        # --- Install status row ---
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.status_icon = Gtk.Image()
        self.status_label = Gtk.Label(xalign=0)
        status_box.pack_start(self.status_icon, False, False, 0)
        status_box.pack_start(self.status_label, True, True, 0)
        box.pack_start(status_box, False, False, 0)

        # --- Install source dropdown (if multiple found) ---
        src_row = self._labeled_row(
            "Install source:",
            "Which Upscayl installation to use. Detected automatically by "
            "checking system paths, Flatpak, and portable/AppImage locations. "
            "Pick a different one if you have more than one installed."
        )
        self.source_combo = Gtk.ComboBoxText()
        if self.sources:
            for s in self.sources:
                self.source_combo.append_text(s["label"])
            self.source_combo.set_active(0)
        else:
            self.source_combo.append_text("No Upscayl installation found")
            self.source_combo.set_active(0)
            self.source_combo.set_sensitive(False)
        self.source_combo.set_tooltip_text(
            "Detected Upscayl installs: system package, Flatpak, or a "
            "portable AppImage. Choose the one you want this run to use."
        )
        self.source_combo.connect("changed", self._on_source_changed)
        src_row.pack_start(self.source_combo, True, True, 0)
        box.pack_start(src_row, False, False, 0)

        recheck_btn = Gtk.Button.new_with_label("Re-check")
        recheck_btn.set_tooltip_text(
            "Scan again for Upscayl installs (system PATH, /opt, Flatpak, "
            "and portable AppImage folders)."
        )
        recheck_btn.connect("clicked", self._on_recheck)
        src_row.pack_start(recheck_btn, False, False, 0)

        # --- Scale ---
        scale_row = self._labeled_row(
            "Upscale:",
            "How many times larger the output image will be, in each "
            "dimension. This is the native scale requested from the model "
            "itself (Upscayl's CLI downscales from a 4x result for models "
            "that don't natively support the chosen scale)."
        )
        self.scale_combo = Gtk.ComboBoxText()
        for label in ["2x", "4x"]:
            self.scale_combo.append_text(label)
        saved_scale = self.settings.get("scale", "4x")
        self.scale_combo.set_active(["2x", "4x"].index(saved_scale) if saved_scale in ["2x", "4x"] else 1)
        self.scale_combo.set_tooltip_text(
            "Base upscale factor for a single Upscayl pass. Combine with "
            "'Double Upscale' below to run a second pass on the result."
        )
        self.scale_combo.connect("changed", lambda w: self._refresh_total_scale())
        scale_row.pack_start(self.scale_combo, True, True, 0)
        box.pack_start(scale_row, False, False, 0)

        # --- Double Upscale (matches Upscayl's own GUI toggle) ---
        double_row = self._labeled_row(
            "Double Upscale:",
            "Matches the 'Double Upscayl' option in the official Upscayl "
            "GUI: runs a second upscale pass on the first pass's result, "
            "at the same scale factor. Roughly doubles processing time and "
            "produces a much larger file — try a single pass first. The "
            "total scale this results in is shown to the right, live."
        )
        self.double_check = Gtk.CheckButton()
        saved_double = self.settings.get("double_upscale", "0") == "1"
        self.double_check.set_active(saved_double)
        self.double_check.set_tooltip_text(
            "Run Upscayl twice in a row on the result, compounding the "
            "scale factor (e.g. 4x then 4x again = 16x total)."
        )
        self.double_check.connect("toggled", lambda w: self._refresh_total_scale())
        double_row.pack_start(self.double_check, False, False, 0)
        self.total_scale_label = Gtk.Label(xalign=0)
        self.total_scale_label.set_tooltip_text(
            "The overall size multiplier this combination of Upscale and "
            "Double Upscale produces, updated live as you change either."
        )
        double_row.pack_start(self.total_scale_label, False, False, 0)
        box.pack_start(double_row, False, False, 0)

        # --- Model directory (custom, optional) ---
        modeldir_row = self._labeled_row(
            "Model folder:",
            "Folder containing .param/.bin model file pairs. Defaults to "
            "the selected install's bundled models folder. Add a custom "
            "folder (e.g. downloaded extra models) to include those too."
        )
        self.modeldir_chooser = Gtk.FileChooserButton(
            title="Select additional models folder",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        saved_dir = self.settings.get("custom_model_dir", "")
        if saved_dir and os.path.isdir(saved_dir):
            self.modeldir_chooser.set_filename(saved_dir)
        self.modeldir_chooser.set_tooltip_text(
            "Optional. Point this at a folder of extra downloaded models "
            "(matching .param + .bin file pairs) to merge them into the "
            "model list below, alongside the bundled ones."
        )
        modeldir_row.pack_start(self.modeldir_chooser, True, True, 0)
        clear_btn = Gtk.Button.new_with_label("Clear")
        clear_btn.set_tooltip_text("Stop using a custom model folder; show only bundled models.")
        clear_btn.connect("clicked", self._on_clear_modeldir)
        modeldir_row.pack_start(clear_btn, False, False, 0)
        self.modeldir_chooser.connect("file-set", lambda w: self._refresh_models())
        box.pack_start(modeldir_row, False, False, 0)

        # --- Model dropdown ---
        model_row = self._labeled_row(
            "Model:",
            "The AI model used for upscaling. Different models suit "
            "different content: general photos, digital art, anime/line "
            "art, or ultra-sharp detail recovery. Includes models from the "
            "bundled folder and any custom folder above."
        )
        self.model_combo = Gtk.ComboBoxText()
        model_row.pack_start(self.model_combo, True, True, 0)
        box.pack_start(model_row, False, False, 0)

        # --- GPU id (optional) ---
        gpu_row = self._labeled_row(
            "GPU id:",
            "Leave blank to auto-select. Set a number (0, 1, 2…) to force "
            "a specific Vulkan-capable GPU on multi-GPU systems. You can "
            "find your GPU IDs by running the CLI once with -v (verbose) "
            "and reading the device list it prints. Comma-separated values "
            "like '0,1' are also accepted for multi-GPU splitting."
        )
        self.gpu_entry = Gtk.Entry()
        self.gpu_entry.set_text(self.settings.get("gpu_id", ""))
        self.gpu_entry.set_placeholder_text("auto")
        self.gpu_entry.set_width_chars(6)
        self.gpu_entry.set_tooltip_text(
            "Vulkan device index to use, e.g. 0. Leave empty for automatic "
            "selection. Only needed if you have multiple GPUs and Upscayl "
            "picks the wrong one, or to split load across several GPUs "
            "(e.g. 0,1). Maps to the CLI's -g flag."
        )
        gpu_row.pack_start(self.gpu_entry, False, False, 0)
        box.pack_start(gpu_row, False, False, 0)

        # --- Tile size (advanced) ---
        tile_row = self._labeled_row(
            "Tile size:",
            "Splits the image into tiles of this pixel size before feeding "
            "each to the model, to limit GPU memory use. 0 = automatic "
            "(recommended). Lower it (e.g. 128 or 64) if you get an "
            "out-of-memory / Vulkan allocation error on large layers or a "
            "GPU with limited VRAM. Higher values may run slightly faster "
            "but use more VRAM. Maps to the CLI's -t flag."
        )
        self.tile_entry = Gtk.Entry()
        self.tile_entry.set_text(self.settings.get("tile_size", "0"))
        self.tile_entry.set_placeholder_text("0 = auto")
        self.tile_entry.set_width_chars(6)
        self.tile_entry.set_tooltip_text(
            "Tile size in pixels for processing (>=32, or 0 for automatic). "
            "Reduce this if upscaling fails with a memory/allocation error. "
            "Maps to the CLI's -t flag."
        )
        tile_row.pack_start(self.tile_entry, False, False, 0)
        box.pack_start(tile_row, False, False, 0)

        # --- Thread count (advanced) ---
        threads_row = self._labeled_row(
            "Threads:",
            "Thread counts for the load / process / save stages, as "
            "load:proc:save (e.g. 1:2:2, the Upscayl default). Higher "
            "process-thread counts can speed things up on capable GPUs "
            "but use more VRAM; leave at the default unless you know you "
            "want to tune this. Maps to the CLI's -j flag."
        )
        self.threads_entry = Gtk.Entry()
        self.threads_entry.set_text(self.settings.get("threads", ""))
        self.threads_entry.set_placeholder_text("default (1:2:2)")
        self.threads_entry.set_width_chars(10)
        self.threads_entry.set_tooltip_text(
            "Load:process:save thread counts, e.g. 1:2:2. Leave blank to "
            "use Upscayl's own default. Maps to the CLI's -j flag."
        )
        threads_row.pack_start(self.threads_entry, False, False, 0)
        box.pack_start(threads_row, False, False, 0)

        # --- TTA mode (advanced) ---
        tta_row = self._labeled_row(
            "TTA mode:",
            "Test-Time Augmentation: runs the model on several flipped/"
            "rotated versions of the image and averages the results. Can "
            "improve quality slightly, especially on noisy or ambiguous "
            "detail, at the cost of roughly 8x longer processing time. "
            "Off by default. Maps to the CLI's -x flag."
        )
        self.tta_check = Gtk.CheckButton()
        self.tta_check.set_active(self.settings.get("tta", "0") == "1")
        self.tta_check.set_tooltip_text(
            "Enable Test-Time Augmentation for slightly higher quality at "
            "roughly 8x the processing time. Maps to the CLI's -x flag."
        )
        tta_row.pack_start(self.tta_check, False, False, 0)
        box.pack_start(tta_row, False, False, 0)

        box.show_all()

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = self.add_button("Upscayl", Gtk.ResponseType.OK)
        ok_btn.get_style_context().add_class("suggested-action")

        self._refresh_status()
        self._refresh_models()
        self._refresh_total_scale()

    def _labeled_row(self, text, tooltip):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=text, xalign=0)
        label.set_size_request(120, -1)
        label.set_tooltip_text(tooltip)
        row.pack_start(label, False, False, 0)
        return row

    def _refresh_total_scale(self):
        base = 2 if self.scale_combo.get_active_text() == "2x" else 4
        total = base * base if self.double_check.get_active() else base
        self.total_scale_label.set_markup(f"→ <b>{total}x total</b>")

    def _current_source(self):
        if not self.sources:
            return None
        idx = self.source_combo.get_active()
        if idx < 0 or idx >= len(self.sources):
            return None
        return self.sources[idx]

    def _refresh_status(self):
        src = self._current_source()
        if src is None:
            self.status_icon.set_from_icon_name("dialog-warning", Gtk.IconSize.BUTTON)
            self.status_label.set_markup(
                "<b>Upscayl not found.</b> Install it (system package, "
                "Flatpak <tt>org.upscayl.Upscayl</tt>, or AppImage) and click Re-check."
            )
        else:
            self.status_icon.set_from_icon_name("emblem-ok", Gtk.IconSize.BUTTON)
            note = ""
            if src.get("is_appimage"):
                note = " — set a model folder manually below."
            self.status_label.set_markup(f"Using: <b>{GLib.markup_escape_text(src['label'])}</b>{note}")

    def _on_source_changed(self, widget):
        self._refresh_status()
        self._refresh_models()

    def _on_recheck(self, widget):
        self.sources = candidate_sources()
        self.source_combo.remove_all()
        if self.sources:
            for s in self.sources:
                self.source_combo.append_text(s["label"])
            self.source_combo.set_active(0)
            self.source_combo.set_sensitive(True)
        else:
            self.source_combo.append_text("No Upscayl installation found")
            self.source_combo.set_active(0)
            self.source_combo.set_sensitive(False)
        self._refresh_status()
        self._refresh_models()

    def _on_clear_modeldir(self, widget):
        self.modeldir_chooser.unselect_all()
        self._refresh_models()

    def _refresh_models(self):
        self.model_combo.remove_all()
        src = self._current_source()
        seen = set()
        entries = []  # (display_name, models_dir_containing_it)

        if src and src.get("models_dir"):
            for m in list_models(src["models_dir"]):
                if m not in seen:
                    seen.add(m)
                    entries.append((m, src["models_dir"]))

        custom_dir = self.modeldir_chooser.get_filename()
        if custom_dir:
            for m in list_models(custom_dir):
                if m not in seen:
                    seen.add(m)
                    entries.append((m + "  (custom)", custom_dir))
                    entries[-1] = (entries[-1][0], custom_dir)

        self._model_entries = entries  # store for lookup on OK

        if not entries:
            self.model_combo.append_text("No models found")
            self.model_combo.set_active(0)
            self.model_combo.set_sensitive(False)
            return

        self.model_combo.set_sensitive(True)
        for name, _dir in entries:
            self.model_combo.append_text(name)

        saved_model = self.settings.get("model", "")
        idx = next(
            (i for i, (n, _) in enumerate(entries) if n.replace("  (custom)", "") == saved_model),
            0,
        )
        self.model_combo.set_active(idx)

    def get_choice(self):
        src = self._current_source()
        if not src or not self._model_entries or not self.model_combo.get_sensitive():
            return None
        model_idx = self.model_combo.get_active()
        model_name, model_dir = self._model_entries[model_idx]
        model_name = model_name.replace("  (custom)", "")
        return {
            "source": src,
            "scale_label": self.scale_combo.get_active_text(),
            "double_upscale": self.double_check.get_active(),
            "model_name": model_name,
            "model_dir": model_dir,
            "out_format": "png",
            "gpu_id": self.gpu_entry.get_text().strip(),
            "tile_size": self.tile_entry.get_text().strip(),
            "threads": self.threads_entry.get_text().strip(),
            "tta": self.tta_check.get_active(),
            "custom_model_dir": self.modeldir_chooser.get_filename() or "",
        }


# ---------------------------------------------------------------------------
# Running the CLI
# ---------------------------------------------------------------------------

def build_command(src, in_path, out_path, scale_pass, model_name, model_dir,
                   gpu_id, tile_size, threads, tta):
    if src.get("flatpak_run"):
        # Use the app's own metadata-declared command name (resolved in
        # candidate_sources()), not a guessed basename — `flatpak run
        # --command=NAME` execs NAME as found on the SANDBOX's PATH,
        # which is not guaranteed to match the filename found by
        # scanning the host filesystem.
        run_cmd = src.get("flatpak_run_command") or os.path.basename(src["binary"])
        # By default a Flatpak sandbox has NO access to host files outside
        # the app itself and ~/.var/app/$FLATPAK_ID — /tmp is NOT shared
        # automatically. --filesystem=PATH grants access for just this
        # invocation (not a permanent override), scoped to the actual
        # temp directory this run uses rather than a blanket /tmp grant.
        tmp_scope = os.path.dirname(in_path)
        cmd = [
            "flatpak", "run",
            f"--filesystem={tmp_scope}",
            "--command=" + run_cmd, FLATPAK_APP_ID,
        ]
    else:
        cmd = [src["binary"]]
    cmd += ["-i", in_path, "-o", out_path, "-s", str(scale_pass), "-n", model_name]
    if model_dir:
        cmd += ["-m", model_dir]
    if gpu_id:
        cmd += ["-g", gpu_id]
    if tile_size and tile_size != "0":
        cmd += ["-t", tile_size]
    if threads:
        cmd += ["-j", threads]
    if tta:
        cmd += ["-x"]
    return cmd


_PERCENT_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$")


def run_upscayl_pass(src, in_path, out_path, scale_pass, model_name, model_dir,
                      gpu_id, tile_size, threads, tta, on_progress=None,
                      should_cancel=None):
    """Runs one Upscayl pass, streaming stdout so on_progress(pct) can be
    called live as each 'NN.NN%' line arrives (upscayl-bin prints one such
    line per tile as it works). on_progress receives a float 0-100.
    should_cancel() is polled between lines; if it returns True the process
    is terminated and a RuntimeError('cancelled') is raised."""
    cmd = build_command(src, in_path, out_path, scale_pass, model_name, model_dir,
                         gpu_id, tile_size, threads, tta)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True,
    )
    stderr_tail = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if line:
                stderr_tail.append(line)
                if len(stderr_tail) > 40:
                    stderr_tail.pop(0)
                m = _PERCENT_RE.match(line)
                if m and on_progress:
                    try:
                        on_progress(float(m.group(1)))
                    except Exception:
                        pass
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("cancelled")
    finally:
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(
            f"Upscayl exited with code {proc.returncode}.\n"
            f"Command: {' '.join(cmd)}\n"
            f"output:\n" + "\n".join(stderr_tail[-25:])
        )
    if not os.path.isfile(out_path):
        raise RuntimeError(
            f"Upscayl reported success but produced no output file.\n"
            f"Command: {' '.join(cmd)}"
        )


# ---------------------------------------------------------------------------
# Progress dialog: pixel progress bar + live CPU/GPU usage graph
# ---------------------------------------------------------------------------

GRAPH_WIDTH = 400
GRAPH_HEIGHT = 40  # "2-line height" — compact strip, not a full chart
GRAPH_HISTORY = GRAPH_WIDTH  # one sample per pixel column
SAMPLE_INTERVAL_MS = 500


class ProgressDialog(Gtk.Dialog):
    """Shown while Upscayl runs. Not user-editable — just status, a pixel
    progress bar reflecting the current pass's live %, a combined CPU/GPU
    usage strip graph, and a Cancel button."""

    def __init__(self, total_passes, gpu_id=None):
        super().__init__(title="Upscayl — working…", modal=True, use_header_bar=False)
        self.set_default_size(420, -1)
        self.set_deletable(False)  # force Cancel button, not the X, for clarity

        self.total_passes = total_passes
        self.current_pass = 1
        self.cancelled = False

        # If the user explicitly set a GPU id (rather than leaving it on
        # auto), that's a strong hint they've picked a specific card on a
        # hybrid system — prefer showing usage for the same vendor family
        # in the graph, rather than whichever GPU happened to be checked
        # first (see detect_gpu_monitor's docstring for why that mattered).
        preferred_vendor = "nvidia" if (gpu_id and shutil.which("nvidia-smi")) else None
        self.gpu_monitor = detect_gpu_monitor(preferred_vendor)  # None if nothing usable found
        self.cpu_history = []
        self.gpu_history = []

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.set_markup("<b>Starting…</b>")
        box.pack_start(self.status_label, False, False, 0)

        # --- CPU / GPU usage strip (sits above the progress bar) ---
        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        legend.pack_start(self._legend_item("#3778d6", "CPU"), False, False, 0)
        if self.gpu_monitor:
            legend.pack_start(self._legend_item("#d63737", f"GPU — {self.gpu_monitor['name']}"), False, False, 0)
        else:
            note = Gtk.Label(xalign=0)
            note.set_markup('<span size="small" foreground="#888888">GPU usage unavailable on this system</span>')
            note.set_tooltip_text(
                "No supported GPU usage source was found (AMD sysfs or "
                "nvidia-smi). CPU usage is still shown above the progress bar."
            )
            legend.pack_start(note, False, False, 0)
        box.pack_start(legend, False, False, 0)

        self.graph_area = Gtk.DrawingArea()
        self.graph_area.set_size_request(GRAPH_WIDTH, GRAPH_HEIGHT)
        self.graph_area.set_tooltip_text(
            "Live CPU (blue) and GPU (red, if available) usage while "
            "Upscayl runs, sampled twice a second."
        )
        self.graph_area.connect("draw", self._on_draw_graph)
        box.pack_start(self.graph_area, False, False, 0)

        # --- Pixel progress bar ---
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("0%")
        self.progress_bar.set_tooltip_text(
            "Live progress of the current Upscayl pass, parsed directly "
            "from Upscayl's own per-tile output."
        )
        box.pack_start(self.progress_bar, False, False, 0)

        box.show_all()
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)

        # Live sampling timer — runs regardless of which pass is active.
        self._timer_id = GLib.timeout_add(SAMPLE_INTERVAL_MS, self._on_sample_tick)
        self.connect("response", self._on_response)

    def _legend_item(self, hex_color, text):
        item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        swatch = Gtk.DrawingArea()
        swatch.set_size_request(10, 10)
        swatch.connect("draw", lambda w, cr: self._draw_swatch(cr, hex_color))
        item.pack_start(swatch, False, False, 0)
        label = Gtk.Label(label=text)
        label.set_use_markup(False)
        item.pack_start(label, False, False, 0)
        return item

    def _draw_swatch(self, cr, hex_color):
        r, g, b = self._hex_to_rgb(hex_color)
        cr.set_source_rgb(r, g, b)
        cr.arc(5, 5, 4, 0, 2 * 3.14159265)
        cr.fill()
        return False

    @staticmethod
    def _hex_to_rgb(hex_color):
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _on_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.CANCEL:
            self.cancelled = True

    def is_cancelled(self):
        return self.cancelled

    def _on_sample_tick(self):
        cpu = read_cpu_percent()
        if cpu is not None:
            self.cpu_history.append(cpu)
            if len(self.cpu_history) > GRAPH_HISTORY:
                self.cpu_history.pop(0)
        if self.gpu_monitor:
            gpu = self.gpu_monitor["poll"]()
            if gpu is not None:
                self.gpu_history.append(gpu)
                if len(self.gpu_history) > GRAPH_HISTORY:
                    self.gpu_history.pop(0)
        self.graph_area.queue_draw()
        return True  # keep the timer running

    def _on_draw_graph(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()

        # Background
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.08)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        self._draw_series(cr, self.cpu_history, w, h, "#3778d6")
        if self.gpu_monitor:
            self._draw_series(cr, self.gpu_history, w, h, "#d63737")
        return False

    def _draw_series(self, cr, history, w, h, hex_color):
        if len(history) < 2:
            return
        r, g, b = self._hex_to_rgb(hex_color)
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(1.4)
        n = len(history)
        step = w / max(1, GRAPH_HISTORY - 1)
        start_x = w - (n - 1) * step
        for i, val in enumerate(history):
            x = start_x + i * step
            y = h - (val / 100.0) * h
            if i == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

    def set_status(self, text):
        GLib.idle_add(self.status_label.set_markup, f"<b>{GLib.markup_escape_text(text)}</b>")

    def set_pass(self, pass_num, pass_label):
        self.current_pass = pass_num
        self.set_status(f"Pass {pass_num} of {self.total_passes} ({pass_label})…")

    def set_progress(self, pct_within_pass):
        # Overall progress spans all passes evenly.
        overall = ((self.current_pass - 1) + (pct_within_pass / 100.0)) / self.total_passes * 100.0
        overall = max(0.0, min(100.0, overall))

        def _update():
            self.progress_bar.set_fraction(overall / 100.0)
            self.progress_bar.set_text(f"{overall:.0f}%")
            return False
        GLib.idle_add(_update)

    def finish(self):
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def run_plugin(procedure, run_mode, image, drawables, config, run_data):
    if run_mode != Gimp.RunMode.INTERACTIVE:
        return procedure.new_return_values(
            Gimp.PDBStatusType.CALLING_ERROR,
            GLib.Error("Upscayl only supports interactive use.")
        )

    if not drawables:
        Gimp.message("No active layer selected.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

    drawable = drawables[0]
    if not isinstance(drawable, Gimp.Layer):
        Gimp.message("Please select a layer (not a channel or mask) to upscale.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

    GimpUi.init("upscayl-gimp")
    try:
        dialog = UpscaylDialog()
    except Exception:
        tb = traceback.format_exc()
        Gimp.message(f"Upscayl failed to open its dialog:\n\n{tb}")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    response = dialog.run()

    if response != Gtk.ResponseType.OK:
        dialog.destroy()
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    choice = dialog.get_choice()
    dialog.destroy()

    if choice is None:
        Gimp.message(
            "No usable Upscayl install or model was selected. "
            "Install Upscayl or point to a model folder, then try again."
        )
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

    if choice["tile_size"] and not choice["tile_size"].isdigit():
        Gimp.message(f"Tile size must be a whole number (got '{choice['tile_size']}').")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

    if choice["threads"]:
        parts = choice["threads"].split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            Gimp.message(
                f"Threads must be in load:proc:save form, e.g. 1:2:2 "
                f"(got '{choice['threads']}')."
            )
            return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

    if choice["gpu_id"] and not all(p.strip().isdigit() for p in choice["gpu_id"].split(",")):
        Gimp.message(f"GPU id must be a number or comma-separated numbers (got '{choice['gpu_id']}').")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

    save_settings({
        "scale": choice["scale_label"],
        "double_upscale": "1" if choice["double_upscale"] else "0",
        "model": choice["model_name"],
        "custom_model_dir": choice["custom_model_dir"],
        "tile_size": choice["tile_size"] or "0",
        "threads": choice["threads"],
        "tta": "1" if choice["tta"] else "0",
        "gpu_id": choice["gpu_id"],
    })

    fmt = "png"  # always PNG: lossless, and the only export call verified
                 # against current PDB argument requirements. JPEG/WEBP
                 # removed — file-jpeg-save/file-webp-save need many more
                 # mandatory PDB args (quality, smoothing, preset, etc.)
                 # that a lossy intermediate format buys nothing for an
                 # AI upscaler anyway.
    tmpdir = tempfile.mkdtemp(prefix="upscayl-gimp-")
    in_path = os.path.join(tmpdir, f"input.{fmt}")
    pass1_path = os.path.join(tmpdir, f"pass1.{fmt}")
    final_path = os.path.join(tmpdir, f"final.{fmt}")

    try:
        Gimp.progress_init("Upscayl: exporting layer…")
        # Export ONLY the selected layer's pixels — not the whole
        # composited image — by copying it into its own scratch image.
        # The real open image/layer are never modified.
        width = drawable.get_width()
        height = drawable.get_height()
        scratch = Gimp.Image.new(width, height, image.get_base_type())
        copied_layer = Gimp.Layer.new_from_drawable(drawable, scratch)
        scratch.insert_layer(copied_layer, None, -1)
        copied_layer.set_offsets(0, 0)
        scratch.flatten()
        file_obj = Gio.File.new_for_path(in_path)

        # Gimp.file_save() is a documented top-level function (not a PDB
        # lookup/config dance) that picks the right save/export handler
        # from the file's extension automatically. No procedure name to
        # get wrong, no drawable array to construct, no format-specific
        # options to guess at — options is documented as "currently
        # unused, should be set to NULL/None right now".
        saved = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, scratch, file_obj, None)
        if not saved:
            raise RuntimeError("Gimp.file_save() reported failure exporting the temp PNG for Upscayl.")
        scratch.delete()


        src = choice["source"]
        model = choice["model_name"]
        model_dir = choice["model_dir"]
        gpu_id = choice["gpu_id"]
        tile_size = choice["tile_size"]
        threads = choice["threads"]
        tta = choice["tta"]

        scale_label = choice["scale_label"]
        scale_pass = 2 if scale_label == "2x" else 4
        double = choice["double_upscale"]
        total_passes = 2 if double else 1

        progress_dialog = ProgressDialog(total_passes, gpu_id=gpu_id)
        progress_dialog.show()

        worker_error = {}

        def worker():
            try:
                if double:
                    progress_dialog.set_pass(1, scale_label)
                    run_upscayl_pass(
                        src, in_path, pass1_path, scale_pass, model, model_dir,
                        gpu_id, tile_size, threads, tta,
                        on_progress=progress_dialog.set_progress,
                        should_cancel=progress_dialog.is_cancelled,
                    )
                    progress_dialog.set_pass(2, scale_label)
                    run_upscayl_pass(
                        src, pass1_path, final_path, scale_pass, model, model_dir,
                        gpu_id, tile_size, threads, tta,
                        on_progress=progress_dialog.set_progress,
                        should_cancel=progress_dialog.is_cancelled,
                    )
                else:
                    progress_dialog.set_pass(1, scale_label)
                    run_upscayl_pass(
                        src, in_path, final_path, scale_pass, model, model_dir,
                        gpu_id, tile_size, threads, tta,
                        on_progress=progress_dialog.set_progress,
                        should_cancel=progress_dialog.is_cancelled,
                    )
            except Exception as exc:
                worker_error["exc"] = exc
            finally:
                GLib.idle_add(progress_dialog.response, Gtk.ResponseType.OK)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        progress_dialog.run()  # blocks (nested main loop) until worker signals done or user cancels
        progress_dialog.finish()
        progress_dialog.destroy()
        thread.join(timeout=10)

        if progress_dialog.is_cancelled():
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        if "exc" in worker_error:
            raise worker_error["exc"]

        total_label = f"{scale_pass * scale_pass}x total ({scale_label} ×2)" if double else scale_label

        # Load result as a new layer in the original image.
        new_layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(final_path))
        new_layer.set_name(f"{drawable.get_name()} — Upscayl {total_label} ({model})")
        image.insert_layer(new_layer, None, -1)
        Gimp.displays_flush()

    except Exception as e:
        tb = traceback.format_exc()
        Gimp.message(f"Upscayl failed:\n\n{tb}")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


class UpscaylPlugin(Gimp.PlugIn):
    def do_set_i18n(self, name):
        return False  # no translation catalog shipped; skip the lookup/warning

    def do_query_procedures(self):
        return [PLUGIN_ID]

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, run_plugin, None
        )
        procedure.set_image_types("*")
        procedure.set_sensitivity_mask(
            Gimp.ProcedureSensitivityMask.DRAWABLE
            | Gimp.ProcedureSensitivityMask.DRAWABLES
        )
        procedure.set_menu_label("Upscayl…")
        procedure.add_menu_path("<Image>/Filters/Enhance")
        procedure.set_documentation(
            "Upscale the active layer with Upscayl (AI upscaler)",
            "Runs the Upscayl CLI (upscayl-bin) against the active layer "
            "and adds the result as a new layer. Supports 2x/4x scale "
            "with an optional Double Upscale pass (matching Upscayl's own "
            "GUI toggle), any installed or custom model, tile size, "
            "thread count, TTA mode, and GPU selection. Auto-detects "
            "system, Flatpak, or AppImage installs of Upscayl.",
            PLUGIN_ID,
        )
        procedure.set_attribution("Zarir + Claude", "GPL-3.0", "2026")
        return procedure


Gimp.main(UpscaylPlugin.__gtype__, sys.argv)
