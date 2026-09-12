# Upscayl for GIMP

AI-powered image upscaling for GIMP 3.x, running entirely offline via the [Upscayl](https://github.com/upscayl/upscayl) CLI — select a layer, pick a scale and model, and get the result back as a new layer without ever leaving GIMP.

![GIMP](https://img.shields.io/badge/GIMP-3.0%2B-5C5543?logo=gimp&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Linux-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Python](https://img.shields.io/badge/python-3-blue?logo=python&logoColor=white)

---

## What it does

This plugin brings [Upscayl](https://upscayl.org)'s AI upscaling — the same Real-ESRGAN / NCNN-Vulkan engine behind Upscayl's own desktop app — directly into GIMP as a native filter. Instead of exporting an image, switching to a separate app, upscaling it, and re-importing the result, you upscale your active layer in place and a new, upscaled layer appears above it.

Key features:

- **2x / 4x upscale**, plus a **Double Upscale** toggle (matching the official Upscayl app's own option) that runs a second pass on the result — with a live label showing the actual total multiplier (e.g. "→ 16x total") as you adjust either control.
- **Full model support** — a dropdown built from whatever models your Upscayl install has, plus an optional custom model folder for anything you've downloaded separately (e.g. from the [Upscayl custom-models repo](https://github.com/upscayl/custom-models)).
- **Auto-detects your Upscayl installation** — system package, Flatpak, or a portable AppImage — with a dropdown if more than one is found and a "Re-check" button if you install one afterward.
- **All CLI parameters exposed**, each with a tooltip: GPU device id, tile size, thread count, TTA (Test-Time Augmentation) mode.
- **Live pixel progress bar**, parsed directly from Upscayl's own per-tile console output — not a fake spinner.
- **Live CPU/GPU usage graph** shown while upscaling — a compact strip with CPU as a blue line and GPU as a red line, sampled twice a second. GPU name is shown when detected (AMD via sysfs, Nvidia via `nvidia-smi`); degrades silently to CPU-only on GPUs/drivers without a supported usage source, rather than showing a blank or broken graph.
- **Cancel button** on the progress dialog — stops the running process cleanly.
- **Non-destructive** — your original layer and image are never modified; the result always lands as a new, clearly-named layer.
- **Remembers your settings** between runs (scale, model, GPU id, tile size, etc.), stored outside of GIMP so they persist across restarts.
- Tooltips on every control, so nothing requires reading this README to use.

## Requirements

- **GIMP 3.0 or later**, with its Python bindings (this uses GIMP's current `Gimp`/`GimpUi`/GTK3 Python API — it will not work on GIMP 2.10).
- **Upscayl** installed somehow — a system package, a Flatpak (`org.upscayl.Upscayl`), or a portable AppImage. The plugin detects it automatically; it doesn't install it for you. Get it from [upscayl.org](https://upscayl.org) or your distro's package manager / AUR.
- **A Vulkan-capable GPU with up-to-date drivers.** Upscayl's backend is GPU-accelerated via Vulkan (not CUDA — it works on Nvidia, AMD, and some Intel GPUs alike, not just Nvidia). Most integrated GPUs and CPUs are not supported. On Linux, make sure your vendor's Vulkan driver package is installed (e.g. `vulkan-radeon` for AMD, `vulkan-intel` for Intel, the proprietary Nvidia driver for Nvidia) — this is sometimes a separate package from the base GPU driver.
- Linux. (This plugin shells out to a CLI binary and reads Linux-specific paths for install detection and GPU monitoring; it has not been built or tested for Windows or macOS.)

## Where it lives

Copy the whole plugin folder into your GIMP 3.x plug-ins directory, keeping the folder name and the `.py` filename inside it matching:

```
~/.config/GIMP/3.0/plug-ins/upscayl-gimp/upscayl-gimp.py
```

(Adjust `3.0` to your installed version if different, e.g. `3.2`.)

Make the script executable:

```
chmod +x ~/.config/GIMP/3.0/plug-ins/upscayl-gimp/upscayl-gimp.py
```

Restart GIMP. The plugin appears at:

**Filters → Enhance → Upscayl…**

Settings are stored separately from GIMP itself, at `~/.config/upscayl-gimp/settings.txt` — a plain text file, safe to delete if you ever want to reset to defaults.

## Usage

1. Open an image and select the layer you want to upscale.
2. **Filters → Enhance → Upscayl…**
3. The dialog shows which Upscayl install was detected. If you have more than one (e.g. both a Flatpak and a system package), pick which to use from the dropdown.
4. Choose your **Upscale** factor (2x or 4x) and optionally tick **Double Upscale** to run a second pass — the label next to it always shows the real total multiplier.
5. Pick a **Model** from the dropdown. If you have extra models downloaded elsewhere, set a **Model folder** to merge them into the list.
6. Adjust GPU id / tile size / threads / TTA mode if needed — leave them at their defaults unless you have a specific reason not to (see tips below).
7. Click **Upscayl**. A progress dialog shows live percentage and CPU/GPU usage; **Cancel** stops it cleanly if needed.
8. The result appears as a new layer, named after the source layer, scale, and model used.

### Usage tips

- **Multi-GPU / hybrid laptops:** Upscayl's automatic GPU selection can pick the wrong device on systems with both an integrated and a discrete GPU (a known upstream behavior, not specific to this plugin). If upscaling seems to be using the wrong GPU or running slowly, run `vulkaninfo --summary` in a terminal to list your Vulkan devices by index, then set that index explicitly in the **GPU id** field. It'll be remembered for future runs.
- **Out-of-memory / Vulkan allocation errors:** lower the **Tile size** field (try `128` or `64`) rather than leaving it on `0` (auto) — this trades a little speed for a much smaller memory footprint per tile.
- **Large layers / 8x-equivalent results:** Double Upscale roughly doubles processing time and produces substantially larger output — try a single pass first before reaching for it.
- **PNG only, by design:** the plugin always uses PNG as the intermediate hand-off format between GIMP and Upscayl (lossless, and avoids a class of PDB argument complexity that JPEG/WEBP export would introduce for no real benefit to an AI upscaler's input).
- **Flatpak installs** are launched with a temporary, scoped filesystem permission for just the plugin's own temp folder — no manual `flatpak override` should be needed.
- If nothing shows up in **Filters → Enhance**, double check the folder/filename match under `plug-ins/` and that the `.py` file is executable — GIMP silently skips plugins that don't meet both conditions.

## How it works

The plugin exports your selected layer's pixels (only that layer, not the flattened image) to a temporary PNG, shells out to Upscayl's CLI binary with the parameters you chose, streams its progress output live into the dialog, and loads the result back in as a new layer. Nothing about your original image or layer is touched in the process, and temporary files are cleaned up after each run.

## Credits & links

Developed by **[Zarir Madon](https://www.zarirmadon.com)**, together with **Claude (Anthropic, Sonnet 5)** — September 2026.

- [Upscayl](https://github.com/upscayl/upscayl) — the AI upscaler this plugin wraps, by the Upscayl team, AGPL-3.0.
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) — the underlying super-resolution model architecture Upscayl is built on.
- [GIMP](https://www.gimp.org) — GNU Image Manipulation Program.
- [Upscayl custom-models](https://github.com/upscayl/custom-models) — a good source of additional models compatible with this plugin's custom model folder option.

## License

GPL-3.0, matching Upscayl's own CLI licensing terms.
