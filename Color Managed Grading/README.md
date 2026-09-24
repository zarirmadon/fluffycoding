
# Color Managed Grading

**A visual, non-destructive colour grading environment for GIMP 3.2+.**

Color Managed Grading brings professional-style colour grading tools into a single photographer- and artist-friendly GIMP interface.

Rather than presenting colour work as a collection of technical filters, it provides visual controls for tonal grading, curves, colour relationships, RGB primaries, global colour, scopes, presets and colour-managed viewing.

Grades are built directly on the GIMP canvas and can be retained as editable **FX layers**, keeping the original image untouched.

---

## Highlights

- Non-destructive GIMP FX workflow
- Live on-canvas preview
- Shadows / Midtones / Highlights / Master grading
- Colour Wheels and RGB Bars sharing the same grade
- Luminance and RGB Curves
- Color Warp
- Hue vs Hue
- Hue vs Saturation
- Hue vs Luminance
- Visual RGB Primary controls
- Temperature, Tint, Chroma and Saturation
- Histogram, Waveform, RGB Parade and Vectorscope
- ICC soft-proofing controls
- OpenColorIO detection
- Built-in creative presets
- User presets
- `.cube` LUT workflow
- Multi-stage **Apply FX + Continue** grading
- Designed specifically for GIMP 3.2+

---

# Grading Workspace

## Wheels / Bars

Two different interfaces control the same tonal grade.

### Wheels

Visual colour wheels are provided for:

- Shadows
- Midtones
- Highlights
- Master

The wheels are intended for fast, intuitive colour balancing and creative tonal separation.

### Bars

The same grade can be manipulated through RGB grading bars.

Switching between Wheels and Bars does not create another adjustment — both views represent the same underlying grading state.

---

# Curves

The Curves workspace provides:

- **Luminance**
- **RGB**
- **Red**
- **Green**
- **Blue**
- **Color Warp**

The curve displays incorporate image histogram information so adjustments can be made in relation to the actual tonal distribution of the photograph.

Individual curves can be reset without disturbing the rest of the grade.

---

## Color Warp

Color Warp provides a spatial approach to colour manipulation.

Instead of working through individual RGB channels, colour regions can be moved around a hue/saturation field.

It is useful for:

- palette shaping
- creative colour separation
- subtle hue movement
- saturation relationships
- stylised colour treatments

The control is designed to remain visual rather than exposing the underlying processing mathematics.

---

# Hue Curves

Three specialised curves provide targeted control over colour relationships:

### Hue vs Hue

Changes one hue into another.

### Hue vs Saturation

Changes saturation according to hue.

### Hue vs Luminance

Changes luminance according to hue.

These controls allow targeted colour changes without first creating selections or masks.

---

# Colour

The Colour workspace combines **Primaries** and **Global Colour** controls.

## Primaries

Red, Green and Blue primaries can be shaped using three visual colour pucks.

Each puck begins in the centre at its neutral state.

- **Direction** controls the primary hue shift.
- **Distance from the centre** controls the strength/purity of the adjustment.
- The complete 360° field is available.
- Response is progressively weighted to provide finer control close to neutral.
- Full creative strength remains available toward the circumference.
- Double-clicking a puck returns it to neutral.

The controls are deliberately visual: the puck, colour field and gradient spoke show the relationship between the original primary and its adjusted position.

---

## Global Colour

Whole-image colour character can be adjusted through four visual gradient controls:

- **Temperature**
- **Tint**
- **Chroma**
- **Saturation**

The colour field itself is the control.

Drag directly across the gradient to make the adjustment. Neutral positions remain clearly marked and double-clicking a control returns it to neutral.

---

# Scopes

Color Managed Grading includes four image-analysis scopes:

- **Histogram**
- **Waveform**
- **RGB Parade**
- **Vectorscope**

These are intended as practical grading tools rather than decorative displays.

### Histogram

Shows the distribution of image tones.

### Waveform

Useful for judging:

- overall luminance
- highlight clipping
- shadow clipping
- exposure distribution
- tonal balance across the image

### RGB Parade

Displays the RGB channels separately and is particularly useful for detecting colour casts and channel imbalance.

### Vectorscope

Shows hue and saturation distribution and complements tools such as Color Warp, Primaries and Global Colour.

Scopes analyse the rendered grading result and are calculated when required rather than continuously consuming resources while working elsewhere in the plugin.

---

# Colour Management

Creative grading and display/proofing decisions are deliberately kept separate.

The Colour Management workspace provides support for:

- image colour-profile information
- ICC proof profiles
- rendering intent
- Black Point Compensation
- soft proofing
- OpenColorIO detection

Where PyOpenColorIO is available to GIMP's Python runtime, the plugin detects it and exposes the appropriate colour-management functionality.

Soft proofing is treated as a **viewing operation** and is not silently baked into the creative grade.

---

# Presets

A collection of built-in creative starting points is included.

Examples include:

- Warm Film
- Cool Cinema
- Soft Portrait
- Punchy Editorial
- Muted Matte
- Clean Commercial
- Golden Hour
- Teal Shadows
- High Key
- Low Key
- Vintage Fade
- Landscape Pop

A preset establishes a grading state rather than permanently locking the controls.

After selecting a preset, every control remains available for further adjustment.

---

## User Presets

Your own grades can be saved as reusable presets.

Use:

**Save current as preset…**

Saved presets can then be applied to other layers and photographs.

User presets are stored separately from the plugin itself so the preset collection can survive plugin updates.

Multiple user presets can also be selected and removed through the preset-management interface.

---

# LUT Support

Color Managed Grading supports `.cube` LUT workflows.

This allows LUTs to participate in a broader grading workflow rather than requiring them to be managed as an entirely separate process.

Creative grades can also be exported for use in compatible LUT workflows.

---

# Non-Destructive FX Workflow

A central design goal of Color Managed Grading is to work *with* GIMP's non-destructive editing system.

While the grading dialog is open, adjustments are shown directly on the GIMP canvas through a dedicated preview/FX layer.

The original photograph remains underneath.

A simple grade may therefore appear as:

    Color Managed Grading [fx]
    Original Photograph

The `[fx]` indicator represents GIMP drawable filters attached to the grading layer.

---

## Apply

Pressing **Apply** commits the current FX grading layer and closes Color Managed Grading.

The original image remains underneath.

---

## Apply FX + Continue

More complex grades can be divided into several stages.

Press:

**Apply FX + Continue**

The current FX layer is committed and becomes the base for another grading stage.

The controls return to neutral and the interface remains open.

You can therefore build a stack such as:

    Color Managed Grading 03 [fx]
    Color Managed Grading 02 [fx]
    Color Managed Grading 01 [fx]
    Original Photograph

This can be useful when different stages of a grade have different purposes.

For example:

    Stage 03 — final colour character
    Stage 02 — creative palette
    Stage 01 — basic tonal balance
    Original Photograph

There is no requirement to work this way — a complete grade can equally be performed in a single FX layer.

---

# Cancel Means Cancel

The current uncommitted grading stage is discarded when you:

- press **Cancel**
- close the window with **×**
- close the window with **Alt+F4**

Simply closing the grading window does **not** silently apply the current preview.

Any stages previously committed using **Apply FX + Continue** remain in the image.

---

# Reset Controls

Resetting is available at several levels.

Individual graphical controls can be returned to neutral where appropriate.

Individual curves have their own reset controls.

Workspace-level resets allow a particular section of the grade to be cleared.

**Reset All** returns the complete current grading stage to neutral.

Supported visual controls can also be returned to neutral by double-clicking them.

---

# Designed for Image Makers

Color Managed Grading deliberately avoids exposing unnecessary implementation terminology in the main interface.

The intended audience is:

- photographers
- fine-art photographers
- digital artists
- illustrators
- image makers
- colour-grading enthusiasts

Where an adjustment has a useful visual representation, the plugin favours that representation over another generic slider.

That philosophy is reflected throughout the interface with:

- wheels
- colour fields
- pucks
- gradients
- curves
- scopes
- clear neutral positions
- persistent high-visibility selections

The underlying processing can remain technical.

Using it shouldn't have to be.

---

# Scope of the Project

Color Managed Grading is intentionally a **colour-grading plugin**, not a general image-processing suite.

Features such as:

- sharpening
- denoising
- local dodge and burn
- frequency separation
- general retouching
- local masking tools

are deliberately outside its scope.

Keeping those functions separate allows the grading interface to remain focused.

---

# Requirements

### Required

- **GIMP 3.2 or later**
- GIMP Python plug-in support
- Python 3 / PyGObject environment supplied or accessible to GIMP

### Optional

- **OpenColorIO / PyOpenColorIO**

The plugin detects optional colour-management support when it is available to GIMP's Python runtime.

---

# Installation

## Linux

Create a plugin directory:

    ~/.config/GIMP/3.2/plug-ins/color-managed-grading/

Place:

    color-managed-grading.py

inside it:

    ~/.config/GIMP/3.2/plug-ins/color-managed-grading/color-managed-grading.py

Make the script executable:

    chmod +x ~/.config/GIMP/3.2/plug-ins/color-managed-grading/color-managed-grading.py

Restart GIMP.

The plugin should then be available from:

**Colors → Color Managed Grading**

---

# Updating

To update the plugin, replace:

    color-managed-grading.py

with the newer release and restart GIMP.

Your separately stored user presets should not need to be recreated simply because the main plugin file has been replaced.

---

# Troubleshooting

If the plugin does not appear in GIMP, first confirm:

1. You are running GIMP 3.2 or later.
2. The directory is named `color-managed-grading`.
3. The file is named `color-managed-grading.py`.
4. The Python file is executable.
5. The plugin is installed beneath the correct GIMP 3.2 configuration directory.

On Linux it can also be useful to launch GIMP from a terminal:

    gimp

Any Python traceback or plug-in registration error will then normally be visible in the terminal.

When submitting a bug report, please include:

- GIMP version
- operating system
- GIMP installation type
- steps required to reproduce the problem
- terminal traceback if available
- screenshot where useful

---

# Platform Status

The primary development environment for Color Managed Grading is:

**Linux + GIMP 3.2**

Other operating systems capable of running the required GIMP 3 Python plug-in environment may work, but should be considered less extensively tested unless confirmed by users of those platforms.

Testing and bug reports from other platforms are welcome.

---

# Project Philosophy

Color Managed Grading grew from a fairly simple tonal colour-grading tool into a much broader visual grading environment.

Throughout that development, the guiding ideas remained:

**Visual rather than cryptic.**

**Powerful without requiring knowledge of the underlying processing engine.**

**Non-destructive wherever GIMP makes that possible.**

**Useful for photographers and artists rather than designed around programmers.**

And, above all:

**Keep colour grading about the image.**

---

# License

Color Managed Grading is free and open-source software.

**SPDX-License-Identifier: GPL-3.0-or-later**

Copyright © 2026 **Zarir Madon**

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but **WITHOUT ANY WARRANTY**; without even the implied warranty of **MERCHANTABILITY** or **FITNESS FOR A PARTICULAR PURPOSE**.

See the GNU General Public License for more details.

---

## Author

**Zarir Madon**  
Fine Art Photographer & Digital Artist
https://www.zarirmadon.com
https://www.zmcreative.art

### The Digital Perspective

*The world isn't changed. The perspective is.*
