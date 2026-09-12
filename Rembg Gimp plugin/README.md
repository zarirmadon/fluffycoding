# rembg-gimp

AI background removal for GIMP 3.x — pick from 16 rembg models, preview
before/after, tune every parameter with hover help, and drop the result
onto a new, non-destructive layer with a live layer mask.

![status](https://img.shields.io/badge/status-working-brightgreen)
![GIMP](https://img.shields.io/badge/GIMP-3.x-5C5543)
![Python](https://img.shields.io/badge/Python-3-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What this is

This plugin brings [rembg](https://github.com/danielgatis/rembg) —
a Python library that removes image backgrounds using neural
segmentation models (U²-Net, IS-Net, BiRefNet, SAM, and others) —
directly into GIMP's **Filters** menu, with a full graphical dialog
around it instead of the command line.

You get:

- A **model picker** covering all 16 models rembg currently ships,
  each with a plain-language explanation of what it's good and bad at.
- **Edge-mode controls** (Naive, Decontaminate, Alpha matting, ViTMatte)
  for fixing color fringing, mask holes, and clipped hair strands —
  the actual failure modes you run into with real images, not just a
  single generic "quality" slider.
- A **live before/after preview**, so you can try a model or setting
  combination without committing to a full-resolution run.
- **GPU detection**, so you know whether a run actually used your
  graphics card or fell back to CPU, with an install hint if it didn't.
- **Bit-depth awareness** — works correctly on 8/16/32-bit images
  without silently downgrading your color data.
- A **non-destructive result**: the cutout lands on a brand-new layer
  with an editable layer mask. Your original layer is never touched,
  and you can paint on the mask afterward to clean up any rough edges.
- **Persistent settings** — your model and parameter choices are
  remembered across GIMP sessions.
- A **built-in update checker** for keeping the underlying `rembg`
  library current, without the plugin ever touching your system behind
  your back.

---

## Where it lives

**Menu location once installed:** `Filters → Distorts → Remove Background...`

**Plugin file location on disk:**

| OS | Path |
|---|---|
| Linux | `~/.config/GIMP/3.0/plug-ins/rembg-gimp/rembg-gimp.py` |
| macOS | `~/Library/Application Support/GIMP/3.0/plug-ins/rembg-gimp/rembg-gimp.py` |
| Windows | `%APPDATA%\GIMP\3.0\plug-ins\rembg-gimp\rembg-gimp.py` |

The **folder name must match the script filename** (minus `.py`) — this
is a hard requirement of how GIMP 3.x discovers plugins, not a
suggestion.

> **macOS/Windows note:** this plugin has been built and tested on
> Linux (CachyOS/Arch). The GIMP API calls it uses are standard
> cross-platform GObject-Introspection API, so the plugin logic itself
> should behave identically on macOS and Windows — but GIMP builds on
> those platforms often bundle their own Python rather than using the
> system one, which affects how you install the dependencies below. If
> you hit install issues on macOS or Windows, please open an issue with
> your `sys.executable` output from GIMP's Python-Fu console (see
> below) and what you tried.

Settings are stored separately from the plugin file, so they survive a
plugin update:

| OS | Path |
|---|---|
| Linux | `~/.config/GIMP/3.0/rembg-gimp-settings.json` |
| macOS | `~/Library/Application Support/GIMP/3.0/rembg-gimp-settings.json` |
| Windows | `%APPDATA%\GIMP\3.0\rembg-gimp-settings.json` |

Downloaded model weights live at `~/.rembg/models/` (all platforms, via
rembg itself, not this plugin) — sizes range from ~5MB to ~1GB
depending on which models you use. If you have models downloaded from
an older rembg version, those live under `~/.u2net/` and are still read
automatically.

---

## Requirements

- **GIMP 3.x.** GIMP 2.10's Python-Fu runs Python 2, which cannot
  import `rembg` or `onnxruntime` — this plugin will not load there.
- Python packages **importable by the exact Python interpreter GIMP
  itself uses.** This is the single most common install snag — see
  below.

## Install

### Step 1 — Find GIMP's Python interpreter

Open GIMP → **Filters → Python-Fu → Console** and run:

```python
import sys
print(sys.executable)
print(sys.path)
```

Everything you install in Step 2 must land somewhere on that `sys.path`.
On most native (non-Flatpak) Linux installs this is your regular system
Python (e.g. `/usr/bin/python3`), so a normal system-wide install works
fine. If GIMP reports a different interpreter (common with Flatpak
builds, and with some macOS/Windows installers that bundle their own
Python), install everything below targeting that exact path instead of
your terminal's default `python3`.

### Step 2 — Install dependencies

**Arch / CachyOS / Manjaro** (tested configuration):

```bash
# Core deps from official repos — no AUR needed for any of this
sudo pacman -S --needed python-numpy python-pillow python-pip

# Pick ONE onnxruntime backend for your hardware:
sudo pacman -S --needed python-onnxruntime-cuda   # NVIDIA GPU
sudo pacman -S --needed python-onnxruntime-rocm   # AMD GPU
sudo pacman -S --needed python-onnxruntime        # CPU only, no GPU

# rembg itself via pip, --no-deps so it doesn't try to reinstall
# onnxruntime/numpy/pillow — letting rembg resolve its own dependencies
# can drag in an unrelated CUDA/PyTorch/gcc toolchain via AUR
python3 -m pip install --break-system-packages --no-deps rembg

# rembg's remaining small dependencies, also from official repos
sudo pacman -S --needed python-pymatting python-jsonschema python-scikit-image
```

If a package name above has changed by the time you read this, search
`pacman -Ss <name>` first — Arch/CachyOS package names for fast-moving
ML libraries do shift occasionally.

**Other Linux distros, macOS, or Windows (pip-only):**

```bash
python3 -m pip install --break-system-packages "rembg[cpu]" pillow numpy
```

Swap `rembg[cpu]` for `rembg[gpu]` (NVIDIA/CUDA) or `rembg[rocm]` (AMD)
if your system supports it — check the
[onnxruntime installation matrix](https://onnxruntime.ai/getting-started)
first to confirm compatibility with your driver/CUDA version.
(`--break-system-packages` is only required on distros that mark the
system Python as externally managed, e.g. recent Arch/Debian/Ubuntu —
drop it on macOS/Windows or inside a venv GIMP is configured to use.)

### Step 3 — Install the plugin

```bash
mkdir -p ~/.config/GIMP/3.0/plug-ins/rembg-gimp
cp rembg-gimp.py ~/.config/GIMP/3.0/plug-ins/rembg-gimp/
chmod +x ~/.config/GIMP/3.0/plug-ins/rembg-gimp/rembg-gimp.py
```

(Adjust the path for macOS/Windows per the table above.)

Restart GIMP. You'll find the plugin at
**Filters → Distorts → Remove Background...**

### Step 4 — First run

The first time you select a given model, rembg downloads its weights
(a few MB to ~1GB, shown in the model picker) before running. This
needs network access once per model; after that it's cached locally and
works offline.

---

## Usage tips

- **Start with the default model** (`bria-rmbg`) for a quick sense of
  quality, but read the licensing note below before using its output
  commercially.
- **If your cutout has holes or gaps** — a common problem on
  anime/flat-illustration art with hard cel-shaded outlines — this is a
  mask *shape* problem, not a color problem. Switch the **edge mode**
  dropdown to **ViTMatte** or **Alpha matting** before trying a
  different model; Decontaminate won't fix this, since it only corrects
  color fringing on already-correct mask shapes.
- **If your cutout has a colored halo/rim** around hair or fur (common
  when the source was shot against strongly colored background — green
  screen, blue sky), that's what **Decontaminate** is for, and it's
  cheap enough to leave on for everything.
- **Use "Generate Preview" before Apply** — it runs at reduced
  resolution so you can try several model/edge-mode combinations
  quickly before committing to a full-resolution run, which matters
  more the larger the model (BiRefNet variants and `sam` are
  noticeably slower than `u2net`/`silueta`).
- **The result layer's mask stays live and editable** — it's not
  flattened into the layer automatically. If a cutout is 95% right,
  it's often faster to paint the last 5% by hand on the mask (white to
  reveal, black to hide) than to keep re-running the model with
  different settings.
- **Check the GPU/CPU status line** after a preview run. If it shows
  CPU when you expected GPU, that's almost always a CUDA/cuDNN/ROCm
  version mismatch between what `onnxruntime-*` expects and what's
  installed on your system — the plugin will show you the install
  command for your GPU vendor if it detects CPU-only.
- **Large models genuinely need the download every time you switch
  hardware/OS** — model weight caches aren't portable between machines,
  so a fresh install anywhere starts from Step 4 again per model.

### ⚠️ A licensing note on `bria-rmbg`

`bria-rmbg` is rembg's current default model and produces excellent
results, but its underlying weights (RMBG-2.0) are released under a
**separate BRIA license that requires a paid agreement for commercial
use** — this is not covered by rembg's own MIT license. If your output
will be used commercially (print sales, client work, merchandise,
etc.), either confirm your licensing status with BRIA first, or use
`u2net`, `isnet-general-use`, or `birefnet-general` instead — all fully
MIT-licensed with no such restriction. The in-app model picker flags
this too.

---

## Checking for updates

The **"Check for rembg updates"** button queries PyPI directly and
tells you if a newer `rembg` release exists. It does **not** run `pip`
for you — if an update is available, you get the exact command in a
copyable field to run yourself in a terminal, then restart GIMP.

This is intentional, not a missing feature:

1. GIMP has already imported the current `rembg` module for this
   session — Python doesn't reliably pick up an in-place package
   upgrade without restarting the process that imported it, so even an
   automatic upgrade wouldn't take effect until GIMP restarts anyway.
2. A plugin silently invoking your system's package manager, without an
   explicit and visible confirmation step, isn't something this project
   wants to do — especially since your install method may not have
   been plain pip (see the Arch/CachyOS instructions above, where
   several pieces come from `pacman` instead).

This only covers updates to `rembg` itself — bug fixes and model
weight improvements for models already in the dropdown. If a future
`rembg` release adds a genuinely new model, parameter, or edge mode,
the plugin's own interface won't expose it until this plugin's code is
updated accordingly (see [Contributing](#contributing)).

---

## Models

| Model | Approx. size | Best for |
|---|---|---|
| `bria-rmbg` | ~1.0 GB | Default, state-of-the-art quality. **Commercial license caveat — see above.** |
| `u2net` | ~176 MB | General purpose, MIT-licensed, safe default for commercial work |
| `u2netp` | ~4.7 MB | Fast/lightweight, previews only |
| `u2net_human_seg` | ~176 MB | People, full body |
| `u2net_cloth_seg` | ~176 MB | Clothing/garment segmentation |
| `silueta` | ~43 MB | Compact general purpose |
| `isnet-general-use` | ~176 MB | Higher quality general purpose, MIT-licensed |
| `isnet-anime` | ~176 MB | Anime/flat illustration |
| `sam` | ~375 MB | Segment Anything (experimental in rembg's automatic mode) |
| `birefnet-general` | ~880 MB | High-precision general, fine edges/hair, MIT-licensed |
| `birefnet-general-lite` | ~170 MB | Lighter BiRefNet, most of the quality at a fraction of the cost |
| `birefnet-portrait` | ~880 MB | High-precision portraits |
| `birefnet-dis` | ~880 MB | Dichotomous (very precise) segmentation, specialist |
| `birefnet-hrsod` | ~880 MB | High-resolution salient object detection, specialist |
| `birefnet-cod` | ~880 MB | Concealed/camouflaged object detection, niche |
| `birefnet-massive` | ~880 MB | Trained on a larger dataset, may generalise better to unusual subjects |

Full plain-language comparisons are available in-app via the **"?"**
button next to the model dropdown.

## Edge modes

| Mode | Fixes | Cost |
|---|---|---|
| Naive (default) | Nothing — no refinement | Free |
| Decontaminate | Colored halo/fringing on soft edges | Cheap |
| Alpha matting | Wrong mask *shape* — holes, clipped hair | Slow; can occasionally fail to converge |
| ViTMatte | Same as Alpha matting, more reliable, recovers more detail | Slow + one-time ~110–380MB checkpoint download |

Full guidance is available in-app via the **"?"** button next to the
edge mode dropdown.

---

## Known limitations

- **SAM** runs in rembg's automatic mode rather than with point/box
  prompts, so results can be less predictable than the purpose-built
  matting models. Included for completeness, not as a default choice.
- **Preview** runs on a size-capped thumbnail for speed — extremely
  fine detail (individual hair strands) may render slightly differently
  in the full-resolution Apply pass.
- **Single layer per run** — no batch/multi-layer mode built in. For
  batch processing across many files at once, use rembg's own CLI
  (`rembg p input_folder/ output_folder/`) outside GIMP.
- **macOS and Windows are untested** by the plugin's author — see the
  note under [Where it lives](#where-it-lives).

## Contributing

Issues and pull requests are welcome — especially:
- Confirmed install steps for macOS and Windows
- New rembg models/parameters that should be added to the dropdown
- Bug reports with the exact GIMP error text (GIMP 3.x's Python API has
  shifted across dev snapshots more than once during this plugin's
  development, so version-specific breakage is plausible)

## Credits

Developed by **[Zarir Madon](https://www.zarirmadon.com)** together with
**Claude Sonnet 5** (Anthropic), September 2026.

Built on [rembg](https://github.com/danielgatis/rembg) by Daniel Gatis,
which itself wraps several independently developed segmentation models
(U²-Net, IS-Net, BiRefNet, SAM, RMBG, and others) — see the
[rembg repository](https://github.com/danielgatis/rembg) for full
upstream credits and each model's individual license.

## License

This plugin is released under the MIT License.

Individual **rembg model weights** carry their own separate licenses —
most are MIT or similarly permissive, but `bria-rmbg` (RMBG-2.0)
requires a paid commercial license from BRIA; see the note above before
using it commercially.
