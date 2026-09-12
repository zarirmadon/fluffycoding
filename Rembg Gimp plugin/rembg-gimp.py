#!/usr/bin/env python3
"""
rembg-gimp.py — Background removal plugin for GIMP 3.x using rembg.

Install location:
    ~/.config/GIMP/3.0/plug-ins/rembg-gimp/rembg-gimp.py

The containing folder name MUST match the script name (minus .py) —
GIMP 3.x plugin discovery requires this.

After copying, make it executable:
    chmod +x ~/.config/GIMP/3.0/plug-ins/rembg-gimp/rembg-gimp.py

Then restart GIMP. Find it under: Filters > Distorts > Remove Background...

Dependencies (must be importable by the SAME Python interpreter GIMP uses —
see README.md for how to confirm this and for full CachyOS/Arch-specific
install commands, since a plain `pip install rembg` often pulls in far more
than you need):
    sudo pacman -S --needed python-numpy python-pillow python-pip
    sudo pacman -S --needed python-onnxruntime-cuda   # or -rocm for AMD, or -cpu for no GPU
    python3 -m pip install --break-system-packages --no-deps rembg
    sudo pacman -S --needed python-pymatting python-jsonschema python-scikit-image

Models download on first use to ~/.rembg/models/ (a few MB to ~1GB depending
on model — bria-rmbg and the birefnet-* variants are the largest). This
requires network access the first time each model is used; after that
they're cached locally. (Older rembg versions used ~/.u2net/ — that
directory is still read for models downloaded previously, so upgrading
never re-downloads what you already have.)

Settings (model choice, edge mode, and all parameters) persist across GIMP
sessions in ~/.config/GIMP/3.0/rembg-gimp-settings.json.
"""

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gimp, GimpUi, GObject, GLib, Gegl, Gio, Gtk, GdkPixbuf, Gdk

import sys
import os
import io
import json
import threading
import subprocess
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Dependency check — fail loudly and usefully rather than a cryptic traceback
# ---------------------------------------------------------------------------
_MISSING = []
try:
    import numpy as np
except ImportError:
    _MISSING.append("numpy")
try:
    from PIL import Image
except ImportError:
    _MISSING.append("pillow")
try:
    from rembg import remove, new_session
except ImportError:
    _MISSING.append("rembg")


# ---------------------------------------------------------------------------
# Model catalogue — name, on-disk/download size, and a plain-language guide
# for the selector help popup. Sizes are approximate.
# ---------------------------------------------------------------------------
MODELS = [
    {
        "id": "bria-rmbg",
        "label": "bria-rmbg (default, state-of-the-art)",
        "size": "~1.02 GB",
        "help": (
            "The current rembg default. State-of-the-art general-purpose "
            "quality, runs at 1024x1024 so it's slower than u2net but "
            "produces the cleanest edges of the general models. "
            "IMPORTANT LICENSING NOTE: RMBG-2.0's model weights are "
            "released under a separate BRIA license that requires a paid "
            "commercial-use agreement — this is NOT the same MIT license "
            "as rembg itself. If you're using output commercially (e.g. "
            "print sales), check BRIA's license terms before relying on "
            "this model; u2net or isnet-general-use avoid this question "
            "entirely."
        ),
    },
    {
        "id": "u2net",
        "label": "u2net (general purpose, MIT-licensed)",
        "size": "~176 MB",
        "help": (
            "The original general-purpose model. Good all-round choice for "
            "people, animals, and everyday objects on varied backgrounds. "
            "Slightly softer/blockier edges than isnet-general-use or "
            "bria-rmbg on fine detail (hair, fur), but no commercial "
            "licensing caveat — a safe default for commercial work."
        ),
    },
    {
        "id": "u2netp",
        "label": "u2netp (lightweight/fast)",
        "size": "~4.7 MB",
        "help": (
            "A pruned, much smaller/faster version of u2net. Noticeably "
            "lower quality edges — use this only for quick previews, batch "
            "throughput on weak hardware, or drafts, not final output."
        ),
    },
    {
        "id": "u2net_human_seg",
        "label": "u2net_human_seg (people, full body)",
        "size": "~176 MB",
        "help": (
            "Trained specifically on human subjects. Better than generic "
            "u2net for portraits and full-body shots — cleaner edges around "
            "skin, clothing folds. Not suitable for non-human subjects."
        ),
    },
    {
        "id": "u2net_cloth_seg",
        "label": "u2net_cloth_seg (clothing segmentation)",
        "size": "~176 MB",
        "help": (
            "Segments clothing items (upper body, lower body, full body "
            "garments) rather than doing simple foreground/background "
            "removal. Niche — use only if you specifically need garment "
            "masks, e.g. for e-commerce product shots."
        ),
    },
    {
        "id": "silueta",
        "label": "silueta (compact general purpose)",
        "size": "~43 MB",
        "help": (
            "Similar use case to u2net but a much smaller model file with "
            "a modest quality trade-off. Reasonable middle ground between "
            "u2netp's speed and u2net's quality."
        ),
    },
    {
        "id": "isnet-general-use",
        "label": "isnet-general-use (higher quality general)",
        "size": "~176 MB",
        "help": (
            "Newer architecture than u2net; sharp, clean edges on general "
            "subjects — product photography, objects, animals. A good "
            "middle ground between u2net's speed and bria-rmbg's size, "
            "with no commercial licensing caveat."
        ),
    },
    {
        "id": "isnet-anime",
        "label": "isnet-anime (anime/illustration)",
        "size": "~176 MB",
        "help": (
            "Specialised for anime-style flat-shaded illustrations and "
            "character art. Handles hard cel-shaded edges and hair much "
            "better than the general models on this kind of source. Poor "
            "choice for photographic content. If this still leaves gaps in "
            "hair spikes or thin outline strokes, try the ViTMatte edge "
            "mode below rather than switching models."
        ),
    },
    {
        "id": "sam",
        "label": "sam (Segment Anything, prompt-based)",
        "size": "~375 MB",
        "help": (
            "Meta's Segment Anything model. Very strong general segmentation "
            "but designed around point/box prompts rather than automatic "
            "foreground extraction — in rembg's default automatic mode it "
            "may behave less predictably than the purpose-built matting "
            "models above. Large download. Try it if the other models "
            "struggle with an unusual subject."
        ),
    },
    {
        "id": "birefnet-general",
        "label": "birefnet-general (high-precision general)",
        "size": "~880 MB",
        "help": (
            "State-of-the-art edge precision for general subjects — best "
            "choice for demanding work (fine hair strands, semi-transparent "
            "or fuzzy edges) if you don't mind a large download and slower "
            "inference. MIT-licensed, unlike bria-rmbg. Overkill for quick/"
            "batch work."
        ),
    },
    {
        "id": "birefnet-general-lite",
        "label": "birefnet-general-lite (BiRefNet, smaller/faster)",
        "size": "~170 MB (approx.)",
        "help": (
            "A lighter BiRefNet variant for general subjects — most of "
            "birefnet-general's edge-quality improvement over u2net at a "
            "fraction of the download/runtime cost. Good choice if "
            "birefnet-general felt too slow but isnet-general-use wasn't "
            "quite good enough on tricky edges."
        ),
    },
    {
        "id": "birefnet-portrait",
        "label": "birefnet-portrait (high-precision portraits)",
        "size": "~880 MB",
        "help": (
            "BiRefNet variant tuned specifically for portrait/headshot "
            "subjects. The best quality option for hair-strand-level "
            "portrait cutouts, at the cost of a large download and slower "
            "processing. Pair with the ViTMatte edge mode below for the "
            "most detail on wispy hair."
        ),
    },
    {
        "id": "birefnet-dis",
        "label": "birefnet-dis (dichotomous segmentation)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained for dichotomous image segmentation — very "
            "precise foreground/background separation on complex, "
            "high-detail subjects. Niche/specialist choice; try "
            "birefnet-general first unless you have a specific reason to "
            "reach for this one."
        ),
    },
    {
        "id": "birefnet-hrsod",
        "label": "birefnet-hrsod (high-resolution salient objects)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained for high-resolution salient object "
            "detection — tuned for large, detailed source images where "
            "the main subject should be identified precisely at full "
            "resolution. Specialist choice for very large source photos."
        ),
    },
    {
        "id": "birefnet-cod",
        "label": "birefnet-cod (concealed/camouflaged objects)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained for concealed object detection (COD) — "
            "designed to find subjects that visually blend into their "
            "background (camouflage-style low contrast). Unlikely to be "
            "useful for typical product/portrait/art cutouts; a research/"
            "niche model included for completeness."
        ),
    },
    {
        "id": "birefnet-massive",
        "label": "birefnet-massive (large training set)",
        "size": "~880 MB (approx.)",
        "help": (
            "BiRefNet trained on a larger, more varied dataset than "
            "birefnet-general. May generalise slightly better to unusual "
            "subjects at the same size/speed cost. Worth trying if "
            "birefnet-general's result is close but not quite right."
        ),
    },
]

MODEL_IDS = [m["id"] for m in MODELS]
MODEL_LABELS = [m["label"] for m in MODELS]


# ---------------------------------------------------------------------------
# Edge modes — rembg's four ways to turn a mask into a cutout. These are
# mutually exclusive (rembg applies at most one), not stackable.
# ---------------------------------------------------------------------------
EDGE_MODES = [
    {
        "id": "naive",
        "label": "Naive (default, fastest)",
        "help": (
            "No edge refinement. Best for hard-edged subjects (products, "
            "logos, screenshots, flat illustration) or when the background "
            "was already close in color to the subject — there's nothing "
            "to correct, so refinement buys nothing. Free."
        ),
    },
    {
        "id": "decontaminate",
        "label": "Decontaminate (fix color fringing, cheap)",
        "help": (
            "Fixes a colored halo left around hair/fur/soft edges when the "
            "subject was shot against a strongly colored background (green "
            "grass, blue sky) — that color bleeds into semi-transparent "
            "edge pixels and stays visible as a rim after cutout. This "
            "only corrects color, never the mask shape/coverage. Cheap "
            "enough to leave on for a whole batch. Does NOT fix holes or "
            "gaps in the mask — for that, use Alpha matting or ViTMatte."
        ),
    },
    {
        "id": "alpha_matting",
        "label": "Alpha matting (fix mask shape, slow)",
        "help": (
            "Re-estimates which pixels are foreground/background/soft-edge "
            "using a closed-form solver — fixes actual shape errors like "
            "the model cutting through hair strands or leaving small holes "
            "in the mask, not just color fringing. Much slower than "
            "Decontaminate. Can occasionally fail to converge, in which "
            "case rembg falls back to a plain decontaminated cutout. "
            "Works best on the older models (u2net, u2netp, silueta) — "
            "newer models (bria-rmbg, birefnet-*, isnet-*) already "
            "produce well-shaped masks and rarely need this."
        ),
    },
    {
        "id": "vitmatte",
        "label": "ViTMatte (best detail recovery, slow + extra download)",
        "help": (
            "Solves the same problem as Alpha matting — wrong mask shape, "
            "holes, clipped hair strands — but predicts the alpha with a "
            "neural network instead of a solver, so it recovers more wispy "
            "detail and cannot fail to converge. Costs an extra ~110-380MB "
            "download on first use (checkpoint-dependent) and runs slower "
            "than Alpha matting. If you're seeing gaps punched through "
            "hair spikes or thin outline strokes on anime/illustration "
            "art, THIS is usually the most reliable fix — try it before "
            "switching models."
        ),
    },
]
EDGE_MODE_IDS = [m["id"] for m in EDGE_MODES]

VITMATTE_CHECKPOINTS = [
    {
        "id": "small-distinctions-646",
        "label": "small-distinctions-646 (default, best quality/byte)",
        "size": "~110 MB",
    },
    {
        "id": "small-composition-1k",
        "label": "small-composition-1k (synthetic composites)",
        "size": "~110 MB",
    },
    {
        "id": "base-distinctions-646",
        "label": "base-distinctions-646 (more detail, ~2.5x runtime)",
        "size": "~380 MB",
    },
    {
        "id": "base-composition-1k",
        "label": "base-composition-1k (larger, synthetic training set)",
        "size": "~380 MB",
    },
]
VITMATTE_CHECKPOINT_IDS = [c["id"] for c in VITMATTE_CHECKPOINTS]


# ---------------------------------------------------------------------------
# Settings persistence — remembers the last-used model and all parameters
# across GIMP sessions. Stored as plain JSON next to GIMP's own config, not
# inside the plugin folder (which some package managers may treat as
# read-only or wipe on plugin update).
# ---------------------------------------------------------------------------

SETTINGS_PATH = os.path.join(
    GLib.get_user_config_dir(), "GIMP", "3.0", "rembg-gimp-settings.json")

DEFAULT_SETTINGS = {
    "model_id": "bria-rmbg",
    "edge_mode": "naive",
    "alpha_matting_foreground_threshold": 240,
    "alpha_matting_background_threshold": 10,
    "alpha_matting_erode_size": 10,
    "vitmatte_model": "small-distinctions-646",
    "post_process_mask": False,
    "only_mask": False,
}


def load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            saved = json.load(f)
        settings = dict(DEFAULT_SETTINGS)
        settings.update({k: v for k, v in saved.items()
                          if k in DEFAULT_SETTINGS})
        # Guard against a stale saved model_id/edge_mode/vitmatte_model that
        # no longer exists (e.g. after this plugin is updated and a model
        # is renamed or removed) — fall back to the default rather than
        # crashing the dialog on an invalid combo index.
        if settings["model_id"] not in MODEL_IDS:
            settings["model_id"] = DEFAULT_SETTINGS["model_id"]
        if settings["edge_mode"] not in EDGE_MODE_IDS:
            settings["edge_mode"] = DEFAULT_SETTINGS["edge_mode"]
        if settings["vitmatte_model"] not in VITMATTE_CHECKPOINT_IDS:
            settings["vitmatte_model"] = DEFAULT_SETTINGS["vitmatte_model"]
        return settings
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        # Non-fatal: settings just won't persist this time. Don't interrupt
        # the user's actual background-removal workflow over this.
        pass


# ---------------------------------------------------------------------------
# Parameter definitions with hover help text (rembg's `remove()` kwargs)
# ---------------------------------------------------------------------------
PARAM_HELP = {
    "alpha_matting_foreground_threshold": (
        "0-255. Pixels with an initial mask confidence above this value "
        "are treated as 'definitely foreground' seeds for the matting "
        "algorithm. Higher = more conservative (only very confident "
        "pixels seed the foreground), which can shrink thin details. "
        "Default 240."
    ),
    "alpha_matting_background_threshold": (
        "0-255. Pixels with mask confidence below this value are treated "
        "as 'definitely background' seeds. Lower = more conservative "
        "about calling something background. Default 10."
    ),
    "alpha_matting_erode_size": (
        "Pixel radius used to erode the foreground/background seed masks "
        "before matting, creating an 'unknown' region for the algorithm to "
        "resolve. Larger values give the matting algorithm more room to "
        "work on soft edges (e.g. hair) but cost more time. Default 10."
    ),
    "only_mask": (
        "Output the alpha mask itself (as a greyscale image) instead of "
        "the cutout RGBA image. Useful if you want to hand-edit the mask "
        "in GIMP before applying it, rather than trusting the automatic "
        "cutout directly."
    ),
    "post_process_mask": (
        "Applies a morphological clean-up pass to the mask (removes small "
        "holes/islands, smooths the boundary slightly). Usually a small "
        "quality improvement with negligible extra cost; occasionally "
        "over-smooths fine detail like individual hair strands."
    ),
    "background_color": (
        "Instead of transparency, composite the cutout over a solid RGBA "
        "colour. Leave disabled to keep a transparent background (normal "
        "use case for compositing in GIMP)."
    ),
}


def check_dependencies_or_error():
    if _MISSING:
        msg = (
            "Missing Python packages for the interpreter GIMP is using: "
            + ", ".join(_MISSING)
            + ".\n\nInstall them for GIMP's Python (see plugin header "
            "comment for how to find the right interpreter), then restart "
            "GIMP."
        )
        Gimp.message(msg)
        return False
    return True


# ---------------------------------------------------------------------------
# rembg version check — queries PyPI for the latest published version and
# compares against what's installed. Does NOT run pip itself; only tells
# the user what command to run and copies/shows it, since silently
# upgrading a package GIMP has already imported this session is unsafe
# (Python doesn't reliably re-import an upgraded package without a
# restart) and an unattended upgrade could change rembg's own behaviour
# with no one there to notice if something breaks.
# ---------------------------------------------------------------------------

def get_installed_rembg_version():
    try:
        import rembg as _rembg_mod
        return getattr(_rembg_mod, "__version__", "unknown")
    except ImportError:
        return None


def get_latest_pypi_version(package_name, timeout_seconds=6):
    """
    Queries PyPI's JSON API directly rather than shelling out to
    `pip index versions` (which pip itself documents as an experimental
    command that may change/be removed without notice). Returns the
    latest version string, or None if the check fails for any reason
    (no network, PyPI unreachable, unexpected response shape) — a failed
    check should never block or alarm the user, it just means "unknown".
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("info", {}).get("version")
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, KeyError, TimeoutError, OSError):
        return None


def build_upgrade_command():
    """
    The exact pip command the user should run themselves. Deliberately
    matches the --no-deps install approach used in the README/header, so
    an upgrade doesn't reopen the large unrelated dependency pull that
    --no-deps was specifically chosen to avoid.
    """
    return f"{sys.executable} -m pip install --break-system-packages --upgrade --no-deps rembg"


# ---------------------------------------------------------------------------
# CPU / GPU execution provider detection
# ---------------------------------------------------------------------------

_GPU_PROVIDERS = (
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "TensorrtExecutionProvider",
    "DmlExecutionProvider",       # DirectML (Windows, irrelevant on CachyOS
                                   # but harmless to check)
    "OpenVINOExecutionProvider",
)

_gpu_notice_shown = False  # only nag once per GIMP session


def detect_execution_provider():
    """
    Returns (active_provider_str, is_gpu_bool, advice_str_or_None).
    advice_str is a ready-to-show install hint, only populated when no GPU
    provider is available at all.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return "unknown (onnxruntime not importable)", False, None

    available = ort.get_available_providers()
    gpu_available = [p for p in available if p in _GPU_PROVIDERS]

    if gpu_available:
        # onnxruntime uses the first provider in its priority list that's
        # available; rembg lets it pick automatically, so report the
        # highest-priority GPU provider found as "active".
        active = gpu_available[0]
        return active, True, None

    advice = (
        "rembg/onnxruntime is running on CPU only — no GPU execution "
        "provider was found. This works fine but is noticeably slower, "
        "especially with the larger models (isnet, sam, birefnet-*).\n\n"
        "To enable GPU acceleration on CachyOS/Arch:\n"
        "  • NVIDIA (CUDA): sudo pacman -S python-onnxruntime-cuda\n"
        "  • AMD (ROCm):    sudo pacman -S python-onnxruntime-rocm\n"
        "    (or python-onnxruntime-opt-rocm, which adds AVX2 CPU "
        "optimizations alongside ROCm)\n\n"
        "Only one onnxruntime variant should be installed at a time — "
        "remove the plain 'python-onnxruntime' (or pip 'onnxruntime') "
        "first if switching, since they conflict."
    )
    return "CPUExecutionProvider", False, advice


# ---------------------------------------------------------------------------
# Core conversion helpers: GIMP layer <-> PIL Image, bit-depth aware
# ---------------------------------------------------------------------------

def layer_to_pil(layer):
    """
    Export the given layer's pixel data to a PIL Image, preserving effective
    bit depth semantics. rembg/onnxruntime models operate on 8-bit RGB(A)
    internally regardless of source precision, so for 16/32-bit float
    images we downsample to 8-bit for the network, then apply the resulting
    alpha mask back onto the ORIGINAL full-precision data — this way you
    don't lose bit depth in the parts of the image that matter (colour),
    only the mask derivation uses 8-bit.
    """
    image = layer.get_image()
    precision = image.get_precision()
    is_high_bit_depth = precision not in (
        Gimp.Precision.U8_LINEAR,
        Gimp.Precision.U8_NON_LINEAR,
        Gimp.Precision.U8_PERCEPTUAL,
    )

    width = layer.get_width()
    height = layer.get_height()

    # Duplicate + flatten-safe export via a temporary PNG buffer at 8-bit,
    # using GIMP's own export machinery so colour management / precision
    # conversion is handled correctly rather than hand-rolled.
    tmp_export = GLib.get_tmp_dir() + "/gimp_rembg_src.png"
    dup_image = image.duplicate()
    # Ensure 8-bit for the exported preview/network input copy only.
    # Guard against converting an image that's already at (or past) 8-bit
    # non-linear precision — GIMP raises an error if the target precision
    # equals the current one, so only convert when actually needed.
    if precision != Gimp.Precision.U8_NON_LINEAR:
        dup_image.convert_precision(Gimp.Precision.U8_NON_LINEAR)
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup_image,
                    Gio.File.new_for_path(tmp_export), None)
    dup_image.delete()

    pil_img = Image.open(tmp_export).convert("RGBA")
    return pil_img, is_high_bit_depth, precision


def pil_mask_to_new_layer(image, original_layer, alpha_mask_pil, layer_name):
    """
    Take the alpha channel produced by rembg (as a PIL 'L' mask, same pixel
    dimensions as the source) and apply it as a new RGBA layer built from
    the ORIGINAL layer's full-precision pixel data, so 16/32-bit colour
    data is preserved even though the mask itself was computed at 8-bit.

    NOTE ON RELIABILITY: this function has NOT been exercised against a
    live GIMP 3.x instance (no GIMP install was available while writing
    this). The Gimp.Layer / mask / edit_paste call sequence for GIMP 3.x's
    GObject-Introspection API has shifted across dev releases, and this is
    the single most likely place the plugin breaks. If it does, see the
    "If the mask step fails" section of the README for a simpler, more
    verbose fallback that swaps this block for direct pixel-region writes.
    """
    new_layer = Gimp.Layer.new_from_drawable(original_layer, image)
    new_layer.set_name(layer_name)
    image.insert_layer(new_layer, None, -1)
    if not new_layer.has_alpha():
        new_layer.add_alpha()

    width = new_layer.get_width()
    height = new_layer.get_height()

    mask_tmp_path = GLib.get_tmp_dir() + "/gimp_rembg_mask.png"
    alpha_mask_pil.resize((width, height)).save(mask_tmp_path)

    layer_mask = new_layer.create_mask(Gimp.AddMaskType.WHITE)
    new_layer.add_mask(layer_mask)

    mask_load_image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                      Gio.File.new_for_path(mask_tmp_path))
    # Gimp.Image has no get_active_drawable() method in the 3.x API — a
    # freshly loaded image's layer is fetched via get_layers() instead.
    mask_load_layers = mask_load_image.get_layers()
    mask_load_layer = mask_load_layers[0]
    if mask_load_layer.get_width() != width or mask_load_layer.get_height() != height:
        mask_load_layer.resize(width, height, 0, 0)

    # Select-all on the source, copy, paste onto the mask, anchor.
    # NOTE: Gimp.edit_copy() in the 3.x API takes an ARRAY of drawables
    # (matching the C signature gimp_edit_copy(num_drawables, drawables[])),
    # not a single drawable object — passing a bare Layer raises
    # "Must be sequence, not Layer".
    Gimp.Selection.all(mask_load_image)
    Gimp.edit_copy([mask_load_layer])
    pasted_layers = Gimp.edit_paste(layer_mask, False)
    if pasted_layers and len(pasted_layers) > 0:
        floating_layer = pasted_layers[0]
        Gimp.floating_sel_anchor(floating_layer)
    mask_load_image.delete()

    # NOTE: Gimp.Layer has no apply_mask() method — that was a guess that
    # doesn't exist in the 3.x API. The real equivalent is
    # layer.remove_mask(Gimp.MaskApplyMode.APPLY), which bakes the mask
    # into the layer's alpha channel and deletes the mask object.
    # Deliberately NOT doing that here: leaving the mask live and editable
    # is more useful for a background-removal result, since it lets you
    # hand-touch-up the cutout edge non-destructively afterwards (paint on
    # the mask in black/white) rather than having it already flattened.
    # Clear the selection so it doesn't linger visibly on the new layer.
    Gimp.Selection.none(image)
    Gimp.displays_flush()
    return new_layer


# ---------------------------------------------------------------------------
# Background removal worker (runs off the UI thread)
# ---------------------------------------------------------------------------

class RembgWorker:
    _session_cache = {}
    last_provider_info = None  # (provider_str, is_gpu_bool) from most recent run

    @classmethod
    def get_session(cls, model_id, progress_cb=None):
        if model_id not in cls._session_cache:
            if progress_cb:
                progress_cb(f"Downloading/loading model '{model_id}'... "
                             f"(first use only, cached after)")
            cls._session_cache[model_id] = new_session(model_id)

            global _gpu_notice_shown
            provider, is_gpu, advice = detect_execution_provider()
            cls.last_provider_info = (provider, is_gpu)
            if advice and not _gpu_notice_shown:
                _gpu_notice_shown = True
                GLib.idle_add(lambda: Gimp.message(advice) or False)
        return cls._session_cache[model_id]

    @classmethod
    def run(cls, pil_image, model_id, params, progress_cb=None):
        session = cls.get_session(model_id, progress_cb)
        if progress_cb:
            progress_cb("Running background removal...")

        # Edge mode is mutually exclusive: naive / decontaminate /
        # alpha_matting / vitmatte. Passing more than one of the
        # decontaminate/alpha_matting/vitmatte flags at once doesn't error,
        # but rembg only honours one (vitmatte takes precedence over
        # alpha_matting internally), so only ever set the one the dialog
        # actually selected to keep behaviour unambiguous.
        edge_mode = params.get("edge_mode", "naive")
        kwargs = dict(
            session=session,
            only_mask=params.get("only_mask", False),
            post_process_mask=params.get("post_process_mask", False),
        )

        if edge_mode == "decontaminate":
            kwargs["decontaminate"] = True
        elif edge_mode == "alpha_matting":
            kwargs["alpha_matting"] = True
            kwargs["alpha_matting_foreground_threshold"] = params.get(
                "alpha_matting_foreground_threshold", 240)
            kwargs["alpha_matting_background_threshold"] = params.get(
                "alpha_matting_background_threshold", 10)
            kwargs["alpha_matting_erode_size"] = params.get(
                "alpha_matting_erode_size", 10)
        elif edge_mode == "vitmatte":
            kwargs["vitmatte"] = True
            kwargs["vitmatte_model"] = params.get(
                "vitmatte_model", "small-distinctions-646")
        # edge_mode == "naive": no extra kwargs, rembg's default behaviour.

        bgcolor = params.get("background_color")
        if bgcolor:
            kwargs["bgcolor"] = bgcolor
        result = remove(pil_image, **kwargs)
        return result


# ---------------------------------------------------------------------------
# Dialog UI
# ---------------------------------------------------------------------------

class RembgDialog(Gtk.Dialog):
    def __init__(self, image, layer):
        Gtk.Dialog.__init__(self, title="Remove Background (rembg)")
        self.set_default_size(760, 680)
        self.image = image
        self.layer = layer
        self.result_pil = None
        self.preview_pixbuf_before = None
        self.preview_pixbuf_after = None
        self.settings = load_settings()

        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("_Apply to New Layer", Gtk.ResponseType.OK)

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_border_width(10)

        # --- Model selector row ---------------------------------------
        model_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        model_label = Gtk.Label(label="Model:")
        self.model_combo = Gtk.ComboBoxText()
        for m in MODELS:
            self.model_combo.append_text(f"{m['label']}  [{m['size']}]")
        saved_model_idx = MODEL_IDS.index(self.settings["model_id"])
        self.model_combo.set_active(saved_model_idx)
        self.model_combo.set_tooltip_text(
            "Segmentation model to use. Larger models are generally more "
            "accurate but slower and (on first use) require a bigger "
            "download. Click the '?' for a full comparison. Your choice "
            "is remembered for next time."
        )
        model_help_btn = Gtk.Button(label="?")
        model_help_btn.set_tooltip_text("Which model should I use?")
        model_help_btn.connect("clicked", self.on_model_help_clicked)

        model_row.pack_start(model_label, False, False, 0)
        model_row.pack_start(self.model_combo, True, True, 0)
        model_row.pack_start(model_help_btn, False, False, 0)
        content.pack_start(model_row, False, False, 0)

        # --- Update check row -------------------------------------------
        update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=6)
        self.update_check_btn = Gtk.Button(label="Check for rembg updates")
        self.update_check_btn.set_tooltip_text(
            "Checks PyPI for a newer rembg release. Does not install "
            "anything automatically — if an update is available, you'll "
            "get the exact command to run yourself, since applying an "
            "upgrade while GIMP has already loaded the current version "
            "can require a GIMP restart to take effect."
        )
        self.update_check_btn.connect("clicked", self.on_check_updates_clicked)
        self.update_status_label = Gtk.Label(label="")
        self.update_status_label.set_xalign(0)
        update_row.pack_start(self.update_check_btn, False, False, 0)
        update_row.pack_start(self.update_status_label, True, True, 0)
        content.pack_start(update_row, False, False, 0)

        # --- Edge mode row (naive / decontaminate / alpha matting / ViTMatte)
        edge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        edge_label = Gtk.Label(label="Edge mode:")
        self.edge_mode_combo = Gtk.ComboBoxText()
        for em in EDGE_MODES:
            self.edge_mode_combo.append_text(em["label"])
        saved_edge_idx = EDGE_MODE_IDS.index(self.settings["edge_mode"])
        self.edge_mode_combo.set_active(saved_edge_idx)
        self.edge_mode_combo.set_tooltip_text(
            "How rembg turns the raw mask into a cutout. These four modes "
            "are mutually exclusive — only one applies at a time. If your "
            "cutouts have gaps/holes or clipped hair strands, try ViTMatte "
            "or Alpha matting rather than switching models. Click '?' for "
            "guidance on which to pick."
        )
        self.edge_mode_combo.connect("changed", self.on_edge_mode_changed)
        edge_help_btn = Gtk.Button(label="?")
        edge_help_btn.set_tooltip_text("Which edge mode should I use?")
        edge_help_btn.connect("clicked", self.on_edge_mode_help_clicked)

        edge_row.pack_start(edge_label, False, False, 0)
        edge_row.pack_start(self.edge_mode_combo, True, True, 0)
        edge_row.pack_start(edge_help_btn, False, False, 0)
        content.pack_start(edge_row, False, False, 0)

        # --- Parameters (with hover tooltips) --------------------------
        params_frame = Gtk.Frame(label="Parameters")
        params_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        params_box.set_border_width(8)

        # Alpha matting sub-parameters (shown only when edge mode ==
        # alpha_matting)
        self.alpha_matting_params_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.spin_fg_thresh = self._spin_row(
            self.alpha_matting_params_box, "Foreground threshold",
            PARAM_HELP["alpha_matting_foreground_threshold"],
            0, 255, self.settings["alpha_matting_foreground_threshold"])
        self.spin_bg_thresh = self._spin_row(
            self.alpha_matting_params_box, "Background threshold",
            PARAM_HELP["alpha_matting_background_threshold"],
            0, 255, self.settings["alpha_matting_background_threshold"])
        self.spin_erode = self._spin_row(
            self.alpha_matting_params_box, "Erode size",
            PARAM_HELP["alpha_matting_erode_size"],
            0, 100, self.settings["alpha_matting_erode_size"])
        params_box.pack_start(self.alpha_matting_params_box, False, False, 0)

        # ViTMatte checkpoint selector (shown only when edge mode ==
        # vitmatte)
        self.vitmatte_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vitmatte_label = Gtk.Label(label="ViTMatte checkpoint:")
        self.vitmatte_combo = Gtk.ComboBoxText()
        for vc in VITMATTE_CHECKPOINTS:
            self.vitmatte_combo.append_text(f"{vc['label']}  [{vc['size']}]")
        saved_vm_idx = VITMATTE_CHECKPOINT_IDS.index(
            self.settings["vitmatte_model"])
        self.vitmatte_combo.set_active(saved_vm_idx)
        self.vitmatte_combo.set_tooltip_text(
            "Which ViTMatte model checkpoint to use for edge refinement. "
            "'small' checkpoints are the default and best quality-per-byte; "
            "'base' checkpoints recover slightly more detail at ~2.5x the "
            "runtime and a larger download."
        )
        self.vitmatte_row.pack_start(vitmatte_label, False, False, 0)
        self.vitmatte_row.pack_start(self.vitmatte_combo, True, True, 0)
        params_box.pack_start(self.vitmatte_row, False, False, 0)

        self.chk_post_process = self._checkbox_row(
            params_box, "Post-process mask (clean up edges)",
            PARAM_HELP["post_process_mask"],
            default=self.settings["post_process_mask"])

        self.chk_only_mask = self._checkbox_row(
            params_box, "Output mask only (greyscale, no cutout)",
            PARAM_HELP["only_mask"],
            default=self.settings["only_mask"])

        params_frame.add(params_box)
        content.pack_start(params_frame, False, False, 0)

        # Show/hide the right sub-parameter row for the saved edge mode
        self._update_edge_mode_visibility()

        # --- Preview area: before / after side by side ------------------
        preview_frame = Gtk.Frame(label="Preview")
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=6)
        preview_box.set_border_width(8)

        before_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        before_col.pack_start(Gtk.Label(label="Before"), False, False, 0)
        self.before_image_widget = Gtk.Image()
        before_col.pack_start(self.before_image_widget, True, True, 0)

        after_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        after_col.pack_start(Gtk.Label(label="After"), False, False, 0)
        self.after_image_widget = Gtk.Image()
        after_col.pack_start(self.after_image_widget, True, True, 0)

        preview_box.pack_start(before_col, True, True, 0)
        preview_box.pack_start(after_col, True, True, 0)
        preview_frame.add(preview_box)
        content.pack_start(preview_frame, True, True, 0)

        # --- Preview button + status -------------------------------
        controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=6)
        self.preview_btn = Gtk.Button(label="Generate Preview")
        self.preview_btn.connect("clicked", self.on_preview_clicked)
        self.status_label = Gtk.Label(label="")
        controls_row.pack_start(self.preview_btn, False, False, 0)
        controls_row.pack_start(self.status_label, False, False, 0)
        content.pack_start(controls_row, False, False, 0)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        content.pack_start(self.progress, False, False, 0)

        content.show_all()

        # Load the "before" thumbnail immediately
        self._load_before_preview()

    # -- UI helper builders -------------------------------------------

    def _checkbox_row(self, box, label, help_text, default=False):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chk = Gtk.CheckButton(label=label)
        chk.set_active(default)
        chk.set_tooltip_text(help_text)
        row.pack_start(chk, True, True, 0)
        box.pack_start(row, False, False, 0)
        return chk

    def _spin_row(self, box, label, help_text, lo, hi, default):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=label)
        lbl.set_tooltip_text(help_text)
        adj = Gtk.Adjustment(value=default, lower=lo, upper=hi,
                              step_increment=1, page_increment=10)
        spin = Gtk.SpinButton(adjustment=adj)
        spin.set_tooltip_text(help_text)
        row.pack_start(lbl, True, True, 0)
        row.pack_start(spin, False, False, 0)
        box.pack_start(row, False, False, 0)
        return spin

    def on_edge_mode_changed(self, combo):
        self._update_edge_mode_visibility()

    def _update_edge_mode_visibility(self):
        edge_mode = self._current_edge_mode_id()
        self.alpha_matting_params_box.set_visible(
            edge_mode == "alpha_matting")
        self.vitmatte_row.set_visible(edge_mode == "vitmatte")

    def on_model_help_clicked(self, btn):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Which model should I use?"
        )
        lines = []
        for m in MODELS:
            lines.append(f"• {m['label']}  [{m['size']}]\n   {m['help']}\n")
        dlg.format_secondary_text("\n".join(lines))
        dlg.set_default_size(600, 500)
        dlg.run()
        dlg.destroy()

    def on_edge_mode_help_clicked(self, btn):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Which edge mode should I use?"
        )
        lines = []
        for em in EDGE_MODES:
            lines.append(f"• {em['label']}\n   {em['help']}\n")
        dlg.format_secondary_text("\n".join(lines))
        dlg.set_default_size(600, 500)
        dlg.run()
        dlg.destroy()

    def on_check_updates_clicked(self, btn):
        self.update_check_btn.set_sensitive(False)
        self.update_status_label.set_text("Checking...")

        def worker_thread():
            installed = get_installed_rembg_version()
            latest = get_latest_pypi_version("rembg")
            GLib.idle_add(self._on_update_check_done, installed, latest)

        threading.Thread(target=worker_thread, daemon=True).start()

    def _on_update_check_done(self, installed, latest):
        self.update_check_btn.set_sensitive(True)

        if latest is None:
            self.update_status_label.set_text(
                "Couldn't reach PyPI to check (no network, or PyPI "
                "unreachable).")
            return False

        if installed is None:
            self.update_status_label.set_text(
                f"Latest on PyPI: {latest}. Could not read your installed "
                f"version.")
            return False

        if installed == latest:
            self.update_status_label.set_text(
                f"Up to date (rembg {installed}).")
            return False

        # An update is available. Never run pip ourselves — show the exact
        # command and let the user run it, since:
        #  1. GIMP has already imported this session's rembg module, and
        #     Python doesn't reliably pick up an upgraded package without
        #     restarting the process that imported it, so an in-place
        #     upgrade wouldn't take effect until GIMP is restarted anyway.
        #  2. Running package-manager commands on the user's system without
        #     an explicit, visible confirmation step isn't something this
        #     plugin should do quietly.
        cmd = build_upgrade_command()
        self.update_status_label.set_text(
            f"Update available: {installed} → {latest}")

        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=f"rembg update available: {installed} → {latest}"
        )
        dlg.format_secondary_text(
            "Run this command in a terminal, then restart GIMP for it to "
            "take effect:\n\n"
            f"{cmd}\n\n"
            "This upgrades rembg only (--no-deps), matching how it was "
            "originally installed, so it won't pull in unrelated packages. "
            "If you installed rembg differently (e.g. via your distro's "
            "package manager instead of pip), use that same method to "
            "upgrade instead."
        )
        dlg.set_default_size(560, 260)

        # Make the command easy to copy: a selectable, read-only entry
        # field the user can click into and Ctrl+C, since MessageDialog's
        # secondary text often isn't selectable in all GTK themes.
        entry = Gtk.Entry()
        entry.set_text(cmd)
        entry.set_editable(False)
        entry.set_can_focus(True)
        content_area = dlg.get_content_area()
        content_area.pack_end(entry, False, False, 6)
        entry.show()

        dlg.run()
        dlg.destroy()
        return False

    # -- Preview handling ------------------------------------------------

    def _load_before_preview(self):
        tmp_path = GLib.get_tmp_dir() + "/gimp_rembg_before_preview.png"
        dup = self.image.duplicate()
        if dup.get_precision() != Gimp.Precision.U8_NON_LINEAR:
            dup.convert_precision(Gimp.Precision.U8_NON_LINEAR)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup,
                        Gio.File.new_for_path(tmp_path), None)
        dup.delete()
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp_path)
        pixbuf = self._scale_for_preview(pixbuf)
        self.before_image_widget.set_from_pixbuf(pixbuf)

    def _scale_for_preview(self, pixbuf, max_dim=320):
        w, h = pixbuf.get_width(), pixbuf.get_height()
        scale = min(max_dim / w, max_dim / h, 1.0)
        if scale < 1.0:
            pixbuf = pixbuf.scale_simple(int(w * scale), int(h * scale),
                                          GdkPixbuf.InterpType.BILINEAR)
        return pixbuf

    def _current_model_id(self):
        idx = self.model_combo.get_active()
        return MODEL_IDS[idx]

    def _current_edge_mode_id(self):
        idx = self.edge_mode_combo.get_active()
        return EDGE_MODE_IDS[idx]

    def _current_vitmatte_checkpoint_id(self):
        idx = self.vitmatte_combo.get_active()
        return VITMATTE_CHECKPOINT_IDS[idx]

    def _current_params(self):
        return {
            "edge_mode": self._current_edge_mode_id(),
            "alpha_matting_foreground_threshold":
                int(self.spin_fg_thresh.get_value()),
            "alpha_matting_background_threshold":
                int(self.spin_bg_thresh.get_value()),
            "alpha_matting_erode_size": int(self.spin_erode.get_value()),
            "vitmatte_model": self._current_vitmatte_checkpoint_id(),
            "only_mask": self.chk_only_mask.get_active(),
            "post_process_mask": self.chk_post_process.get_active(),
        }

    def _save_current_settings(self):
        settings = dict(self._current_params())
        settings["model_id"] = self._current_model_id()
        save_settings(settings)

    def on_preview_clicked(self, btn):
        if not check_dependencies_or_error():
            return
        self.preview_btn.set_sensitive(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text("Starting...")
        model_id = self._current_model_id()
        params = self._current_params()

        def progress_cb(msg):
            GLib.idle_add(self._set_status, msg)

        def worker_thread():
            try:
                pil_src, is_hbd, precision = layer_to_pil(self.layer)
                # Downscale for a fast preview only; full-res run happens
                # on Apply.
                preview_src = pil_src.copy()
                preview_src.thumbnail((512, 512))
                result = RembgWorker.run(preview_src, model_id, params,
                                          progress_cb)
                GLib.idle_add(self._on_preview_ready, result, None)
            except Exception as e:
                GLib.idle_add(self._on_preview_ready, None, str(e))

        threading.Thread(target=worker_thread, daemon=True).start()

    def _set_status(self, msg):
        self.status_label.set_text(msg)
        self.progress.pulse()
        return False

    def _on_preview_ready(self, result_pil, error):
        self.preview_btn.set_sensitive(True)
        self.progress.set_fraction(1.0 if not error else 0.0)
        if error:
            self.progress.set_text("Failed")
            Gimp.message(f"rembg preview failed: {error}")
            return False
        self.progress.set_text("Preview ready")
        provider_str = ""
        if RembgWorker.last_provider_info:
            provider, is_gpu = RembgWorker.last_provider_info
            provider_str = f"  ·  {'GPU' if is_gpu else 'CPU'} ({provider})"
        self.status_label.set_text(f"Preview ready{provider_str}")

        buf = io.BytesIO()
        result_pil.save(buf, format="PNG")
        buf.seek(0)
        loader = GdkPixbuf.PixbufLoader()
        loader.write(buf.read())
        loader.close()
        pixbuf = loader.get_pixbuf()
        pixbuf = self._scale_for_preview(pixbuf)
        self.after_image_widget.set_from_pixbuf(pixbuf)
        self._preview_result_pil = result_pil
        return False


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

class RembgPlugin(Gimp.PlugIn):
    def do_query_procedures(self):
        return ["plug-in-rembg-remove-bg"]

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        procedure.set_image_types("*")
        procedure.set_menu_label("Remove Background...")
        procedure.add_menu_path("<Image>/Filters/Distorts")
        procedure.set_documentation(
            "Remove background using rembg (U2-Net / IS-Net / BiRefNet / "
            "SAM models)",
            "Runs the selected image/layer through rembg and inserts the "
            "result as a new layer with an alpha mask.",
            name)
        procedure.set_attribution("Zarir", "Zarir", "2026")
        return procedure

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        if run_mode != Gimp.RunMode.INTERACTIVE:
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error())

        if not check_dependencies_or_error():
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

        GimpUi.init("rembg-gimp")
        layer = drawables[0] if drawables else image.get_active_layer()

        dialog = RembgDialog(image, layer)
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            model_id = dialog._current_model_id()
            params = dialog._current_params()
            try:
                Gimp.progress_init("Removing background...")
                pil_src, is_hbd, precision = layer_to_pil(layer)
                result_pil = RembgWorker.run(pil_src, model_id, params)

                alpha = result_pil.split()[-1]
                new_layer = pil_mask_to_new_layer(
                    image, layer, alpha,
                    f"{layer.get_name()} (bg removed)")

                if is_hbd:
                    Gimp.message(
                        "Note: source was higher than 8-bit precision. "
                        "The mask was computed at 8-bit (rembg/onnxruntime "
                        "limitation) but applied back onto your original "
                        "full-precision colour data, so colour fidelity is "
                        "preserved — only the matte itself is 8-bit "
                        "derived."
                    )

                Gimp.displays_flush()
                dialog._save_current_settings()
            except Exception as e:
                Gimp.message(f"rembg failed: {e}")
                dialog.destroy()
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())

        dialog.destroy()
        return procedure.new_return_values(
            Gimp.PDBStatusType.SUCCESS, GLib.Error())


Gimp.main(RembgPlugin.__gtype__, sys.argv)
