#!/usr/bin/env python3
# color-managed-grading.py
# SPDX-License-Identifier: GPL-3.0-or-later
# GIMP 3.2+ plugin — Colors > Color Managed Grading
#
# Copyright (C) 2026  Zarir Madon
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Color Managed Grading — non-destructive colour grading for GIMP 3.2+.

Provides visual tonal and colour-grading tools including Wheels / Bars,
Curves, Color Warp, Hue Curves, Primaries, Global Colour, colour-management
controls, scopes, presets, and a staged FX workflow.

The live grade is built on a dedicated preview/FX layer. Apply commits the
current FX stage; Apply FX + Continue commits the stage and starts a fresh
neutral stage; Cancel, window close, and Alt+F4 discard only the current
uncommitted preview stage.

Install: place this file in:
  ~/.config/GIMP/3.2/plug-ins/color-managed-grading/color-managed-grading.py
and make it executable.

Menu: Colors > Color Managed Grading
License: GPL-3.0-or-later
"""

import math
import colorsys
import json
import sys
import os

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('GLib', '2.0')
gi.require_version('Gtk', '3.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gimp, GimpUi, GLib, GObject, Gdk, Gtk, Pango

import cairo


# ─────────────────────────────────────────────────────────────────────────────
# Palette — neutral dark grading theme
# ─────────────────────────────────────────────────────────────────────────────

P = {
    'surface':      (0.118, 0.118, 0.118),   # #1e1e1e disc / track bg
    'track_bg':     (0.094, 0.094, 0.094),   # #181818 recessed tracks
    'bar_track':    (0.070, 0.070, 0.070),   # RGB slider track
    'xhair':        (1.0,   1.0,   1.0,   0.10),
    'guide':        (1.0,   1.0,   1.0,   0.28),
    'centre_dot':   (1.0,   1.0,   1.0,   0.28),
    'handle_ring':  (1.0,   1.0,   1.0,   0.88),
    'handle_dot':   (1.0,   1.0,   1.0,   1.0),
    # range-bar dot colours
    'dot_shw_fill':  (0.165, 0.165, 0.165),
    'dot_shw_ring':  (0.745, 0.745, 0.745, 0.85),
    'dot_mid_fill':  (0.314, 0.314, 0.671),
    'dot_mid_ring':  (0.667, 0.667, 0.824, 0.85),
    'dot_hlt_fill':  (0.784, 0.784, 0.784),
    'dot_hlt_ring':  (0.431, 0.431, 0.431, 0.85),
    # colour-bars sliders
    'bar_r_fill':   (0.780, 0.220, 0.180),   # #c73830 red fill
    'bar_g_fill':   (0.220, 0.620, 0.260),   # #389e42 green fill
    'bar_b_fill':   (0.200, 0.420, 0.780),   # #336bc7 blue fill
    'bar_zero':     (1.0,   1.0,   1.0, 0.18),  # zero-line
    'bar_txt':      (0.600, 0.600, 0.600),   # #999 value text
    'bar_txt_neg':  (1.0,   0.500, 0.400),   # orange-red for negative
    'sat_fill':     (0.780, 0.620, 0.160),   # #c79e29 saturation gold
    'contrast_fill':(0.400, 0.680, 0.820),   # #66add1 contrast blue
    'pivot_fill':   (0.680, 0.680, 0.680),   # #adadad pivot grey
    # histogram
    'hist_r':  (0.824, 0.275, 0.235, 0.45),
    'hist_g':  (0.235, 0.706, 0.314, 0.40),
    'hist_b':  (0.235, 0.392, 0.784, 0.45),
    'hist_div':(1.0,   1.0,   1.0,   0.06),
    'hist_txt':(1.0,   1.0,   1.0,   0.18),
}


def _rgb(key):
    v = P[key]
    return v[0], v[1], v[2]

def _rgba(key):
    v = P[key]
    return v[0], v[1], v[2], v[3] if len(v) == 4 else 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PLUGIN_NAME  = 'color-managed-grading'
MENU_LABEL   = 'Color Managed Grading'

SHADOWS    = 'shadows'
MIDTONES   = 'midtones'
HIGHLIGHTS = 'highlights'
MASTER     = 'master'
BANDS      = (SHADOWS, MIDTONES, HIGHLIGHTS)
BAND_LABEL = {
    SHADOWS:    'Lift',
    MIDTONES:   'Gamma',
    HIGHLIGHTS: 'Gain',
    MASTER:     'Master',
}

# Wheel geometry
WHEEL_SIZE        = 172   # main wheels (px)
WHEEL_SIZE_MASTER = 168   # master wheel (px) — use the available Master area
RING_WIDTH        = 8     # slim ring

# Range bar geometry (circle-dot style)
BAR_H    = 7     # gradient strip height
DOT_R    = 5     # handle dot radius
BAR_PAD  = 10    # horizontal inset
TOTAL_H  = 26    # total widget height (bar centred, room for dots above/below)

# Defaults
DEFAULT_SHADOWS_MID    = 0.50
DEFAULT_HIGHLIGHTS_MID = 0.50
DEFAULT_MIDTONES_LEFT  = 0.33
DEFAULT_MIDTONES_RIGHT = 0.66

DEFAULT_SATURATION = 1.0    # 0.0 … 2.0  (1.0 = unchanged)
DEFAULT_CONTRAST   = 0.0    # -1.0 … +1.0
DEFAULT_PIVOT      = 0.5    # 0.0 … 1.0  luminance pivot

# Colour-balance scaling
CB_SCALE = {SHADOWS: 1.5, MIDTONES: 0.75, HIGHLIGHTS: 1.5, MASTER: 1.0}

CB_TRANSFER = {
    SHADOWS:    Gimp.TransferMode.SHADOWS,
    MIDTONES:   Gimp.TransferMode.MIDTONES,
    HIGHLIGHTS: Gimp.TransferMode.HIGHLIGHTS,
}

HIT_NONE  = 0
HIT_LEFT  = 1
HIT_RIGHT = 2

_LINEAR_PRECISIONS = {
    Gimp.Precision.U8_LINEAR,
    Gimp.Precision.U16_LINEAR,
    Gimp.Precision.U32_LINEAR,
    Gimp.Precision.HALF_LINEAR,
    Gimp.Precision.FLOAT_LINEAR,
    Gimp.Precision.DOUBLE_LINEAR,
}
_TO_LINEAR = {
    Gimp.Precision.U8_NON_LINEAR:     Gimp.Precision.U8_LINEAR,
    Gimp.Precision.U8_PERCEPTUAL:     Gimp.Precision.U8_LINEAR,
    Gimp.Precision.U16_NON_LINEAR:    Gimp.Precision.U16_LINEAR,
    Gimp.Precision.U16_PERCEPTUAL:    Gimp.Precision.U16_LINEAR,
    Gimp.Precision.U32_NON_LINEAR:    Gimp.Precision.U32_LINEAR,
    Gimp.Precision.U32_PERCEPTUAL:    Gimp.Precision.U32_LINEAR,
    Gimp.Precision.HALF_NON_LINEAR:   Gimp.Precision.HALF_LINEAR,
    Gimp.Precision.HALF_PERCEPTUAL:   Gimp.Precision.HALF_LINEAR,
    Gimp.Precision.FLOAT_NON_LINEAR:  Gimp.Precision.FLOAT_LINEAR,
    Gimp.Precision.FLOAT_PERCEPTUAL:  Gimp.Precision.FLOAT_LINEAR,
    Gimp.Precision.DOUBLE_NON_LINEAR: Gimp.Precision.DOUBLE_LINEAR,
    Gimp.Precision.DOUBLE_PERCEPTUAL: Gimp.Precision.DOUBLE_LINEAR,
}


# ─────────────────────────────────────────────────────────────────────────────
# Maths helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hsv_to_rgb(h, s, v):
    if s == 0.0:
        return v, v, v
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i % 6
    if i == 0: return v, t, p
    if i == 1: return q, v, p
    if i == 2: return p, v, t
    if i == 3: return p, q, v
    if i == 4: return t, p, v
    return v, p, q


def wheel_to_rgb(hue_deg, saturation):
    r, g, b = _hsv_to_rgb(hue_deg / 360.0, 1.0, 1.0)
    mean = (r + g + b) / 3.0
    return (r - mean) * saturation, (g - mean) * saturation, (b - mean) * saturation


def rgb_to_wheel(r, g, b):
    """Return the wheel H/S whose cast most closely represents an RGB cast.

    Colour Bars are allowed to express casts that are not exactly on the wheel
    gamut, so this is a projection used for display only.  The canonical RGB
    values are never replaced merely by changing tabs.
    """
    target = (float(r), float(g), float(b))
    if max(abs(v) for v in target) < 1e-9:
        return 0.0, 0.0
    best_h, best_s, best_err = 0.0, 0.0, float('inf')
    # One-degree search is inexpensive (only when switching tabs) and exactly
    # matches wheel_to_rgb rather than relying on a different colour model.
    for h in range(360):
        base = wheel_to_rgb(float(h), 1.0)
        den = sum(v*v for v in base)
        if den <= 1e-12:
            continue
        sat = max(0.0, min(1.0, sum(target[i]*base[i] for i in range(3)) / den))
        err = sum((target[i] - base[i]*sat)**2 for i in range(3))
        if err < best_err:
            best_h, best_s, best_err = float(h), sat, err
    return best_h, best_s


def _monotone_cubic_lut(pts):
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    dx = [xs[i+1] - xs[i] for i in range(n-1)]
    dy = [ys[i+1] - ys[i] for i in range(n-1)]
    m  = [dy[i] / dx[i] if dx[i] != 0 else 0.0 for i in range(n-1)]
    t = [0.0] * n
    t[0] = m[0]; t[n-1] = m[-1]
    for i in range(1, n-1):
        t[i] = 0.0 if m[i-1] * m[i] <= 0 else (m[i-1] + m[i]) / 2.0
    for i in range(n-1):
        if m[i] == 0:
            t[i] = t[i+1] = 0.0
        else:
            a, b = t[i] / m[i], t[i+1] / m[i]
            if a**2 + b**2 > 9:
                s = 3.0 / math.sqrt(a**2 + b**2)
                t[i] = s * a * m[i]; t[i+1] = s * b * m[i]

    def _h(i, x):
        h = dx[i]; tt = (x - xs[i]) / h
        return (2*tt**3-3*tt**2+1)*ys[i] + (tt**3-2*tt**2+tt)*h*t[i] + \
               (-2*tt**3+3*tt**2)*ys[i+1] + (tt**3-tt**2)*h*t[i+1]

    lut = []; seg = 0
    for k in range(256):
        x = k / 255.0
        while seg < n-2 and x > xs[seg+1]: seg += 1
        lut.append(int(max(0, min(255, round(_h(seg, max(xs[seg], min(xs[seg+1], x))) * 255.0)))))
    return lut


def _smooth_curve_values(pts, samples=1024):
    """Float monotone cubic interpolation for display; avoids 8-bit stair-stepping."""
    pts=sorted((float(x),float(y)) for x,y in pts)
    n=len(pts)
    if n < 2:
        return [0.0]*samples
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    dx=[max(1e-9,xs[i+1]-xs[i]) for i in range(n-1)]
    m=[(ys[i+1]-ys[i])/dx[i] for i in range(n-1)]
    t=[0.0]*n; t[0]=m[0]; t[-1]=m[-1]
    for i in range(1,n-1):
        if m[i-1]*m[i] <= 0: t[i]=0.0
        else:
            w1=2*dx[i]+dx[i-1]; w2=dx[i]+2*dx[i-1]
            t[i]=(w1+w2)/(w1/m[i-1]+w2/m[i])
    out=[]; seg=0
    for k in range(samples):
        x=k/(samples-1)
        while seg<n-2 and x>xs[seg+1]: seg+=1
        h=dx[seg]; u=max(0.0,min(1.0,(x-xs[seg])/h))
        h00=2*u**3-3*u**2+1; h10=u**3-2*u**2+u
        h01=-2*u**3+3*u**2; h11=u**3-u**2
        y=h00*ys[seg]+h10*h*t[seg]+h01*ys[seg+1]+h11*h*t[seg+1]
        out.append(max(0.0,min(1.0,y)))
    return out


def _shadows_lut(mid):
    return _monotone_cubic_lut([(0.0, 1.0), (mid, 0.5), (1.0, 0.0)])

def _highlights_lut(mid):
    return _monotone_cubic_lut([(0.0, 0.0), (mid, 0.5), (1.0, 1.0)])

def _midtones_lut(left, right):
    return _monotone_cubic_lut([(0.0, 0.0), (left, 0.5), (0.5, 1.0), (right, 0.5), (1.0, 0.0)])


def _apply_curve_lut(mask, lut):
    curve = Gimp.Curve.new()
    curve.set_curve_type(Gimp.CurveType.FREE)
    curve.set_n_samples(256)
    for i, v in enumerate(lut):
        curve.set_sample(i / 255.0, v / 255.0)
    f = Gimp.DrawableFilter.new(mask, 'gimp:curves', '')
    if f is None:
        raise RuntimeError('Failed to create curves filter for mask.')
    cfg = f.get_config()
    if cfg is None:
        raise RuntimeError('Failed to get curves filter config.')
    cfg.set_property('curve', curve)
    cfg.set_property('channel', Gimp.HistogramChannel.VALUE)
    f.update()
    mask.merge_filter(f)


# ─────────────────────────────────────────────────────────────────────────────
# Shared Cairo drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _draw_wheel_on(cr, cx, cy, outerR, ring_w, nx, ny):
    """Paint a complete colour wheel into an existing Cairo context."""
    innerR = outerR - ring_w - 2
    midR   = (outerR + innerR) / 2.0
    TWO_PI = 2 * math.pi

    # Spectrum ring
    for i in range(360):
        a1 = math.radians(i)
        a2 = math.radians(i + 1.5)
        r, g, b = _hsv_to_rgb(((360 - i) % 360) / 360.0, 1.0, 1.0)
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(ring_w + 1)
        cr.arc(cx, cy, midR, a1, a2)
        cr.stroke()

    # Dark disc
    cr.arc(cx, cy, innerR, 0, TWO_PI)
    cr.set_source_rgb(*_rgb('surface'))
    cr.fill()

    # Crosshair
    cr.set_line_width(0.5)
    cr.set_source_rgba(*_rgba('xhair'))
    cr.move_to(cx - innerR + 3, cy); cr.line_to(cx + innerR - 3, cy); cr.stroke()
    cr.move_to(cx, cy - innerR + 3); cr.line_to(cx, cy + innerR - 3); cr.stroke()

    # Handle position
    hx = cx + nx * innerR
    hy = cy + ny * innerR

    # Dashed guide line
    cr.set_dash([4.0, 2.5])
    cr.set_source_rgba(0.92, 0.92, 0.92, 0.82)
    cr.set_line_width(1.6)
    cr.move_to(cx, cy); cr.line_to(hx, hy); cr.stroke()
    cr.set_dash([])

    # Centre dot
    cr.arc(cx, cy, 4.2, 0, TWO_PI)
    cr.set_source_rgba(0.97, 0.97, 0.97, 0.98)
    cr.fill_preserve()
    cr.set_source_rgba(0.05, 0.05, 0.05, 0.95)
    cr.set_line_width(1.2)
    cr.stroke()

    # Handle ring
    hr = 5.0 if outerR < 30 else 7.0
    cr.arc(hx, hy, hr, 0, TWO_PI)
    cr.set_source_rgba(*_rgba('handle_ring'))
    cr.set_line_width(2.0)
    cr.stroke()

    # Handle fill dot
    dr = 1.5 if outerR < 30 else 2.0
    cr.arc(hx, hy, dr, 0, TWO_PI)
    cr.set_source_rgba(*_rgba('handle_dot'))
    cr.fill()


# ─────────────────────────────────────────────────────────────────────────────
# Colour wheel widget
# ─────────────────────────────────────────────────────────────────────────────

class ColourWheelWidget(Gtk.DrawingArea):
    """
    Cairo colour wheel.  Emits 'value-changed'(hue_deg, sat, r, g, b).
    size_px controls physical size; ring_w the ring thickness.
    """

    __gsignals__ = {
        'value-changed': (GObject.SignalFlags.RUN_LAST, None,
                          (float, float, float, float, float)),
    }

    def __init__(self, label='', size_px=WHEEL_SIZE, ring_w=RING_WIDTH):
        super().__init__()
        self.label       = label
        self._size_px    = size_px
        self._ring_w     = ring_w
        self.hue_deg     = 0.0
        self.saturation  = 0.0
        self._nx         = 0.0
        self._ny         = 0.0
        self._dragging   = False
        self._reset_pend = False
        self.set_size_request(size_px, size_px)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.BUTTON1_MOTION_MASK
        )
        self.connect('draw',                 self._on_draw)
        self.connect('button-press-event',   self._on_press)
        self.connect('button-release-event', self._on_release)
        self.connect('motion-notify-event',  self._on_motion)

    # ── public ───────────────────────────────────────────────────────────────

    def get_values(self):
        r, g, b = wheel_to_rgb(self.hue_deg, self.saturation)
        return self.hue_deg, self.saturation, r, g, b

    def reset(self):
        self._nx = self._ny = self.hue_deg = self.saturation = 0.0
        self.queue_draw()
        self.emit('value-changed', 0.0, 0.0, 0.0, 0.0, 0.0)

    def set_values(self, hue_deg, saturation, emit=False):
        # Programmatic synchronisation must not re-apply the grade.  Tab changes
        # are presentation changes only; user interaction remains the only path
        # that emits value-changed.
        rad = math.radians(hue_deg)
        self._nx = math.cos(rad) * saturation
        self._ny = -math.sin(rad) * saturation
        self.hue_deg = hue_deg
        self.saturation = saturation
        self.queue_draw()
        if emit:
            r, g, b = wheel_to_rgb(hue_deg, saturation)
            self.emit('value-changed', hue_deg, saturation, r, g, b)

    # ── geometry ─────────────────────────────────────────────────────────────

    def _geometry(self):
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        outerR = min(w, h) / 2.0 - 2
        return w / 2.0, h / 2.0, outerR

    # ── drawing ───────────────────────────────────────────────────────────────

    def _on_draw(self, _w, cr):
        try:
            cx, cy, outerR = self._geometry()
            _draw_wheel_on(cr, cx, cy, outerR, self._ring_w, self._nx, self._ny)
        except Exception:
            pass
        return False

    # ── mouse ────────────────────────────────────────────────────────────────

    def _inner_r(self):
        _, _, outerR = self._geometry()
        return outerR - self._ring_w - 2

    def _update_handle(self, ex, ey):
        cx, cy, _ = self._geometry()
        ir = self._inner_r()
        nx = (ex - cx) / ir
        ny = (ey - cy) / ir
        d  = math.sqrt(nx*nx + ny*ny)
        if d > 1.0: nx, ny = nx/d, ny/d
        self._nx, self._ny = nx, ny
        self.hue_deg  = math.degrees(math.atan2(-ny, nx)) % 360
        self.saturation = min(1.0, math.sqrt(nx*nx + ny*ny))
        self.queue_draw()

    def _on_press(self, _w, event):
        if event.button == 1:
            if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
                self._reset_pend = True
                self.reset()
            else:
                self._dragging = True
                self._update_handle(event.x, event.y)
        return True

    def _on_release(self, _w, event):
        if event.button == 1 and self._dragging:
            if self._reset_pend:
                self._reset_pend = self._dragging = False
                return True
            self._dragging = False
            self._update_handle(event.x, event.y)
            r, g, b = wheel_to_rgb(self.hue_deg, self.saturation)
            self.emit('value-changed', self.hue_deg, self.saturation, r, g, b)
        return True

    def _on_motion(self, _w, event):
        if self._dragging:
            self._update_handle(event.x, event.y)
        return True


def _thumbnail_rgb_pixels(drawable, max_w=640, max_h=480):
    """Return (width, height, [(r,g,b), ...]) from one GIMP-rendered thumbnail.

    Scopes intentionally work from a reduced preview, following the same design
    used here: scope analysis should never scan the full
    resolution image just to draw a measurement display.
    """
    if drawable is None:
        raise RuntimeError('No drawable is available for scope sampling.')
    result = drawable.get_thumbnail_data(max_w, max_h)
    vals = list(result) if isinstance(result, (tuple, list)) else [result]
    blob = None; nums = []
    for v in vals:
        if isinstance(v, (int, float)): nums.append(int(v))
        elif blob is None: blob = v
    if blob is None or len(nums) < 3:
        raise RuntimeError('GIMP returned an unexpected thumbnail-data result.')
    width, height, bpp = nums[-3], nums[-2], nums[-1]
    if hasattr(blob, 'get_data'): raw = bytes(blob.get_data())
    elif hasattr(blob, 'get_bytes'): raw = bytes(blob.get_bytes())
    else: raw = bytes(blob)
    if width <= 0 or height <= 0 or bpp <= 0 or not raw:
        raise RuntimeError(f'Invalid thumbnail ({width}×{height}, bpp={bpp}, bytes={len(raw)}).')
    limit=min(len(raw), width*height*bpp); px=[]
    for i in range(0, limit-bpp+1, bpp):
        if bpp >= 3:
            r,g,b=raw[i],raw[i+1],raw[i+2]; a=raw[i+3] if bpp >= 4 else 255
        else:
            r=g=b=raw[i]; a=raw[i+1] if bpp >= 2 else 255
        if a: px.append((r,g,b))
        else: px.append((0,0,0))
    return width, height, px


class ScopeDisplay(Gtk.DrawingArea):
    """Preview-resolution histogram/waveform/parade/vectorscope renderer."""
    MODES=('Histogram','Waveform','RGB Parade','Vectorscope')
    def __init__(self):
        super().__init__(); self.mode='Histogram'; self.data=None; self.error=''
        self.set_size_request(-1, 470); self.connect('draw', self._draw)

    def set_mode(self, mode): self.mode=mode; self.queue_draw()
    def refresh(self, drawable):
        w,h,px=_thumbnail_rgb_pixels(drawable)
        # 256-bin RGB histogram
        hist=[[0]*256 for _ in range(3)]
        # waveform density, deliberately modest resolution for responsive redraw
        ww=min(320,max(64,w)); wh=256
        wave=[bytearray(ww*wh) for _ in range(3)]
        # vectorscope density in YCbCr chroma plane (standard video-style orientation)
        vs=bytearray(256*256)
        sx=max(1,w/ww)
        for idx,(r,g,b) in enumerate(px):
            hist[0][r]+=1; hist[1][g]+=1; hist[2][b]+=1
            x=idx%w; wx=min(ww-1,int(x/sx))
            for c,v in enumerate((r,g,b)):
                pos=(255-v)*ww+wx
                if wave[c][pos] < 255: wave[c][pos]+=1
            # BT.709 YCbCr chroma. Luma intentionally discarded for vectorscope.
            cb=max(-.5,min(.5,(-0.114572*r-0.385428*g+0.5*b)/255.0))
            cr=max(-.5,min(.5,(0.5*r-0.454153*g-0.045847*b)/255.0))
            vx=max(0,min(255,int(128+cb*240))); vy=max(0,min(255,int(128-cr*240)))
            q=vy*256+vx
            if vs[q] < 255: vs[q]+=1
        self.data=(w,h,hist,wave,ww,vs); self.error=''; self.queue_draw()

    def _bg(self,cr,w,h):
        cr.set_source_rgb(.055,.055,.055); cr.rectangle(0,0,w,h); cr.fill()
        cr.set_line_width(1); cr.set_source_rgba(1,1,1,.10)
        for f in (.25,.5,.75): cr.move_to(0,h*f); cr.line_to(w,h*f); cr.stroke()

    def _draw(self,_w,cr):
        W=self.get_allocated_width(); H=self.get_allocated_height(); self._bg(cr,W,H)
        if not self.data:
            cr.set_source_rgba(1,1,1,.55); cr.move_to(18,32); cr.show_text(self.error or 'Open Scopes or press Refresh.'); return False
        iw,ih,hist,wave,ww,vs=self.data
        if self.mode=='Histogram':
            peak=max(max(a) for a in hist) or 1
            cols=((1,.22,.22),(.25,1,.35),(.25,.5,1))
            for bins,col in zip(hist,cols):
                cr.set_source_rgba(*col,.72); cr.set_line_width(1.35)
                for i,v in enumerate(bins):
                    x=i*(W-1)/255; y=H-8-(H-18)*math.sqrt(v/peak)
                    if i==0: cr.move_to(x,y)
                    else: cr.line_to(x,y)
                cr.stroke()
        elif self.mode in ('Waveform','RGB Parade'):
            cols=((1,.18,.18),(.18,1,.28),(.2,.45,1))
            panels=3 if self.mode=='RGB Parade' else 1
            for c,col in enumerate(cols):
                x0=(c*W/3) if panels==3 else 0; pw=W/3 if panels==3 else W
                arr=wave[c]; mx=max(arr) or 1
                cr.set_source_rgba(*col,.09 if panels==1 else .13)
                for yy in range(256):
                    for xx in range(ww):
                        n=arr[yy*ww+xx]
                        if not n: continue
                        a=min(.85, .08+.72*math.sqrt(n/mx))
                        cr.set_source_rgba(*col,a)
                        cr.rectangle(x0+xx*pw/ww, yy*H/256, max(1,pw/ww+0.4), max(1,H/256+0.4)); cr.fill()
        else:
            size=min(W,H)-28; ox=(W-size)/2; oy=(H-size)/2; cx=ox+size/2; cy=oy+size/2
            cr.set_source_rgba(1,1,1,.16); cr.arc(cx,cy,size*.47,0,2*math.pi); cr.stroke()
            cr.move_to(cx,oy); cr.line_to(cx,oy+size); cr.move_to(ox,cy); cr.line_to(ox+size,cy); cr.stroke()
            mx=max(vs) or 1
            for yy in range(256):
                for xx in range(256):
                    n=vs[yy*256+xx]
                    if not n: continue
                    # Colour the density point by its chroma direction for readability.
                    ang=math.atan2(128-yy,xx-128); hue=(ang/(2*math.pi))%1.0
                    r,g,b=colorsys.hsv_to_rgb(hue,.9,1.0)
                    cr.set_source_rgba(r,g,b,min(.85,.06+.75*math.sqrt(n/mx)))
                    cr.rectangle(ox+xx*size/256,oy+yy*size/256,max(1,size/256+0.3),max(1,size/256+0.3)); cr.fill()
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Histogram strip widget
# ─────────────────────────────────────────────────────────────────────────────

class HistogramStrip(Gtk.DrawingArea):
    """
    Live RGB histogram drawn from a GIMP drawable.
    Falls back to a plausible placeholder when no drawable is set.
    Call refresh(drawable) to update from the current image state.
    """

    def __init__(self):
        super().__init__()
        self._bins_r = []
        self._bins_g = []
        self._bins_b = []
        self.set_size_request(-1, 48)
        self.connect('draw', self._on_draw)

    def refresh(self, drawable):
        """Fast RGB histogram from a single rendered drawable thumbnail."""
        data=_thumbnail_histogram_data(drawable,128)
        self._bins_r=data['red']; self._bins_g=data['green']; self._bins_b=data['blue']
        self.queue_draw()

    def set_placeholder(self):
        """Generate plausible-looking demo histogram data."""
        bins = 64
        def _curve(peak, width, offset=0.0):
            return [
                max(0.0, 0.08 + peak * math.exp(-((i/bins - (0.35 + offset))**2) / (2*width**2))
                    + (peak*0.35) * math.exp(-((i/bins - (0.68 + offset))**2) / (2*(width*0.8)**2))
                    + 0.025 * math.sin(i * 0.4))
                for i in range(bins)
            ]
        self._bins_r = _curve(0.55, 0.13, -0.02)
        self._bins_g = _curve(0.48, 0.14,  0.05)
        self._bins_b = _curve(0.38, 0.12,  0.02)
        self.queue_draw()

    def _on_draw(self, _w, cr):
        try:
            w = self.get_allocated_width()
            h = self.get_allocated_height()

            # Background
            cr.rectangle(0, 0, w, h)
            cr.set_source_rgb(*_rgb('track_bg'))
            cr.fill()

            if not self._bins_r:
                # Empty state label
                cr.set_source_rgba(1, 1, 1, 0.15)
                cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(9)
                cr.move_to(6, h - 4)
                cr.show_text('histogram')
                return False

            bins = len(self._bins_r)
            bw   = w / bins

            global_peak=max(self._bins_r+self._bins_g+self._bins_b) if (self._bins_r or self._bins_g or self._bins_b) else 1.0
            if global_peak<=0: global_peak=1.0
            def _draw_channel(data, color_key, outline):
                pts=[]
                for i,v in enumerate(data):
                    x=i*bw+bw/2; bh=min(v/global_peak,1.0)*(h-8); pts.append((x,h-bh))
                if not pts: return
                cr.begin_new_path(); cr.move_to(0,h)
                for x,y in pts: cr.line_to(x,y)
                cr.line_to(w,h); cr.close_path(); cr.set_source_rgba(*_rgba(color_key)); cr.fill()
                cr.begin_new_path(); cr.move_to(*pts[0])
                for x,y in pts[1:]: cr.line_to(x,y)
                cr.set_source_rgba(outline[0],outline[1],outline[2],0.90); cr.set_line_width(1.15); cr.stroke()

            _draw_channel(self._bins_b, 'hist_b', (0.30,0.55,1.0))
            _draw_channel(self._bins_g, 'hist_g', (0.25,1.0,0.42))
            _draw_channel(self._bins_r, 'hist_r', (1.0,0.30,0.30))

            # Quartile guides
            cr.set_line_width(0.5)
            cr.set_source_rgba(*_rgba('hist_div'))
            for i in range(1, 4):
                x = w * i / 4
                cr.move_to(x, 0); cr.line_to(x, h); cr.stroke()

            # 0 / 255 labels
            cr.set_source_rgba(*_rgba('hist_txt'))
            cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8)
            cr.move_to(3,     h - 3); cr.show_text('0')
            cr.move_to(w - 18, h - 3); cr.show_text('255')

        except Exception:
            pass
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Range bar widgets  (circle-dot handles, stacked layout)
# ─────────────────────────────────────────────────────────────────────────────

class BandBar(Gtk.DrawingArea):
    """Single luminance-band gradient strip with circle-dot handle(s)."""

    _HANDLES        = []
    _GRADIENT_STOPS = []   # (offset, r, g, b)
    _DOT_CONFIGS    = []   # ('fill_key', 'ring_key')

    def __init__(self):
        super().__init__()
        self._drag_handle = HIT_NONE
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.set_size_request(-1, TOTAL_H)
        self.connect('draw',                 self._on_draw)
        self.connect('button-press-event',   self._on_press)
        self.connect('button-release-event', self._on_release)
        self.connect('motion-notify-event',  self._on_motion)

    # ── coordinate helpers ───────────────────────────────────────────────────

    def _bar_rect(self, alloc):
        bx = BAR_PAD
        bw = alloc.width - BAR_PAD * 2
        by = TOTAL_H // 2
        return bx, by, bw, BAR_H

    def _val_to_px(self, val, bx, bw):
        return bx + val * bw

    def _px_to_val(self, px, bx, bw):
        return max(0.0, min(1.0, (px - bx) / bw)) if bw > 0 else 0.0

    # ── drawing ───────────────────────────────────────────────────────────────

    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        bx, by, bw, bh = self._bar_rect(alloc)
        TWO_PI = 2 * math.pi

        # Recessed track background
        cr.rectangle(bx, by - bh // 2, bw, bh)
        cr.set_source_rgb(*_rgb('track_bg'))
        cr.fill()

        # Gradient overlay
        grad = cairo.LinearGradient(bx, 0, bx + bw, 0)
        for offset, r, g, b in self._GRADIENT_STOPS:
            grad.add_color_stop_rgb(offset, r, g, b)
        cr.rectangle(bx, by - bh // 2, bw, bh)
        cr.set_source(grad)
        cr.fill()

        # Hairline border on track
        cr.rectangle(bx, by - bh // 2, bw, bh)
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.set_line_width(0.5)
        cr.stroke()

        # Circle-dot handles
        for attr, (fill_key, ring_key) in zip(self._HANDLES, self._DOT_CONFIGS):
            val = getattr(self, attr)
            dx  = self._val_to_px(val, bx, bw)
            cr.arc(dx, by, DOT_R, 0, TWO_PI)
            cr.set_source_rgb(*_rgb(fill_key))
            cr.fill()
            cr.arc(dx, by, DOT_R, 0, TWO_PI)
            cr.set_source_rgba(*_rgba(ring_key))
            cr.set_line_width(1.2)
            cr.stroke()

        return False

    # ── hit testing ───────────────────────────────────────────────────────────

    def _hit_test(self, px, py, alloc):
        bx, by, bw, _ = self._bar_rect(alloc)
        hit_r = DOT_R + 4
        candidates = []
        for hit_id, attr in zip((HIT_LEFT, HIT_RIGHT), self._HANDLES):
            val  = getattr(self, attr)
            dist = abs(px - self._val_to_px(val, bx, bw))
            candidates.append((dist, hit_id))
        candidates.sort()
        if candidates and candidates[0][0] <= hit_r:
            return candidates[0][1]
        return HIT_NONE

    # ── drag ─────────────────────────────────────────────────────────────────

    def _apply_drag(self, px, alloc):
        bx, _, bw, _ = self._bar_rect(alloc)
        val = self._px_to_val(px, bx, bw)
        if self._drag_handle == HIT_LEFT and self._HANDLES:
            setattr(self, self._HANDLES[0], val)
        elif self._drag_handle == HIT_RIGHT and len(self._HANDLES) > 1:
            setattr(self, self._HANDLES[1], val)
        self._clamp_and_constrain()

    def _clamp_and_constrain(self):
        for attr in self._HANDLES:
            setattr(self, attr, max(0.0, min(1.0, getattr(self, attr))))

    # ── events ───────────────────────────────────────────────────────────────

    def _on_press(self, _w, event):
        if event.button == 1:
            alloc = self.get_allocation()
            h = self._hit_test(event.x, event.y, alloc)
            if h != HIT_NONE:
                self._drag_handle = h
                self._apply_drag(event.x, alloc)
                self.queue_draw()
        return True

    def _on_release(self, _w, event):
        if event.button == 1 and self._drag_handle != HIT_NONE:
            alloc = self.get_allocation()
            self._apply_drag(event.x, alloc)
            self._drag_handle = HIT_NONE
            self.queue_draw()
            self._emit_signal()
        return True

    def _on_motion(self, _w, event):
        if self._drag_handle != HIT_NONE:
            alloc = self.get_allocation()
            self._apply_drag(event.x, alloc)
            self.queue_draw()
        return True

    def _emit_signal(self):
        raise NotImplementedError


class ShadowsBandBar(BandBar):
    __gsignals__ = {
        'shadows-changed': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_DOUBLE,)),
    }
    _HANDLES        = ['shadows_mid']
    _GRADIENT_STOPS = [(0.0, 0.0, 0.0, 0.0), (1.0, 0.78, 0.78, 0.78)]
    _DOT_CONFIGS    = [('dot_shw_fill', 'dot_shw_ring')]

    def __init__(self):
        super().__init__()
        self.shadows_mid = DEFAULT_SHADOWS_MID

    def _emit_signal(self):
        self.emit('shadows-changed', self.shadows_mid)


class MidtonesBandBar(BandBar):
    __gsignals__ = {
        'midtones-changed': (GObject.SignalFlags.RUN_FIRST, None,
                             (GObject.TYPE_DOUBLE, GObject.TYPE_DOUBLE)),
    }
    _HANDLES        = ['midtones_left', 'midtones_right']
    _GRADIENT_STOPS = [(0.0, 0.78, 0.78, 0.78), (0.5, 0.0, 0.0, 0.0), (1.0, 0.78, 0.78, 0.78)]
    _DOT_CONFIGS    = [('dot_mid_fill', 'dot_mid_ring'), ('dot_mid_fill', 'dot_mid_ring')]

    def __init__(self):
        super().__init__()
        self.midtones_left  = DEFAULT_MIDTONES_LEFT
        self.midtones_right = DEFAULT_MIDTONES_RIGHT

    def _clamp_and_constrain(self):
        super()._clamp_and_constrain()
        self.midtones_left  = min(self.midtones_left,  0.5)
        self.midtones_right = max(self.midtones_right, 0.5)

    def _emit_signal(self):
        self.emit('midtones-changed', self.midtones_left, self.midtones_right)


class HighlightsBandBar(BandBar):
    __gsignals__ = {
        'highlights-changed': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_DOUBLE,)),
    }
    _HANDLES        = ['highlights_mid']
    _GRADIENT_STOPS = [(0.0, 0.0, 0.0, 0.0), (1.0, 0.86, 0.86, 0.86)]
    _DOT_CONFIGS    = [('dot_hlt_fill', 'dot_hlt_ring')]

    def __init__(self):
        super().__init__()
        self.highlights_mid = DEFAULT_HIGHLIGHTS_MID

    def _emit_signal(self):
        self.emit('highlights-changed', self.highlights_mid)


# ─────────────────────────────────────────────────────────────────────────────
# GIMP image engine (PreviewManager)
# ─────────────────────────────────────────────────────────────────────────────

class PreviewManager:
    """Stable GIMP 3.2 preview engine using native non-destructive drawable filters."""

    def __init__(self, image, drawable):
        self._image = image
        self._drawable = drawable
        self._preview_layer = None
        self._preview_group = None
        self._filters = {}
        self._sat_filter = None
        self._contrast_filter = None
        self._sat_value = 1.0
        self._contrast_value = 0.0
        self._pivot_value = 0.5
        self._undo_open = False
        self._initialised = False
        # Presets are applied as one transaction. Drawable filters still receive
        # their final values, but the canvas is flushed only once at the end.
        self._batch_depth = 0
        self._batch_dirty = False
        self._band_wheel = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._master_rgb = (0.0, 0.0, 0.0)
        self._range_values = {
            'shadows_mid': DEFAULT_SHADOWS_MID,
            'midtones_left': DEFAULT_MIDTONES_LEFT,
            'midtones_right': DEFAULT_MIDTONES_RIGHT,
            'highlights_mid': DEFAULT_HIGHLIGHTS_MID,
        }

    def begin_batch(self):
        self._batch_depth += 1

    def end_batch(self):
        if self._batch_depth > 0:
            self._batch_depth -= 1
        if self._batch_depth == 0 and self._batch_dirty:
            self._batch_dirty = False
            Gimp.displays_flush()

    def _display_flush(self):
        if self._batch_depth:
            self._batch_dirty = True
        else:
            Gimp.displays_flush()

    def initialise(self):
        try:
            self._image.undo_group_start()
            self._undo_open = True
            layer = Gimp.Layer.new_from_visible(self._image, self._image, 'Color grade preview')
            if layer is None:
                raise RuntimeError('Could not create the Color Managed Grading preview layer.')
            self._image.insert_layer(layer, None, 0)
            layer.set_visible(True)
            self._preview_layer = layer

            for band in BANDS:
                f = Gimp.DrawableFilter.new(layer, 'gimp:color-balance', BAND_LABEL[band])
                if f is None:
                    raise RuntimeError(f'GIMP could not create gimp:color-balance for {BAND_LABEL[band]}.')
                cfg = f.get_config()
                cfg.set_property('range', CB_TRANSFER[band])
                cfg.set_property('cyan-red', 0.0)
                cfg.set_property('magenta-green', 0.0)
                cfg.set_property('yellow-blue', 0.0)
                cfg.set_property('preserve-luminosity', True)
                f.update()
                layer.append_filter(f)
                self._filters[band] = f

            self._sat_filter = Gimp.DrawableFilter.new(layer, 'gimp:hue-saturation', 'Saturation')
            if self._sat_filter is not None:
                cfg = self._sat_filter.get_config()
                cfg.set_property('saturation', 0.0)
                self._sat_filter.update()
                layer.append_filter(self._sat_filter)

            self._contrast_filter = Gimp.DrawableFilter.new(layer, 'gimp:brightness-contrast', 'Contrast / Pivot')
            if self._contrast_filter is not None:
                cfg = self._contrast_filter.get_config()
                cfg.set_property('brightness', 0.0)
                cfg.set_property('contrast', 0.0)
                self._contrast_filter.update()
                layer.append_filter(self._contrast_filter)

            self._initialised = True
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to initialise Color Managed Grading')
            self.discard()

    def _range_strength(self, band):
        # Native GIMP color-balance supplies the tonal transfer.  The range
        # controls deliberately use a nonlinear gain curve so their complete
        # travel is useful rather than a cosmetic ±25% trim.  Defaults are 1x;
        # the practical extremes are about 0.37x..2.72x.
        if band == SHADOWS:
            x = max(0.0, min(1.0, float(self._range_values['shadows_mid'])))
            return math.exp((DEFAULT_SHADOWS_MID - x) * 2.0)
        if band == HIGHLIGHTS:
            x = max(0.0, min(1.0, float(self._range_values['highlights_mid'])))
            return math.exp((x - DEFAULT_HIGHLIGHTS_MID) * 2.0)
        left = max(0.0, min(1.0, float(self._range_values['midtones_left'])))
        right = max(0.0, min(1.0, float(self._range_values['midtones_right'])))
        width = max(0.02, right - left)
        default_width = DEFAULT_MIDTONES_RIGHT - DEFAULT_MIDTONES_LEFT
        ratio = width / default_width
        return max(0.25, min(3.0, ratio ** 1.35))

    def _apply_band(self, band):
        f = self._filters.get(band)
        if f is None:
            return
        cfg = f.get_config()
        br, bg, bb = self._band_wheel.get(band, (0.0, 0.0, 0.0))
        mr, mg, mb = self._master_rgb
        scale = CB_SCALE[band] * self._range_strength(band)
        cfg.set_property('cyan-red', max(-1.0, min(1.0, float(br) * scale + float(mr))))
        cfg.set_property('magenta-green', max(-1.0, min(1.0, float(bg) * scale + float(mg))))
        cfg.set_property('yellow-blue', max(-1.0, min(1.0, float(bb) * scale + float(mb))))
        f.update()

    def update_wheels(self, wheel_values):
        if not self._initialised:
            return
        try:
            for band in BANDS:
                _, _, r, g, b = wheel_values[band]
                self._band_wheel[band] = (r, g, b)
                self._apply_band(band)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update colour controls')

    def update_master(self, hue_deg, saturation):
        r, g, b = wheel_to_rgb(hue_deg, saturation)
        self.update_master_rgb(r, g, b)

    def update_master_rgb(self, r, g, b):
        if not self._initialised:
            return
        try:
            self._master_rgb = (float(r) * CB_SCALE[MASTER],
                                float(g) * CB_SCALE[MASTER],
                                float(b) * CB_SCALE[MASTER])
            for band in BANDS:
                self._apply_band(band)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update master control')

    def set_preview_enabled(self, enabled):
        if self._preview_layer is not None:
            self._preview_layer.set_visible(bool(enabled))
            self._display_flush()

    def update_ranges(self, range_values, band=None):
        if not self._initialised:
            return
        try:
            self._range_values.update(range_values)
            for b in (BANDS if band is None else (band,)):
                self._apply_band(b)
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update tonal ranges')

    def update_saturation(self, saturation):
        self._sat_value = float(saturation)
        if not self._initialised or self._sat_filter is None:
            return
        try:
            cfg = self._sat_filter.get_config()
            cfg.set_property('saturation', max(-1.0, min(1.0, float(saturation) - 1.0)))
            self._sat_filter.update()
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update saturation')

    def update_contrast(self, contrast, pivot):
        self._contrast_value = float(contrast)
        self._pivot_value = float(pivot)
        if not self._initialised or self._contrast_filter is None:
            return
        try:
            cfg = self._contrast_filter.get_config()
            # Deliberately photographic rather than extreme: the UI keeps its full
            # travel, while the engine maps it to a more useful working range.
            raw = max(-1.0, min(1.0, float(contrast)))
            # Fine control around zero, progressively stronger toward the ends.
            # Native brightness-contrast remains smooth; this response removes the
            # over-aggressive centre travel of the previous linear mapping.
            c = math.copysign((abs(raw) ** 1.65) * 0.62, raw) if raw else 0.0
            # GIMP's native contrast op has a fixed midpoint. Shift into/out of that
            # midpoint to make Pivot useful while retaining the native live filter.
            pv = max(0.05, min(0.95, float(pivot)))
            brightness = (0.5 - pv) * c * 1.10
            cfg.set_property('contrast', c)
            cfg.set_property('brightness', max(-0.45, min(0.45, brightness)))
            self._contrast_filter.update()
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc, 'Failed to update contrast / pivot')

    def discard(self):
        try:
            if self._preview_group is not None:
                self._image.remove_layer(self._preview_group)
            elif self._preview_layer is not None:
                self._image.remove_layer(self._preview_layer)
        except Exception:
            pass
        self._preview_group = None
        self._preview_layer = None
        self._filters.clear()
        self._sat_filter = None
        self._contrast_filter = None
        self._initialised = False
        self._close_undo()
        try: self._display_flush()
        except Exception: pass

    def commit(self, flatten):
        if not self._initialised or self._preview_layer is None:
            self._close_undo()
            return
        try:
            layer = self._preview_layer
            if flatten:
                try:
                    layer.merge_filters()
                except Exception:
                    # Keeping live filters is preferable to failing the commit.
                    pass
                try: layer.set_name('Color grade')
                except Exception: pass
            else:
                # Keep the preview layer itself as the single non-destructive FX layer.
                # All drawable filters already live directly on this layer, so wrapping it
                # in a group only adds unnecessary hierarchy to GIMP's layer stack.
                try: layer.set_name('Color Managed Grading')
                except Exception: pass
                self._preview_group = None
            self._preview_layer = None
            self._initialised = False
        finally:
            self._close_undo()
            try: self._display_flush()
            except Exception: pass

    def _close_undo(self):
        if self._undo_open:
            try: self._image.undo_group_end()
            except Exception: pass
            self._undo_open = False

    def _error_dialog(self, exc, title):
        try: Gimp.message(f'{title}:\n{exc}')
        except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# Colour bars widget  (R / G / B drag sliders per band)
# ─────────────────────────────────────────────────────────────────────────────

# Slider geometry
_SL_PAD    = 10   # horizontal inset inside the widget
_SL_H      = 10   # filled-bar height
_SL_ROW    = 26   # total row height (bar + dot clearance)
_SL_DOT_R  =  5   # handle dot radius
_SL_ROWS   = 3    # R, G, B
_SL_TOTAL  = _SL_ROW * _SL_ROWS + 8   # widget height

_CHANNELS  = ('R', 'G', 'B')
_FILL_KEYS = ('bar_r_fill', 'bar_g_fill', 'bar_b_fill')

# Value range: –1.0 … +1.0
_SL_MIN, _SL_MAX = -1.0, 1.0


class ColourBarsWidget(Gtk.DrawingArea):
    """
    Three stacked drag-sliders (R, G, B) for one tonal band.
    Values are in [–1, +1].  Emits 'values-changed'(r, g, b).
    Double-click on any slider row resets that channel to 0.
    """

    __gsignals__ = {
        'values-changed': (GObject.SignalFlags.RUN_LAST, None,
                           (float, float, float)),
    }

    def __init__(self):
        super().__init__()
        self._vals     = [0.0, 0.0, 0.0]   # R, G, B in [–1, +1]
        self._dragging = -1                 # channel index being dragged, or –1
        self.set_size_request(-1, _SL_TOTAL)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect('draw',                 self._on_draw)
        self.connect('button-press-event',   self._on_press)
        self.connect('button-release-event', self._on_release)
        self.connect('motion-notify-event',  self._on_motion)

    # ── public ───────────────────────────────────────────────────────────────

    def get_rgb(self):
        return tuple(self._vals)

    def set_rgb(self, r, g, b):
        self._vals = [
            max(_SL_MIN, min(_SL_MAX, r)),
            max(_SL_MIN, min(_SL_MAX, g)),
            max(_SL_MIN, min(_SL_MAX, b)),
        ]
        self.queue_draw()

    def reset(self):
        self._vals = [0.0, 0.0, 0.0]
        self.queue_draw()
        self.emit('values-changed', 0.0, 0.0, 0.0)

    # ── geometry helpers ─────────────────────────────────────────────────────

    def _row_y(self, ch):
        """Centre y of channel ch's bar."""
        return 4 + ch * _SL_ROW + _SL_ROW // 2

    def _track_x(self):
        w = self.get_allocated_width()
        return _SL_PAD, w - _SL_PAD - 36   # bx, bw  (36 reserves right-side label column)

    def _val_to_px(self, val, bx, bw):
        return bx + (val - _SL_MIN) / (_SL_MAX - _SL_MIN) * bw

    def _px_to_val(self, px, bx, bw):
        raw = (px - bx) / bw * (_SL_MAX - _SL_MIN) + _SL_MIN
        return max(_SL_MIN, min(_SL_MAX, raw))

    def _zero_px(self, bx, bw):
        return self._val_to_px(0.0, bx, bw)

    # ── drawing ───────────────────────────────────────────────────────────────

    def _on_draw(self, _w, cr):
        bx, bw = self._track_x()
        TWO_PI = 2 * math.pi

        for ch in range(_SL_ROWS):
            cy   = self._row_y(ch)
            val  = self._vals[ch]
            fill = _rgb(_FILL_KEYS[ch])
            zpx  = self._zero_px(bx, bw)
            dpx  = self._val_to_px(val, bx, bw)

            # Track background (recessed)
            cr.rectangle(bx, cy - _SL_H // 2, bw, _SL_H)
            cr.set_source_rgb(*_rgb('bar_track'))
            cr.fill()

            # Filled region from zero to value
            left_x  = min(zpx, dpx)
            fill_w  = abs(dpx - zpx)
            if fill_w > 0:
                cr.rectangle(left_x, cy - _SL_H // 2, fill_w, _SL_H)
                cr.set_source_rgb(*fill)
                cr.fill()

            # Track border hairline
            cr.rectangle(bx, cy - _SL_H // 2, bw, _SL_H)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.set_line_width(0.5)
            cr.stroke()

            # Zero-line
            cr.set_line_width(0.75)
            cr.set_source_rgba(*_rgba('bar_zero'))
            cr.move_to(zpx, cy - _SL_H // 2 - 2)
            cr.line_to(zpx, cy + _SL_H // 2 + 2)
            cr.stroke()

            # Handle dot
            cr.arc(dpx, cy, _SL_DOT_R, 0, TWO_PI)
            cr.set_source_rgb(*fill)
            cr.fill()
            cr.arc(dpx, cy, _SL_DOT_R, 0, TWO_PI)
            cr.set_source_rgba(1, 1, 1, 0.75)
            cr.set_line_width(1.2)
            cr.stroke()

            # Channel letter
            cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8)
            cr.set_source_rgb(*_rgb('bar_txt'))
            cr.move_to(bx - 9, cy + 3)
            cr.show_text(_CHANNELS[ch])

            # Numeric value (right-aligned past the track)
            val_str = f'{val:+.2f}'
            if val < 0:
                cr.set_source_rgb(*_rgb('bar_txt_neg'))
            else:
                cr.set_source_rgb(*_rgb('bar_txt'))
            cr.move_to(bx + bw + 4, cy + 3)
            cr.show_text(val_str)

        return False

    # ── hit test ─────────────────────────────────────────────────────────────

    def _hit_channel(self, px, py):
        bx, bw = self._track_x()
        for ch in range(_SL_ROWS):
            cy  = self._row_y(ch)
            dpx = self._val_to_px(self._vals[ch], bx, bw)
            if abs(py - cy) <= _SL_DOT_R + 4 and abs(px - dpx) <= _SL_DOT_R + 8:
                return ch
        return -1

    # ── events ───────────────────────────────────────────────────────────────

    def _on_press(self, _w, event):
        if event.button == 1:
            if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
                # reset the channel under the cursor
                bx, bw = self._track_x()
                for ch in range(_SL_ROWS):
                    if abs(event.y - self._row_y(ch)) <= _SL_ROW // 2:
                        self._vals[ch] = 0.0
                        self.queue_draw()
                        self.emit('values-changed', *self._vals)
                        return True
            ch = self._hit_channel(event.x, event.y)
            if ch >= 0:
                self._dragging = ch
                bx, bw = self._track_x()
                self._vals[ch] = self._px_to_val(event.x, bx, bw)
                self.queue_draw()
        return True

    def _on_release(self, _w, event):
        if event.button == 1 and self._dragging >= 0:
            bx, bw = self._track_x()
            self._vals[self._dragging] = self._px_to_val(event.x, bx, bw)
            self._dragging = -1
            self.queue_draw()
            self.emit('values-changed', *self._vals)
        return True

    def _on_motion(self, _w, event):
        if self._dragging >= 0:
            bx, bw = self._track_x()
            self._vals[self._dragging] = self._px_to_val(event.x, bx, bw)
            self.queue_draw()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Saturation slider widget
# ─────────────────────────────────────────────────────────────────────────────

class SaturationSlider(Gtk.DrawingArea):
    """
    Single horizontal drag slider: 0.0 … 2.0, default 1.0.
    Emits 'value-changed'(saturation).
    Double-click resets to 1.0.
    """

    __gsignals__ = {
        'value-changed': (GObject.SignalFlags.RUN_LAST, None, (float,)),
    }

    _MIN, _MAX, _DEFAULT = 0.0, 2.0, 1.0

    def __init__(self):
        super().__init__()
        self.value    = self._DEFAULT
        self._dragging = False
        self.set_size_request(-1, TOTAL_H)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect('draw',                 self._on_draw)
        self.connect('button-press-event',   self._on_press)
        self.connect('button-release-event', self._on_release)
        self.connect('motion-notify-event',  self._on_motion)

    def reset(self):
        self.value = self._DEFAULT
        self.queue_draw()
        self.emit('value-changed', self.value)

    def _track(self):
        w = self.get_allocated_width()
        return BAR_PAD, w - BAR_PAD * 2, TOTAL_H // 2

    def _val_to_px(self, bx, bw):
        return bx + (self.value - self._MIN) / (self._MAX - self._MIN) * bw

    def _px_to_val(self, px, bx, bw):
        return max(self._MIN, min(self._MAX,
               (px - bx) / bw * (self._MAX - self._MIN) + self._MIN))

    def _on_draw(self, _w, cr):
        bx, bw, cy = self._track()
        TWO_PI = 2 * math.pi

        # Track
        cr.rectangle(bx, cy - BAR_H // 2, bw, BAR_H)
        cr.set_source_rgb(*_rgb('track_bg'))
        cr.fill()

        # Gradient: grey→grey with gold highlight at mid (neutral point)
        grad = cairo.LinearGradient(bx, 0, bx + bw, 0)
        grad.add_color_stop_rgb(0.0,  0.10, 0.10, 0.10)
        grad.add_color_stop_rgb(0.5,  *_rgb('sat_fill'))
        grad.add_color_stop_rgb(1.0,  0.90, 0.85, 0.50)
        cr.rectangle(bx, cy - BAR_H // 2, bw, BAR_H)
        cr.set_source(grad)
        cr.fill()

        # Border
        cr.rectangle(bx, cy - BAR_H // 2, bw, BAR_H)
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.set_line_width(0.5)
        cr.stroke()

        # Centre tick (neutral = 1.0)
        mid_px = bx + bw * 0.5
        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.set_line_width(0.75)
        cr.move_to(mid_px, cy - BAR_H // 2 - 3)
        cr.line_to(mid_px, cy + BAR_H // 2 + 3)
        cr.stroke()

        # Handle dot
        dpx = self._val_to_px(bx, bw)
        cr.arc(dpx, cy, DOT_R, 0, TWO_PI)
        cr.set_source_rgb(*_rgb('sat_fill'))
        cr.fill()
        cr.arc(dpx, cy, DOT_R, 0, TWO_PI)
        cr.set_source_rgba(1, 1, 1, 0.75)
        cr.set_line_width(1.2)
        cr.stroke()

        return False

    def _on_press(self, _w, event):
        if event.button == 1:
            if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
                self.reset()
                return True
            self._dragging = True
            bx, bw, _ = self._track()
            self.value = self._px_to_val(event.x, bx, bw)
            self.queue_draw()
        return True

    def _on_release(self, _w, event):
        if event.button == 1 and self._dragging:
            bx, bw, _ = self._track()
            self.value = self._px_to_val(event.x, bx, bw)
            self._dragging = False
            self.queue_draw()
            self.emit('value-changed', self.value)
        return True

    def _on_motion(self, _w, event):
        if self._dragging:
            bx, bw, _ = self._track()
            self.value = self._px_to_val(event.x, bx, bw)
            self.queue_draw()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Contrast / Pivot widget  (two stacked sliders)
# ─────────────────────────────────────────────────────────────────────────────

class ContrastPivotWidget(Gtk.DrawingArea):
    """
    Two stacked sliders: Contrast (–1 … +1) and Pivot (0 … 1).
    Emits 'value-changed'(contrast, pivot).
    Double-click on a row resets that slider.
    """

    __gsignals__ = {
        'value-changed': (GObject.SignalFlags.RUN_LAST, None, (float, float)),
    }

    _ROW_H = TOTAL_H

    def __init__(self):
        super().__init__()
        self.contrast  = DEFAULT_CONTRAST
        self.pivot     = DEFAULT_PIVOT
        self._dragging = -1   # 0=contrast, 1=pivot
        self.set_size_request(-1, self._ROW_H * 2)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK   |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect('draw',                 self._on_draw)
        self.connect('button-press-event',   self._on_press)
        self.connect('button-release-event', self._on_release)
        self.connect('motion-notify-event',  self._on_motion)

    def reset(self):
        self.contrast = DEFAULT_CONTRAST
        self.pivot    = DEFAULT_PIVOT
        self.queue_draw()
        self.emit('value-changed', self.contrast, self.pivot)

    def _track(self, row):
        w  = self.get_allocated_width()
        bx = BAR_PAD
        bw = w - BAR_PAD * 2
        cy = row * self._ROW_H + self._ROW_H // 2
        return bx, bw, cy

    def _con_to_px(self, bx, bw):
        return bx + (self.contrast + 1.0) / 2.0 * bw

    def _piv_to_px(self, bx, bw):
        return bx + self.pivot * bw

    def _px_to_contrast(self, px, bx, bw):
        return max(-1.0, min(1.0, (px - bx) / bw * 2.0 - 1.0))

    def _px_to_pivot(self, px, bx, bw):
        return max(0.0, min(1.0, (px - bx) / bw))

    def _on_draw(self, _w, cr):
        TWO_PI = 2 * math.pi

        # row 0 = Contrast (fills from centre zero), row 1 = Pivot (fills from left)
        rows = [
            ('Contrast', 'contrast_fill', True,  self._con_to_px),
            ('Pivot',    'pivot_fill',    False, self._piv_to_px),
        ]
        for row, (label, fill_key, has_zero, dpx_fn) in enumerate(rows):
            bx, bw, cy = self._track(row)
            zpx = bx + 0.5 * bw   # centre zero for Contrast
            dpx = dpx_fn(bx, bw)

            # Track bg
            cr.rectangle(bx, cy - BAR_H // 2, bw, BAR_H)
            cr.set_source_rgb(*_rgb('track_bg'))
            cr.fill()

            # Fill region
            if has_zero:
                lx = min(zpx, dpx)
                fw = abs(dpx - zpx)
            else:
                lx = bx
                fw = dpx - bx
            if fw > 0.5:
                cr.rectangle(lx, cy - BAR_H // 2, fw, BAR_H)
                cr.set_source_rgb(*_rgb(fill_key))
                cr.fill()

            # Border
            cr.rectangle(bx, cy - BAR_H // 2, bw, BAR_H)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.set_line_width(0.5)
            cr.stroke()

            # Centre tick (Contrast only)
            if has_zero:
                cr.set_source_rgba(1, 1, 1, 0.22)
                cr.set_line_width(0.75)
                cr.move_to(zpx, cy - BAR_H // 2 - 3)
                cr.line_to(zpx, cy + BAR_H // 2 + 3)
                cr.stroke()

            # Handle dot
            cr.arc(dpx, cy, DOT_R, 0, TWO_PI)
            cr.set_source_rgb(*_rgb(fill_key))
            cr.fill()
            cr.arc(dpx, cy, DOT_R, 0, TWO_PI)
            cr.set_source_rgba(1, 1, 1, 0.75)
            cr.set_line_width(1.2)
            cr.stroke()

            # Row label (left of track)
            cr.set_source_rgb(0.4, 0.4, 0.4)
            cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8)
            cr.move_to(bx - BAR_PAD + 1, cy + 3)
            cr.show_text(label[0])   # 'C' or 'P'

            # Value (right)
            val = self.contrast if row == 0 else self.pivot
            val_str = f'{val:+.2f}' if row == 0 else f'{val:.2f}'
            cr.set_source_rgb(0.5, 0.5, 0.5)
            cr.move_to(bx + bw + 4, cy + 3)
            cr.show_text(val_str)

        return False

    def _hit_row(self, py):
        for row in range(2):
            _, _, cy = self._track(row)
            if abs(py - cy) <= self._ROW_H // 2:
                return row
        return -1

    def _on_press(self, _w, event):
        if event.button == 1:
            row = self._hit_row(event.y)
            if row < 0:
                return True
            if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
                if row == 0: self.contrast = DEFAULT_CONTRAST
                else:        self.pivot    = DEFAULT_PIVOT
                self.queue_draw()
                self.emit('value-changed', self.contrast, self.pivot)
                return True
            self._dragging = row
            bx, bw, _ = self._track(row)
            if row == 0: self.contrast = self._px_to_contrast(event.x, bx, bw)
            else:        self.pivot    = self._px_to_pivot(event.x, bx, bw)
            self.queue_draw()
        return True

    def _on_release(self, _w, event):
        if event.button == 1 and self._dragging >= 0:
            row = self._dragging
            bx, bw, _ = self._track(row)
            if row == 0: self.contrast = self._px_to_contrast(event.x, bx, bw)
            else:        self.pivot    = self._px_to_pivot(event.x, bx, bw)
            self._dragging = -1
            self.queue_draw()
            self.emit('value-changed', self.contrast, self.pivot)
        return True

    def _on_motion(self, _w, event):
        if self._dragging >= 0:
            row = self._dragging
            bx, bw, _ = self._track(row)
            if row == 0: self.contrast = self._px_to_contrast(event.x, bx, bw)
            else:        self.pivot    = self._px_to_pivot(event.x, bx, bw)
            self.queue_draw()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# CSS provider — dark grading panel
# ─────────────────────────────────────────────────────────────────────────────

_CSS = b"""
window.cg-dialog, dialog.cg-dialog {
    background-color: #252525;
}
.cg-panel {
    background-color: #252525;
}
scrolledwindow {
    background-color: #252525;
    border: none;
}
scrolledwindow > viewport {
    background-color: #252525;
}
scrollbar {
    background-color: #1e1e1e;
    min-width: 6px;
}
scrollbar slider {
    background-color: #484848;
    border-radius: 3px;
    min-height: 30px;
}
scrollbar slider:hover {
    background-color: #5a5a5a;
}
.cg-section-label {
    font-size: 9pt;
    color: #666666;
    letter-spacing: 1px;
}
.cg-readout {
    font-size: 8pt;
    color: #888888;
    font-family: monospace;
}
.cg-sep {
    background-color: #333333;
    min-height: 1px;
}
.cg-btn {
    background-color: #383838;
    color: #cccccc;
    border: 1px solid #484848;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 9pt;
}
.cg-btn:hover {
    background-color: #424242;
}
.cg-btn-ok {
    background-color: #2e4d6e;
    color: #90bfe8;
    border: 1px solid #5a8fc4;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 9pt;
    font-weight: bold;
}
.cg-btn-ok:hover {
    background-color: #375a80;
}
.cg-radio {
    color: #999999;
    font-size: 9pt;
}
.cg-mode-btn {
    background-color: #303030;
    color: #888888;
    border: 1px solid #444444;
    border-radius: 4px;
    padding: 3px 14px;
    font-size: 9pt;
    font-weight: 500;
}
.cg-mode-btn:hover {
    background-color: #3a3a3a;
    color: #aaaaaa;
}
.cg-mode-btn-active {
    background-color: #2e4d6e;
    color: #b0d1f0;
    border: 1px solid #5a8fc4;
    border-radius: 4px;
    padding: 3px 14px;
    font-size: 9pt;
    font-weight: 500;
}

.cg-module {
    background-image: none;
    background-color: #303030;
    color: #a8a8a8;
    border: 1px solid #4a4a4a;
    border-radius: 5px;
    padding: 5px 11px;
}
.cg-module:hover {
    background-image: none;
    background-color: #3a4652;
    color: #d8eaff;
    border-color: #6688aa;
}
.cg-module:checked, .cg-module-selected {
    background-image: none;
    background-color: #0878d1;
    color: #ffffff;
    border: 3px solid #b9e3ff;
    box-shadow: 0 0 12px 4px rgba(75, 180, 255, 0.95);
    font-weight: 900;
}
.cg-preset {
    background-image: none;
    background-color: #303030;
    color: #d0d0d0;
    border: 2px solid #555555;
    border-radius: 5px;
    padding: 7px 12px;
    font-weight: 700;
}
.cg-preset:hover {
    background-color: #3b4d5f;
    border-color: #82bfff;
}
.cg-preset:checked {
    background-image: none;
    background-color: #0878d1;
    color: #ffffff;
    border: 3px solid #d0ecff;
    box-shadow: 0 0 12px 4px rgba(75, 180, 255, 0.95);
    font-weight: 900;
}
.cg-scope-error {
    color: #ff9a9a;
    font-weight: bold;
}

.cg-bars-readout {
    font-size: 8pt;
    color: #888888;
    font-family: monospace;
    letter-spacing: 1px;
}
"""


def _apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: small uppercase label
# ─────────────────────────────────────────────────────────────────────────────

def _section_label(text):
    lbl = Gtk.Label(label=text.upper())
    lbl.get_style_context().add_class('cg-section-label')
    lbl.set_halign(Gtk.Align.START)
    return lbl


def _divider():
    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    sep.get_style_context().add_class('cg-sep')
    return sep


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class _BaseColorGradingDialog(Gtk.Dialog):

    def __init__(self, image, drawable):
        super().__init__(title='Color Managed Grading', use_header_bar=False)
        self._image    = image
        self._drawable = drawable
        self._ready    = False
        self._preview  = PreviewManager(image, drawable)

        self._wheel_timer   = {b: None for b in BANDS}
        self._range_timers  = {b: None for b in BANDS}
        self._master_timer    = None
        self._sat_timer       = None
        self._contrast_timer  = None
        self._wheel_values  = {b: (0.0, 0.0, 0.0, 0.0, 0.0) for b in BANDS}
        self._canonical_rgb = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._master_canonical_rgb = (0.0, 0.0, 0.0)
        # store per-band rgb cast for master mixing
        self._preview._band_wheel = {b: (0.0, 0.0, 0.0) for b in BANDS}

        _apply_css()
        self.get_style_context().add_class('cg-dialog')
        self._build_ui()
        self._ready = True
        GLib.idle_add(self._do_init)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.set_border_width(0)
        self.set_resizable(True)

        # Compact landscape dialog: wide enough for three proper grading wheels,
        # but short enough that the action buttons remain reachable.
        # GTK 3: Gdk.Screen.get_height() is deprecated. Query the active
        # monitor geometry instead; fall back safely when no monitor is exposed.
        sh = 900
        try:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() if display else None
            if monitor is None and display and display.get_n_monitors() > 0:
                monitor = display.get_monitor(0)
            if monitor is not None:
                sh = monitor.get_geometry().height
        except Exception:
            pass
        max_h = int(sh * 0.92)
        self.set_default_size(680, min(max_h, 820))

        # Outer content area — just holds the scrolled window
        outer = self.get_content_area()
        outer.set_spacing(0)
        outer.get_style_context().add_class('cg-panel')

        # ScrolledWindow: vertical scroll only, never horizontal
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Do NOT propagate natural height — let the dialog height cap it
        outer.pack_start(sw, True, True, 0)

        # Inner box — all widgets go here; use a Viewport-friendly Box
        ca = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        ca.set_border_width(9)
        ca.get_style_context().add_class('cg-panel')
        # ScrolledWindow.add() wraps in a Viewport automatically for a Box
        sw.add(ca)

        # ── 0. Mode switcher (Wheels / Bars) at the very top ───────────────
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        mode_box.set_halign(Gtk.Align.CENTER)

        self._btn_wheels = Gtk.Button(label='Colour Wheels')
        self._btn_bars   = Gtk.Button(label='Colour Bars')
        self._btn_wheels.get_style_context().add_class('cg-mode-btn-active')
        self._btn_bars.get_style_context().add_class('cg-mode-btn')
        self._btn_wheels.connect('clicked', self._on_mode_wheels)
        self._btn_bars.connect('clicked',   self._on_mode_bars)
        mode_box.pack_start(self._btn_wheels, False, False, 0)
        mode_box.pack_start(self._btn_bars,   False, False, 4)
        ca.pack_start(mode_box, False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)

        # ── 1. Gtk.Stack: wheels page / bars page ─────────────────────────
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(120)
        if hasattr(self._stack, 'set_vhomogeneous'):
            self._stack.set_vhomogeneous(False)

        # ── Page A: Colour Wheels ──────────────────────────────────────────
        wheels_page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._wheels   = {}
        self._readouts = {}

        for band in BANDS:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            col.set_halign(Gtk.Align.CENTER)

            wheel = ColourWheelWidget(label=BAND_LABEL[band],
                                      size_px=WHEEL_SIZE, ring_w=RING_WIDTH)
            wheel.connect('value-changed', self._on_wheel_changed, band)
            self._wheels[band] = wheel
            col.pack_start(wheel, False, False, 0)

            ro = Gtk.Label(label='H  0.0°   S 0.00')
            ro.get_style_context().add_class('cg-readout')
            ro.set_halign(Gtk.Align.CENTER)
            self._readouts[band] = ro
            col.pack_start(ro, False, False, 0)

            name_lbl = Gtk.Label(label=BAND_LABEL[band].upper())
            name_lbl.get_style_context().add_class('cg-section-label')
            name_lbl.set_halign(Gtk.Align.CENTER)
            col.pack_start(name_lbl, False, False, 2)

            wheels_page.pack_start(col, True, True, 4)

        self._stack.add_named(wheels_page, 'wheels')

        # ── Page B: Colour Bars ────────────────────────────────────────────
        bars_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._colour_bars   = {}
        self._bars_readouts = {}

        for band in BANDS:
            # band header row: label + R/G/B readout
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            band_lbl = Gtk.Label(label=BAND_LABEL[band].upper())
            band_lbl.get_style_context().add_class('cg-section-label')
            band_lbl.set_width_chars(6)
            band_lbl.set_halign(Gtk.Align.START)

            ro = Gtk.Label(label='R +0.00   G +0.00   B +0.00')
            ro.get_style_context().add_class('cg-bars-readout')
            ro.set_halign(Gtk.Align.END)
            self._bars_readouts[band] = ro

            hdr.pack_start(band_lbl, False, False, 0)
            hdr.pack_end(ro, False, False, 0)
            bars_page.pack_start(hdr, False, False, 0)

            cb = ColourBarsWidget()
            cb.connect('values-changed', self._on_bars_changed, band)
            self._colour_bars[band] = cb
            bars_page.pack_start(cb, True, True, 0)

        self._stack.add_named(bars_page, 'bars')
        self._stack.set_visible_child_name('wheels')

        # Never vertically expand the mode stack. Expanding it was the source of
        # the large dead area visible below the wheels.
        ca.pack_start(self._stack, False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 3)

        # Histogram intentionally omitted: the previous strip did not provide a
        # dependable live histogram and consumed valuable vertical space.
        self._histogram = None
        ca.pack_start(Gtk.Box(spacing=0), False, False, 2)

        # ── 3. Luminance range bars (stacked, labelled) ────────────────────
        ca.pack_start(_section_label('Luminance range'), False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 2)

        self._band_shadows    = ShadowsBandBar()
        self._band_midtones   = MidtonesBandBar()
        self._band_highlights = HighlightsBandBar()

        range_data = [
            ('Shadows',    self._band_shadows,
             'shadows-changed',    self._on_shadows_changed),
            ('Midtones',   self._band_midtones,
             'midtones-changed',   self._on_midtones_changed),
            ('Highlights', self._band_highlights,
             'highlights-changed', self._on_highlights_changed),
        ]
        for lbl_text, bar, sig, handler in range_data:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=lbl_text)
            lbl.get_style_context().add_class('cg-section-label')
            lbl.set_width_chars(9)
            lbl.set_halign(Gtk.Align.END)
            bar.connect(sig, handler)
            row.pack_start(lbl, False, False, 0)
            row.pack_start(bar, True,  True,  0)
            ca.pack_start(row, False, False, 0)

        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)
        ca.pack_start(_divider(), False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)

        # ── 4a. Saturation ─────────────────────────────────────────────────
        sat_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sat_hdr.pack_start(_section_label('Saturation'), False, False, 0)
        self._sat_readout = Gtk.Label(label='1.00')
        self._sat_readout.get_style_context().add_class('cg-readout')
        self._sat_readout.set_halign(Gtk.Align.END)
        sat_hdr.pack_end(self._sat_readout, False, False, 0)
        ca.pack_start(sat_hdr, False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 2)
        self._sat_slider = SaturationSlider()
        self._sat_slider.connect('value-changed', self._on_saturation_changed)
        ca.pack_start(self._sat_slider, False, False, 0)

        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)

        # ── 4b. Contrast / Pivot ───────────────────────────────────────────
        con_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        con_hdr.pack_start(_section_label('Contrast / Pivot'), False, False, 0)
        self._con_readout = Gtk.Label(label='C +0.00   P 0.50')
        self._con_readout.get_style_context().add_class('cg-readout')
        self._con_readout.set_halign(Gtk.Align.END)
        con_hdr.pack_end(self._con_readout, False, False, 0)
        ca.pack_start(con_hdr, False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 2)
        self._con_pivot = ContrastPivotWidget()
        self._con_pivot.connect('value-changed', self._on_contrast_changed)
        ca.pack_start(self._con_pivot, False, False, 0)

        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)
        ca.pack_start(_divider(), False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)

        # ── 4. Master — wheel in wheels mode, bars in bars mode ────────────
        # Header row (label + readout, always visible)
        master_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_master = _section_label('Master')
        lbl_master.set_halign(Gtk.Align.START)
        master_hdr.pack_start(lbl_master, False, False, 0)

        self._master_readout = Gtk.Label(label='H  0.0°   S 0.00')
        self._master_readout.get_style_context().add_class('cg-readout')
        self._master_readout.set_halign(Gtk.Align.END)
        master_hdr.pack_end(self._master_readout, False, False, 0)
        ca.pack_start(master_hdr, False, False, 0)

        # Stack: wheel page / bars page
        self._master_stack = Gtk.Stack()
        self._master_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._master_stack.set_transition_duration(120)

        # Wheel page
        master_wheel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        master_wheel_box.set_halign(Gtk.Align.CENTER)
        self._master_wheel = ColourWheelWidget(
            label='Master', size_px=WHEEL_SIZE_MASTER, ring_w=5)
        self._master_wheel.connect('value-changed', self._on_master_wheel_changed)
        master_wheel_box.pack_start(self._master_wheel, False, False, 0)
        self._master_stack.add_named(master_wheel_box, 'wheel')

        # Bars page
        self._master_bars = ColourBarsWidget()
        self._master_bars.connect('values-changed', self._on_master_bars_changed)
        self._master_stack.add_named(self._master_bars, 'bars')

        self._master_stack.set_visible_child_name('wheel')
        ca.pack_start(self._master_stack, False, False, 0)

        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)
        ca.pack_start(_divider(), False, False, 0)
        ca.pack_start(Gtk.Box(spacing=0), False, False, 4)

        # ── 5. Output option ───────────────────────────────────────────────
        opt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        opt_lbl = _section_label('Output')
        opt_lbl.set_width_chars(9)
        opt_lbl.set_halign(Gtk.Align.END)
        opt_box.pack_start(opt_lbl, False, False, 0)

        self._radio_flat  = Gtk.RadioButton.new_with_label(None, 'Merged layer')
        self._radio_group = Gtk.RadioButton.new_with_label_from_widget(
            self._radio_flat, 'Layer group')
        self._radio_flat.set_active(True)
        for rb in (self._radio_flat, self._radio_group):
            rb.get_style_context().add_class('cg-radio')
            opt_box.pack_start(rb, False, False, 0)
        # Output is intentionally fixed to one non-destructive FX layer.
        self._radio_group.set_active(True)
        opt_box.set_no_show_all(True)
        opt_box.hide()

        ca.pack_start(Gtk.Box(spacing=0), False, False, 5)

        # ── 6. Buttons ─────────────────────────────────────────────────────
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reset_btn  = Gtk.Button(label='Reset all')
        cancel_btn = Gtk.Button(label='Cancel')
        ok_btn     = Gtk.Button(label='OK')
        self._preview_toggle = Gtk.CheckButton(label='Preview')
        self._preview_toggle.set_active(True)
        self._preview_toggle.set_tooltip_text('Toggle the complete Color Managed Grading preview on/off')
        self._preview_toggle.connect('toggled', self._on_preview_toggled)
        for btn in (reset_btn, cancel_btn):
            btn.get_style_context().add_class('cg-btn')
        ok_btn.get_style_context().add_class('cg-btn-ok')
        reset_btn.connect('clicked',  self._on_reset)
        cancel_btn.connect('clicked', self._on_cancel)
        ok_btn.connect('clicked',     self._on_ok)
        btn_box.pack_start(reset_btn, False, False, 0)
        btn_box.pack_start(self._preview_toggle, False, False, 12)
        btn_box.pack_end(ok_btn,      False, False, 0)
        btn_box.pack_end(cancel_btn,  False, False, 0)
        ca.pack_start(btn_box, False, False, 0)

        self.show_all()

    # ── mode switching ────────────────────────────────────────────────────────

    def _on_mode_wheels(self, _btn):
        if self._stack.get_visible_child_name() == 'wheels':
            return
        # The RGB grade is canonical.  Switching tabs only projects that state
        # onto the wheel controls; it never re-applies or mutates the grade.
        for band in BANDS:
            r, g, b = self._canonical_rgb[band]
            hue, sat = rgb_to_wheel(r, g, b)
            self._wheels[band].set_values(hue, sat, emit=False)
            self._update_wheel_readout(band, hue, sat)
        mr, mg, mb = self._master_canonical_rgb
        mhue, msat = rgb_to_wheel(mr, mg, mb)
        self._master_wheel.set_values(mhue, msat, emit=False)
        self._update_master_readout_hs(mhue, msat)
        self._stack.set_visible_child_name('wheels')
        self._master_stack.set_visible_child_name('wheel')
        self._btn_wheels.get_style_context().remove_class('cg-mode-btn')
        self._btn_wheels.get_style_context().add_class('cg-mode-btn-active')
        self._btn_bars.get_style_context().remove_class('cg-mode-btn-active')
        self._btn_bars.get_style_context().add_class('cg-mode-btn')

    def _on_mode_bars(self, _btn):
        if self._stack.get_visible_child_name() == 'bars':
            return
        # Exact canonical RGB values go to the fine-tuning bars.  No colour
        # conversion and no preview update occurs simply because the tab changed.
        for band in BANDS:
            r, g, b = self._canonical_rgb[band]
            self._colour_bars[band].set_rgb(r, g, b)
            self._update_bars_readout(band, r, g, b)
        mr, mg, mb = self._master_canonical_rgb
        self._master_bars.set_rgb(mr, mg, mb)
        self._update_master_readout_rgb(mr, mg, mb)
        self._stack.set_visible_child_name('bars')
        self._master_stack.set_visible_child_name('bars')
        self._btn_bars.get_style_context().remove_class('cg-mode-btn')
        self._btn_bars.get_style_context().add_class('cg-mode-btn-active')
        self._btn_wheels.get_style_context().remove_class('cg-mode-btn-active')
        self._btn_wheels.get_style_context().add_class('cg-mode-btn')


    # ── init ─────────────────────────────────────────────────────────────────

    def _do_init(self):
        GimpUi.window_set_transient(self)
        Gimp.progress_init('Initialising Color Managed Grading…')
        self._preview.initialise()
        GLib.idle_add(self._refresh_histogram)
        return False

    def _refresh_histogram(self):
        if self._histogram is None:
            return False
        try:
            self._histogram.set_placeholder()
        except Exception:
            pass
        return False

    # ── readout helpers ───────────────────────────────────────────────────────

    def _update_wheel_readout(self, band, hue, sat):
        lbl = self._readouts.get(band)
        if lbl:
            lbl.set_text(f'H {hue:5.1f}°   S {sat:.2f}')

    def _update_bars_readout(self, band, r, g, b):
        lbl = self._bars_readouts.get(band)
        if lbl:
            lbl.set_text(f'R {r:+.2f}   G {g:+.2f}   B {b:+.2f}')

    def _update_master_readout_hs(self, hue, sat):
        """Wheel mode readout: H / S."""
        self._master_readout.set_text(f'H {hue:5.1f}°   S {sat:.2f}')

    def _update_master_readout_rgb(self, r, g, b):
        """Bars mode readout: R / G / B."""
        self._master_readout.set_text(f'R {r:+.2f}   G {g:+.2f}   B {b:+.2f}')

    # ── shared colour-push helper ─────────────────────────────────────────────

    def _push_band_colour(self, band, r, g, b):
        """Store cast and schedule a preview update — same path for both modes."""
        self._canonical_rgb[band] = (float(r), float(g), float(b))
        self._preview._band_wheel[band] = self._canonical_rgb[band]
        self._wheel_values[band] = (0.0, 0.0, r, g, b)   # hue/sat unused by engine
        if self._wheel_timer[band] is not None:
            GLib.source_remove(self._wheel_timer[band])
        self._wheel_timer[band] = GLib.timeout_add(80, self._flush_colour, band)

    def _flush_colour(self, band):
        self._wheel_timer[band] = None
        self._preview.update_wheels(self._wheel_values)
        return False

    # ── wheel signals ─────────────────────────────────────────────────────────

    def _on_wheel_changed(self, _wheel, hue, sat, r, g, b, band):
        self._update_wheel_readout(band, hue, sat)
        self._push_band_colour(band, r, g, b)

    # ── bars signals ──────────────────────────────────────────────────────────

    def _on_bars_changed(self, _cb, r, g, b, band):
        self._update_bars_readout(band, r, g, b)
        self._push_band_colour(band, r, g, b)

    # ── master controls ───────────────────────────────────────────────────────

    def _on_master_wheel_changed(self, _wheel, hue, sat, r, g, b):
        self._update_master_readout_hs(hue, sat)
        self._master_canonical_rgb = (float(r), float(g), float(b))
        if self._master_timer is not None:
            GLib.source_remove(self._master_timer)
        self._master_timer = GLib.timeout_add(80, self._flush_master_rgb, r, g, b)

    def _on_master_bars_changed(self, _cb, r, g, b):
        self._update_master_readout_rgb(r, g, b)
        self._master_canonical_rgb = (float(r), float(g), float(b))
        if self._master_timer is not None:
            GLib.source_remove(self._master_timer)
        self._master_timer = GLib.timeout_add(80, self._flush_master_rgb, r, g, b)

    def _flush_master_rgb(self, r, g, b):
        self._master_timer = None
        self._preview.update_master_rgb(r, g, b)
        return False

    # ── saturation signal ────────────────────────────────────────────────────

    def _on_saturation_changed(self, _slider, value):
        self._sat_readout.set_text(f'{value:.2f}')
        if self._sat_timer is not None:
            GLib.source_remove(self._sat_timer)
        self._sat_timer = GLib.timeout_add(80, self._flush_saturation, value)

    def _flush_saturation(self, value):
        self._sat_timer = None
        self._preview.update_saturation(value)
        return False

    # ── contrast/pivot signal ─────────────────────────────────────────────────

    def _on_contrast_changed(self, _widget, contrast, pivot):
        self._con_readout.set_text(f'C {contrast:+.2f}   P {pivot:.2f}')
        if self._contrast_timer is not None:
            GLib.source_remove(self._contrast_timer)
        self._contrast_timer = GLib.timeout_add(80, self._flush_contrast, contrast, pivot)

    def _flush_contrast(self, contrast, pivot):
        self._contrast_timer = None
        self._preview.update_contrast(contrast, pivot)
        return False

    # ── luminance range signals ───────────────────────────────────────────────

    def _on_shadows_changed(self, _bar, _mid):
        self._schedule_range(SHADOWS)

    def _on_midtones_changed(self, _bar, _l, _r):
        self._schedule_range(MIDTONES)

    def _on_highlights_changed(self, _bar, _mid):
        self._schedule_range(HIGHLIGHTS)

    def _schedule_range(self, band):
        if self._range_timers[band] is not None:
            GLib.source_remove(self._range_timers[band])
        self._range_timers[band] = GLib.timeout_add(80, self._flush_range, band)

    def _flush_range(self, band):
        self._range_timers[band] = None
        rv = {
            'shadows_mid':    self._band_shadows.shadows_mid,
            'highlights_mid': self._band_highlights.highlights_mid,
            'midtones_left':  self._band_midtones.midtones_left,
            'midtones_right': self._band_midtones.midtones_right,
        }
        self._preview.update_ranges(rv, band=band)
        return False

    # ── buttons ───────────────────────────────────────────────────────────────

    def _cancel_pending_preview_timers(self):
        """Remove deferred UI→preview callbacks so none can fire after close/commit."""
        for band in BANDS:
            sid = self._wheel_timer.get(band)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
                self._wheel_timer[band] = None
            sid = self._range_timers.get(band)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
                self._range_timers[band] = None
        for attr in ('_master_timer', '_sat_timer', '_contrast_timer'):
            sid = getattr(self, attr, None)
            if sid is not None:
                try: GLib.source_remove(sid)
                except Exception: pass
                setattr(self, attr, None)

    def _flush_pending_preview_state(self):
        """Synchronously push the latest controls before an Apply operation."""
        self._cancel_pending_preview_timers()
        self._preview.begin_batch()
        try:
            self._preview.update_wheels(self._wheel_values)
            self._preview.update_master_rgb(*self._master_canonical_rgb)
            self._preview.update_ranges({
                'shadows_mid': self._band_shadows.shadows_mid,
                'highlights_mid': self._band_highlights.highlights_mid,
                'midtones_left': self._band_midtones.midtones_left,
                'midtones_right': self._band_midtones.midtones_right,
            })
            self._preview.update_saturation(self._sat_slider.value)
            self._preview.update_contrast(self._con_pivot.contrast, self._con_pivot.pivot)
        finally:
            self._preview.end_batch()

    def _on_preview_toggled(self, button):
        self._preview.set_preview_enabled(button.get_active())

    def _on_reset(self, _btn):
        # Reset wheels
        for band, wheel in self._wheels.items():
            wheel.reset()
            self._update_wheel_readout(band, 0.0, 0.0)
        # Reset bars
        for band, cb in self._colour_bars.items():
            cb.reset()
            self._update_bars_readout(band, 0.0, 0.0, 0.0)
        # Reset master (both wheel and bars)
        self._master_wheel.reset()
        self._master_bars.reset()
        self._update_master_readout_hs(0.0, 0.0)
        self._update_master_readout_rgb(0.0, 0.0, 0.0)
        # Reset luminance range bars
        self._band_shadows.shadows_mid       = DEFAULT_SHADOWS_MID
        self._band_highlights.highlights_mid = DEFAULT_HIGHLIGHTS_MID
        self._band_midtones.midtones_left    = DEFAULT_MIDTONES_LEFT
        self._band_midtones.midtones_right   = DEFAULT_MIDTONES_RIGHT
        for bar in (self._band_shadows, self._band_midtones, self._band_highlights):
            bar.queue_draw()
        # Reset saturation and contrast/pivot
        self._sat_slider.reset()    # emits value-changed → sets readout
        self._con_pivot.reset()     # emits value-changed → sets readout

        # Push to preview
        self._wheel_values = {b: (0.0, 0.0, 0.0, 0.0, 0.0) for b in BANDS}
        self._preview._band_wheel = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._canonical_rgb = {b: (0.0, 0.0, 0.0) for b in BANDS}
        self._master_canonical_rgb = (0.0, 0.0, 0.0)
        self._preview.update_master_rgb(0.0, 0.0, 0.0)
        self._preview.update_wheels(self._wheel_values)
        self._preview.update_ranges({
            'shadows_mid':    DEFAULT_SHADOWS_MID,
            'highlights_mid': DEFAULT_HIGHLIGHTS_MID,
            'midtones_left':  DEFAULT_MIDTONES_LEFT,
            'midtones_right': DEFAULT_MIDTONES_RIGHT,
        })

    def _on_cancel(self, _btn):
        self._cancel_pending_preview_timers()
        self._preview.discard()
        self.destroy()

    def _on_ok(self, _btn):
        self._flush_pending_preview_state()
        self._preview.set_preview_enabled(True)
        if hasattr(self._preview, 'has_active_grade') and not self._preview.has_active_grade():
            self._preview.discard()
        else:
            self._preview.commit(False)
        self.destroy()




# ─────────────────────────────────────────────────────────────────────────────
# Extended Color Managed Grading UI / state
# ─────────────────────────────────────────────────────────────────────────────

CURVE_CHANNELS = ('Luminance', 'RGB', 'Red', 'Green', 'Blue')
_THUMB_HIST_LAST_ERROR=['']

CURVE_ENUMS = {
    'Luminance': Gimp.HistogramChannel.VALUE,
    'RGB':       Gimp.HistogramChannel.VALUE,
    'Red':       Gimp.HistogramChannel.RED,
    'Green':     Gimp.HistogramChannel.GREEN,
    'Blue':      Gimp.HistogramChannel.BLUE,
}


def _thumbnail_histogram_data(drawable, bins=128):
    """RGB/value histogram from one rendered thumbnail.

    Accepts either Gimp.Drawable or Gimp.Image.  GIMP 3 GI bindings return
    get_thumbnail_data() out-parameters in slightly different tuple layouts
    across builds, so this deliberately recognises the common layouts rather
    than silently returning an empty scope.
    """
    empty={'value':[0.0]*bins,'red':[0.0]*bins,'green':[0.0]*bins,'blue':[0.0]*bins}
    if drawable is None:
        _THUMB_HIST_LAST_ERROR[0]='No drawable is available for scope sampling.'
        return empty
    try:
        result=drawable.get_thumbnail_data(512,512)
        vals=list(result) if isinstance(result,(tuple,list)) else [result]
        blob=None; nums=[]
        for v in vals:
            if isinstance(v,(int,float)): nums.append(int(v))
            elif blob is None: blob=v
        if blob is None or len(nums)<3:
            _THUMB_HIST_LAST_ERROR[0]=f'Unexpected GIMP thumbnail result: {type(result).__name__}.'
            return empty
        # width/height/bpp are the three integer out values; tolerate a leading
        # boolean/status integer if a GI build happens to expose one.
        width,height,bpp=nums[-3],nums[-2],nums[-1]
        if hasattr(blob,'get_data'): data=bytes(blob.get_data())
        elif hasattr(blob,'get_bytes'): data=bytes(blob.get_bytes())
        else: data=bytes(blob)
        if width<=0 or height<=0 or bpp<=0 or not data:
            _THUMB_HIST_LAST_ERROR[0]=f'Invalid thumbnail data ({width}×{height}, bpp={bpp}, bytes={len(data)}).'
            return empty
        rr=[0.0]*bins; gg=[0.0]*bins; bb=[0.0]*bins; vv=[0.0]*bins
        limit=min(len(data),width*height*bpp)
        for i in range(0,limit-bpp+1,bpp):
            if bpp>=3:
                r,g,b=data[i],data[i+1],data[i+2]; a=(data[i+3]/255.0) if bpp>=4 else 1.0
            else:
                r=g=b=data[i]; a=(data[i+1]/255.0) if bpp>=2 else 1.0
            if a<=0: continue
            rr[min(bins-1,r*bins//256)]+=a; gg[min(bins-1,g*bins//256)]+=a; bb[min(bins-1,b*bins//256)]+=a
            y=int(round(.2126*r+.7152*g+.0722*b)); vv[min(bins-1,y*bins//256)]+=a
        def scale(a):
            if not any(a): return [0.0]*len(a)
            # 99th-percentile normalisation + sqrt display compression.  This is
            # a density scale, not a second 0..255 axis.
            pos=sorted(v for v in a if v>0); ref=pos[max(0,min(len(pos)-1,int((len(pos)-1)*.99)))] or max(pos) or 1.0
            return [min(1.0,math.sqrt(max(0.0,v)/ref)) for v in a]
        return {'value':scale(vv),'red':scale(rr),'green':scale(gg),'blue':scale(bb)}
    except Exception as exc:
        # Preserve the reason for the Scopes status line instead of failing blank.
        _THUMB_HIST_LAST_ERROR[0]=str(exc)
        return empty


_THUMB_HIST_CACHE = {}


def _cached_thumbnail_histograms(drawable, bins=128):
    try:
        key=(int(drawable.get_id()), bins)
    except Exception:
        key=(id(drawable), bins)
    cached=_THUMB_HIST_CACHE.get(key)
    if cached is None:
        cached=_thumbnail_histogram_data(drawable,bins)
        _THUMB_HIST_CACHE[key]=cached
    return cached


def _identity_points():
    return [(0.0, 0.0), (1.0, 1.0)]


class CurveEditor(Gtk.DrawingArea):
    """Compact smooth curve editor with a subdued histogram backdrop."""
    __gsignals__ = {
        'curve-changed': (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, drawable=None, histogram_channel=None, neutral_mid=False, curve_color=None):
        super().__init__()
        self._drawable = drawable
        self._hist_channel = histogram_channel or Gimp.HistogramChannel.VALUE
        self._neutral_mid = bool(neutral_mid)
        self._curve_color = curve_color
        self._points = [(0.0, 0.5), (1.0, 0.5)] if neutral_mid else _identity_points()
        self._active = None
        self._hist = None
        self.set_size_request(330, 330)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('draw', self._draw)
        self.connect('button-press-event', self._press)
        self.connect('button-release-event', self._release)
        self.connect('motion-notify-event', self._motion)

    def points(self):
        return [(float(x), float(y)) for x, y in self._points]

    def set_points(self, pts, emit=False):
        self._points = sorted([(max(0.0,min(1.0,float(x))), max(0.0,min(1.0,float(y)))) for x,y in pts])
        self.queue_draw()
        if emit: self.emit('curve-changed', self.points())

    def reset(self, emit=True):
        self._points = [(0.0, 0.5), (1.0, 0.5)] if self._neutral_mid else _identity_points()
        self.queue_draw()
        if emit: self.emit('curve-changed', self.points())

    def set_histogram_source(self, drawable, channel=None):
        self._drawable = drawable
        if channel is not None: self._hist_channel = channel
        self._hist = None
        self.queue_draw()

    def _histogram(self):
        if self._hist is not None:
            return self._hist
        all_hist=_cached_thumbnail_histograms(self._drawable,128)
        if self._hist_channel == Gimp.HistogramChannel.RED:
            self._hist=all_hist['red']
        elif self._hist_channel == Gimp.HistogramChannel.GREEN:
            self._hist=all_hist['green']
        elif self._hist_channel == Gimp.HistogramChannel.BLUE:
            self._hist=all_hist['blue']
        else:
            self._hist=all_hist['value']
        return self._hist

    def _xy(self, x, y):
        a=self.get_allocation(); pad=18
        return pad+x*(a.width-2*pad), pad+(1.0-y)*(a.height-2*pad)

    def _inv(self, px, py):
        a=self.get_allocation(); pad=18
        return (max(0,min(1,(px-pad)/max(1,a.width-2*pad))),
                max(0,min(1,1-(py-pad)/max(1,a.height-2*pad))))

    def _lut_float(self):
        if self._neutral_mid:
            # Smooth interpolation around a neutral 0.5 line.
            pts=self._points
            shifted=[(x, max(0,min(1,y))) for x,y in pts]
            return [v/255.0 for v in _monotone_cubic_lut(shifted)]
        return [v/255.0 for v in _monotone_cubic_lut(self._points)]

    def _draw(self, _w, cr):
        a=self.get_allocation(); w=a.width; h=a.height; pad=18
        cr.set_source_rgb(0.075,0.075,0.075); cr.paint()
        # grid
        cr.set_line_width(1)
        for i in range(5):
            p=pad+i*(w-2*pad)/4
            cr.set_source_rgba(1,1,1,0.07); cr.move_to(p,pad); cr.line_to(p,h-pad); cr.stroke()
            q=pad+i*(h-2*pad)/4
            cr.move_to(pad,q); cr.line_to(w-pad,q); cr.stroke()
        # histogram: low opacity fill + bright outline
        hist=self._histogram(); n=len(hist)
        if n:
            cr.move_to(pad,h-pad)
            for i,v in enumerate(hist):
                x=pad+i*(w-2*pad)/max(1,n-1); y=h-pad-v*(h-2*pad)*0.92
                cr.line_to(x,y)
            cr.line_to(w-pad,h-pad); cr.close_path()
            hc = self._curve_color if self._curve_color is not None else (0.78,0.81,0.88)
            cr.set_source_rgba(hc[0],hc[1],hc[2],0.16); cr.fill()
            cr.move_to(pad,h-pad-hist[0]*(h-2*pad)*0.92)
            for i,v in enumerate(hist[1:],1):
                x=pad+i*(w-2*pad)/max(1,n-1); y=h-pad-v*(h-2*pad)*0.92
                cr.line_to(x,y)
            cr.set_source_rgba(hc[0],hc[1],hc[2],0.72); cr.set_line_width(1.25); cr.stroke()
        # neutral reference
        cr.set_source_rgba(1,1,1,0.18); cr.set_line_width(1)
        if self._neutral_mid:
            y=self._xy(0,0.5)[1]; cr.move_to(pad,y); cr.line_to(w-pad,y)
        else:
            x0,y0=self._xy(0,0); x1,y1=self._xy(1,1); cr.move_to(x0,y0); cr.line_to(x1,y1)
        cr.stroke()
        # Tonal scale: 8-bit-equivalent input values. Histogram height is display-
        # normalized (sqrt-compressed), so the y height is density, not a 0..255 axis.
        cr.select_font_face('sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(8); cr.set_source_rgba(1,1,1,0.38)
        for val in (0,64,128,192,255):
            tx=pad+(val/255.0)*(w-2*pad); label=str(val)
            ext=cr.text_extents(label); tw=ext.width if hasattr(ext,'width') else ext[2]
            cr.move_to(max(pad,min(w-pad-tw,tx-tw/2)), h-4); cr.show_text(label)
        # smooth curve
        lut=_smooth_curve_values(self._points, 1024)
        if self._curve_color is None:
            cr.set_source_rgba(0.92,0.94,1.0,0.98)
        else:
            cr.set_source_rgba(*self._curve_color, 0.98)
        cr.set_line_width(2.2)
        cr.set_line_join(cairo.LINE_JOIN_ROUND); cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for i,y in enumerate(lut):
            x=i/max(1,len(lut)-1); px,py=self._xy(x,y)
            if i==0: cr.move_to(px,py)
            else: cr.line_to(px,py)
        cr.stroke()
        for i,(x,y) in enumerate(self._points):
            px,py=self._xy(x,y); cr.arc(px,py,5.0,0,math.pi*2)
            cr.set_source_rgb(0.95,0.95,0.98); cr.fill_preserve(); cr.set_source_rgb(0.15,0.15,0.15); cr.set_line_width(1); cr.stroke()
        return False

    def _nearest(self, px, py):
        best=None; bd=12
        for i,(x,y) in enumerate(self._points):
            qx,qy=self._xy(x,y); d=math.hypot(px-qx,py-qy)
            if d<bd: best=i; bd=d
        return best

    def _press(self, _w, e):
        if e.type == Gdk.EventType._2BUTTON_PRESS:
            self.reset(); return True
        if e.button == 3:
            i=self._nearest(e.x,e.y)
            if i is not None and i not in (0,len(self._points)-1):
                self._points.pop(i); self.queue_draw(); self.emit('curve-changed', self.points())
            return True
        if e.button != 1: return False
        i=self._nearest(e.x,e.y)
        if i is None:
            x,y=self._inv(e.x,e.y); self._points.append((x,y)); self._points.sort(); i=min(range(len(self._points)),key=lambda k:abs(self._points[k][0]-x))
        self._active=i; self.queue_draw(); return True

    def _motion(self, _w, e):
        if self._active is None: return False
        x,y=self._inv(e.x,e.y); i=self._active
        if i==0: x=0.0
        elif i==len(self._points)-1: x=1.0
        else: x=max(self._points[i-1][0]+0.002,min(self._points[i+1][0]-0.002,x))
        self._points[i]=(x,y); self.queue_draw(); self.emit('curve-changed', self.points()); return True

    def _release(self, *_): self._active=None; return True


class ColorWarpEditor(Gtk.DrawingArea):
    """Six-node hue/chroma warp inspired by photographic colour-zone tools.

    Nodes start on a neutral hue ring. Drag tangentially to shift hue and
    radially to change saturation for that source hue. Processing remains a
    separate native FX stage; this widget is only its editor/view.
    """
    __gsignals__={'warp-changed': (GObject.SignalFlags.RUN_LAST, None, (object,))}
    def __init__(self):
        super().__init__(); self.set_size_request(330,330)
        self._nodes=[(0.0,1.0)]*6  # (hue shift turns, saturation multiplier)
        self._active=None
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK|Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('draw',self._draw); self.connect('button-press-event',self._press); self.connect('button-release-event',self._release); self.connect('motion-notify-event',self._motion)
    def state(self): return [[float(h),float(s)] for h,s in self._nodes]
    def set_state(self,nodes,emit=False):
        vals=[]
        for item in list(nodes or [])[:6]:
            try: vals.append((max(-.5,min(.5,float(item[0]))),max(0.0,min(2.0,float(item[1])))))
            except Exception: vals.append((0.0,1.0))
        while len(vals)<6: vals.append((0.0,1.0))
        self._nodes=vals; self.queue_draw()
        if emit: self.emit('warp-changed',self.state())
    def reset(self,emit=True): self.set_state([(0.0,1.0)]*6,emit)
    def _geom(self):
        a=self.get_allocation(); cx=a.width/2; cy=a.height/2; r=max(35,(min(a.width,a.height)/2-30)*0.80); return cx,cy,r
    def _node_xy(self,i):
        cx,cy,r=self._geom(); shift,sat=self._nodes[i]; ang=(i/6.0+shift)*math.pi*2-math.pi/2; rr=r*(0.28+0.62*(sat/2.0)); return cx+math.cos(ang)*rr,cy+math.sin(ang)*rr
    def _draw(self,_w,cr):
        a=self.get_allocation(); cr.set_source_rgb(.075,.075,.075); cr.paint(); cx,cy,r=self._geom()
        # hue wheel guide
        for k in range(180):
            h=k/180.0; rr,gg,bb=colorsys.hsv_to_rgb(h,.78,.88); a0=h*2*math.pi-math.pi/2; a1=(k+1)/180*2*math.pi-math.pi/2
            cr.set_source_rgba(rr,gg,bb,.82); cr.set_line_width(18); cr.arc(cx,cy,r,a0,a1); cr.stroke()
        for frac in (.28,.59,.90): cr.set_source_rgba(1,1,1,.20); cr.set_line_width(1.35); cr.arc(cx,cy,r*frac,0,2*math.pi); cr.stroke()
        # neutral spokes + displaced nodes
        for i in range(6):
            base=i/6*2*math.pi-math.pi/2; bx=cx+math.cos(base)*r*.59; by=cy+math.sin(base)*r*.59; x,y=self._node_xy(i)
            # Show the colour journey directly on the spoke: source hue at the
            # neutral position blending into the currently warped hue at the node.
            sr,sg,sb=colorsys.hsv_to_rgb(i/6.0,.82,1.0)
            rr,gg,bb=colorsys.hsv_to_rgb((i/6+self._nodes[i][0])%1.0,.82,1.0)
            grad=cairo.LinearGradient(bx,by,x,y)
            grad.add_color_stop_rgba(0.0,sr,sg,sb,.88)
            grad.add_color_stop_rgba(1.0,rr,gg,bb,1.0)
            cr.set_source(grad); cr.set_line_width(3.0); cr.move_to(bx,by); cr.line_to(x,y); cr.stroke()
            cr.arc(x,y,8 if i==self._active else 7,0,2*math.pi); cr.set_source_rgb(rr,gg,bb); cr.fill_preserve(); cr.set_source_rgba(1,1,1,1.0); cr.set_line_width(2.2); cr.stroke()
        cr.set_source_rgba(1,1,1,.55); cr.set_font_size(10); cr.move_to(12,a.height-10); cr.show_text('angle = hue shift    radius = saturation')
        return False
    def _nearest(self,x,y):
        best=None; bd=18
        for i in range(6):
            px,py=self._node_xy(i); d=math.hypot(x-px,y-py)
            if d<bd: best=i; bd=d
        return best
    def _press(self,_w,e):
        if e.type==Gdk.EventType._2BUTTON_PRESS: self.reset(); return True
        if e.button!=1:return False
        self._active=self._nearest(e.x,e.y); self.queue_draw(); return self._active is not None
    def _motion(self,_w,e):
        if self._active is None:return False
        cx,cy,r=self._geom(); dx=e.x-cx; dy=e.y-cy; ang=(math.atan2(dy,dx)+math.pi/2)/(2*math.pi); base=self._active/6.0
        shift=((ang-base+.5)%1.0)-.5; radius=math.hypot(dx,dy)/r; sat=max(0.0,min(2.0,(radius-.28)/.62*2.0))
        self._nodes[self._active]=(shift,sat); self.queue_draw(); self.emit('warp-changed',self.state()); return True
    def _release(self,*_): self._active=None; self.queue_draw(); return True


class ManagedPreviewManager(PreviewManager):
    """Extends the known-good preview layer; every added stage is a native drawable filter."""
    def __init__(self, image, drawable):
        super().__init__(image, drawable)
        self._curve_filters={}
        self._curve_state={name:_identity_points() for name in CURVE_CHANNELS}
        self._hue_filters={}
        self._warp_filters={}
        self._warp_state=[(0.0,1.0)]*6
        self._primaries_filter=None
        self._temperature_filter=None
        self._tint_filter=None
        self._chroma_filter=None
        self._global_sat_filter=None
        self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}

    def initialise(self):
        super().initialise()
        if not self._initialised or self._preview_layer is None: return
        try:
            for name in CURVE_CHANNELS:
                f=Gimp.DrawableFilter.new(self._preview_layer,'gimp:curves',f'{name} Curve')
                if f is None: continue
                cfg=f.get_config(); cfg.set_property('channel',CURVE_ENUMS[name])
                c=Gimp.Curve.new(); c.set_curve_type(Gimp.CurveType.FREE); c.set_n_samples(256)
                for i in range(256): c.set_sample(i/255.0,i/255.0)
                cfg.set_property('curve',c); f.update(); self._preview_layer.append_filter(f); self._curve_filters[name]=f
            self._init_hue_filters()
            self._init_warp_filters()
            self._init_primary_global_filters()
            self._display_flush()
        except Exception as exc:
            self._error_dialog(exc,'Failed to initialise managed grading stages')

    def _init_hue_filters(self):
        # Six native hue ranges. Three independent hue-curve stages share these by summing
        # their requested adjustments before each update.
        self._hue_state={k:[0.0]*6 for k in ('hue','sat','lum')}
        ranges=[]
        try:
            ranges=[Gimp.HueRange.RED,Gimp.HueRange.YELLOW,Gimp.HueRange.GREEN,
                    Gimp.HueRange.CYAN,Gimp.HueRange.BLUE,Gimp.HueRange.MAGENTA]
        except Exception:
            return
        for i,rng in enumerate(ranges):
            f=Gimp.DrawableFilter.new(self._preview_layer,'gimp:hue-saturation',f'Hue Curve {i+1}')
            if f is None: continue
            cfg=f.get_config(); cfg.set_property('range',rng); cfg.set_property('hue',0.0); cfg.set_property('saturation',0.0); cfg.set_property('lightness',0.0)
            try: cfg.set_property('overlap',0.35)
            except Exception: pass
            f.update(); self._preview_layer.append_filter(f); self._hue_filters[i]=f

    def _init_warp_filters(self):
        try:
            ranges=[Gimp.HueRange.RED,Gimp.HueRange.YELLOW,Gimp.HueRange.GREEN,Gimp.HueRange.CYAN,Gimp.HueRange.BLUE,Gimp.HueRange.MAGENTA]
        except Exception: return
        for i,rng in enumerate(ranges):
            f=Gimp.DrawableFilter.new(self._preview_layer,'gimp:hue-saturation',f'Color Warp {i+1}')
            if f is None: continue
            cfg=f.get_config(); cfg.set_property('range',rng); cfg.set_property('hue',0.0); cfg.set_property('saturation',0.0); cfg.set_property('lightness',0.0)
            try: cfg.set_property('overlap',0.45)
            except Exception: pass
            f.update(); self._preview_layer.append_filter(f); self._warp_filters[i]=f

    def _init_primary_global_filters(self):
        # Primaries: GEGL channel-mixer is a native point filter and gives us a
        # compact RGB matrix stage without rasterising the layer.
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:channel-mixer','RGB Primaries')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('preserve-luminosity',True)
                for prop,val in (('rr-gain',1.0),('rg-gain',0.0),('rb-gain',0.0),('gr-gain',0.0),('gg-gain',1.0),('gb-gain',0.0),('br-gain',0.0),('bg-gain',0.0),('bb-gain',1.0)): cfg.set_property(prop,val)
                f.update(); self._preview_layer.append_filter(f); self._primaries_filter=f
        except Exception: self._primaries_filter=None
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:color-temperature','Temperature')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('original-temperature',6500.0); cfg.set_property('intended-temperature',6500.0); f.update(); self._preview_layer.append_filter(f); self._temperature_filter=f
        except Exception: self._temperature_filter=None
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gimp:color-balance','Global Tint')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('range',CB_TRANSFER[MIDTONES]); cfg.set_property('cyan-red',0.0); cfg.set_property('magenta-green',0.0); cfg.set_property('yellow-blue',0.0); cfg.set_property('preserve-luminosity',True); f.update(); self._preview_layer.append_filter(f); self._tint_filter=f
        except Exception: self._tint_filter=None
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:hue-chroma','Global Chroma')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('hue',0.0); cfg.set_property('chroma',0.0); cfg.set_property('lightness',0.0); f.update(); self._preview_layer.append_filter(f); self._chroma_filter=f
        except Exception: self._chroma_filter=None
        try:
            f=Gimp.DrawableFilter.new(self._preview_layer,'gegl:saturation','Global Saturation')
            if f is not None:
                cfg=f.get_config(); cfg.set_property('scale',1.0); f.update(); self._preview_layer.append_filter(f); self._global_sat_filter=f
        except Exception: self._global_sat_filter=None

    @staticmethod
    def _primary_matrix(state):
        # Start at identity. Hue rotates each primary column toward its two
        # neighbours; purity expands/contracts that column around neutral.
        m=[[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]]
        for j,key in enumerate(('red','green','blue')):
            # The puck is deliberately photographic rather than linear.  Its
            # centre is a broad fine-control zone and authority builds toward
            # the rim.  Angle supplies direction; radius supplies strength.
            hue_deg=max(-180.0,min(180.0,float(state.get(key,{}).get('hue',0.0))))
            radius=max(0.0,min(100.0,float(state.get(key,{}).get('purity',0.0))))/100.0
            strength=radius*radius
            purity=strength
            # Purity: positive strengthens the home channel while subtracting a
            # small equal amount from neighbours.  The squared response keeps
            # small puck movements subtle while preserving full range at rim.
            home=1.0+0.32*purity
            side=-0.16*purity
            col=[side,side,side]; col[j]=home
            # Hue direction is also weighted by radius: angle near the centre
            # must not create a large colour rotation from a tiny movement.
            hue=(hue_deg/180.0)*strength
            amount=0.28*hue
            nxt=(j+1)%3; prv=(j-1)%3
            col[nxt]+=amount; col[prv]-=amount
            for i in range(3): m[i][j]=max(-2.0,min(2.0,col[i]))
        return m

    def update_primaries(self,state):
        self._primaries_state={k:{'hue':float(state.get(k,{}).get('hue',0.0)),'purity':float(state.get(k,{}).get('purity',0.0))} for k in ('red','green','blue')}
        f=self._primaries_filter
        if f is None:return
        try:
            m=self._primary_matrix(self._primaries_state); cfg=f.get_config()
            props=(('rr-gain',m[0][0]),('rg-gain',m[0][1]),('rb-gain',m[0][2]),('gr-gain',m[1][0]),('gg-gain',m[1][1]),('gb-gain',m[1][2]),('br-gain',m[2][0]),('bg-gain',m[2][1]),('bb-gain',m[2][2]))
            for prop,val in props: cfg.set_property(prop,val)
            f.update(); self._display_flush()
        except Exception as exc:self._error_dialog(exc,'Failed to update RGB Primaries')

    def update_global_colour(self,state):
        self._global_colour_state={
            'temperature':float(state.get('temperature',0.0)), 'tint':float(state.get('tint',0.0)),
            'chroma':float(state.get('chroma',0.0)), 'saturation':float(state.get('saturation',100.0))}
        try:
            t=max(-100.0,min(100.0,self._global_colour_state['temperature']))
            if self._temperature_filter is not None:
                # Symmetric photographic control around D65; bounded by GEGL's range.
                kelvin=max(2500.0,min(10500.0,6500.0+t*40.0)); cfg=self._temperature_filter.get_config(); cfg.set_property('original-temperature',6500.0); cfg.set_property('intended-temperature',kelvin); self._temperature_filter.update()
            if self._tint_filter is not None:
                tint=max(-100.0,min(100.0,self._global_colour_state['tint']))/100.0; cfg=self._tint_filter.get_config(); cfg.set_property('magenta-green',max(-1.0,min(1.0,-tint*0.45))); self._tint_filter.update()
            if self._chroma_filter is not None:
                cfg=self._chroma_filter.get_config(); cfg.set_property('chroma',max(-100.0,min(100.0,self._global_colour_state['chroma']))); self._chroma_filter.update()
            if self._global_sat_filter is not None:
                cfg=self._global_sat_filter.get_config(); cfg.set_property('scale',max(0.0,min(2.0,self._global_colour_state['saturation']/100.0))); self._global_sat_filter.update()
            self._display_flush()
        except Exception as exc:self._error_dialog(exc,'Failed to update Global Colour')

    def update_color_warp(self,nodes):
        vals=[]
        for item in list(nodes or [])[:6]: vals.append((max(-.5,min(.5,float(item[0]))),max(0.0,min(2.0,float(item[1])))))
        while len(vals)<6: vals.append((0.0,1.0))
        self._warp_state=vals
        for i,f in self._warp_filters.items():
            shift,sat=vals[i]; cfg=f.get_config(); cfg.set_property('hue',max(-1.0,min(1.0,shift*2.0))); cfg.set_property('saturation',max(-1.0,min(1.0,sat-1.0))); f.update()
        self._display_flush()

    def update_curve(self,name,points):
        self._curve_state[name]=[(float(x),float(y)) for x,y in points]
        f=self._curve_filters.get(name)
        if f is None: return
        try:
            lut=_monotone_cubic_lut(points); c=Gimp.Curve.new(); c.set_curve_type(Gimp.CurveType.FREE); c.set_n_samples(256)
            for i,v in enumerate(lut): c.set_sample(i/255.0,v/255.0)
            cfg=f.get_config(); cfg.set_property('channel',CURVE_ENUMS[name]); cfg.set_property('curve',c); f.update(); self._display_flush()
        except Exception as exc: self._error_dialog(exc,f'Failed to update {name} curve')

    def update_hue_curve(self,kind,points):
        if not self._hue_filters: return
        lut=_monotone_cubic_lut(points)
        # sector centres across the cyclic hue axis
        vals=[]
        for x in (0.0,1/6,2/6,3/6,4/6,5/6):
            y=lut[min(255,int(round(x*255)))]/255.0
            vals.append(max(-1.0,min(1.0,(0.5-y)*2.0)))
        self._hue_state[kind]=vals
        for i,f in self._hue_filters.items():
            cfg=f.get_config(); cfg.set_property('hue',self._hue_state['hue'][i]); cfg.set_property('saturation',self._hue_state['sat'][i]); cfg.set_property('lightness',self._hue_state['lum'][i]); f.update()
        self._display_flush()


    @staticmethod
    def _points_neutral(points, mid=False, eps=1e-6):
        target=[(0.0,0.5),(1.0,0.5)] if mid else [(0.0,0.0),(1.0,1.0)]
        if len(points)!=len(target): return False
        return all(abs(a-c)<=eps and abs(b-d)<=eps for (a,b),(c,d) in zip(points,target))

    def _delete_filter(self, f):
        if f is None: return
        try:
            if f.is_valid(): f.delete()
        except Exception:
            try: f.delete()
            except Exception: pass

    def _prune_neutral_filters(self):
        """Leave only effects that materially contribute to the committed FX layer."""
        eps=1e-7
        master_active=any(abs(v)>eps for v in self._master_rgb)
        for band,f in list(self._filters.items()):
            band_active=any(abs(v)>eps for v in self._band_wheel.get(band,(0,0,0)))
            if not (master_active or band_active):
                self._delete_filter(f); self._filters.pop(band,None)
        if abs(getattr(self,'_sat_value',1.0)-1.0)<=eps:
            self._delete_filter(self._sat_filter); self._sat_filter=None
        if abs(getattr(self,'_contrast_value',0.0))<=eps:
            self._delete_filter(self._contrast_filter); self._contrast_filter=None
        for name,f in list(self._curve_filters.items()):
            if self._points_neutral(self._curve_state.get(name,_identity_points())):
                self._delete_filter(f); self._curve_filters.pop(name,None)
        for i,f in list(self._hue_filters.items()):
            active=False
            for kind in ('hue','sat','lum'):
                vals=self._hue_state.get(kind,[0.0]*6)
                if i < len(vals) and abs(vals[i])>eps: active=True; break
            if not active:
                self._delete_filter(f); self._hue_filters.pop(i,None)
        for i,f in list(self._warp_filters.items()):
            h,sa=self._warp_state[i] if i < len(self._warp_state) else (0.0,1.0)
            if abs(h)<=eps and abs(sa-1.0)<=eps:
                self._delete_filter(f); self._warp_filters.pop(i,None)
        if all(abs(float(self._primaries_state[k][p]))<=eps for k in ('red','green','blue') for p in ('hue','purity')):
            self._delete_filter(self._primaries_filter); self._primaries_filter=None
        gc=self._global_colour_state
        if abs(gc.get('temperature',0.0))<=eps: self._delete_filter(self._temperature_filter); self._temperature_filter=None
        if abs(gc.get('tint',0.0))<=eps: self._delete_filter(self._tint_filter); self._tint_filter=None
        if abs(gc.get('chroma',0.0))<=eps: self._delete_filter(self._chroma_filter); self._chroma_filter=None
        if abs(gc.get('saturation',100.0)-100.0)<=eps: self._delete_filter(self._global_sat_filter); self._global_sat_filter=None

    def has_active_grade(self, eps=1e-7):
        if any(any(abs(v)>eps for v in self._band_wheel.get(b,(0,0,0))) for b in BANDS): return True
        if any(abs(v)>eps for v in self._master_rgb): return True
        if abs(self._sat_value-1.0)>eps or abs(self._contrast_value)>eps: return True
        if any(not self._points_neutral(self._curve_state.get(n,_identity_points())) for n in CURVE_CHANNELS): return True
        if any(any(abs(v)>eps for v in self._hue_state.get(k,[0.0]*6)) for k in ('hue','sat','lum')): return True
        if any(abs(h)>eps or abs(sa-1.0)>eps for h,sa in self._warp_state): return True
        if any(abs(float(self._primaries_state[k][p]))>eps for k in ('red','green','blue') for p in ('hue','purity')): return True
        gc=self._global_colour_state
        return (abs(gc.get('temperature',0.0))>eps or abs(gc.get('tint',0.0))>eps or
                abs(gc.get('chroma',0.0))>eps or abs(gc.get('saturation',100.0)-100.0)>eps)

    def commit(self, flatten):
        if not flatten:
            self._prune_neutral_filters()
        super().commit(flatten)



class PrimaryPuck(Gtk.DrawingArea):
    """Artist-facing 2D primary control: angle = hue shift, radius = purity."""
    def __init__(self, key, changed_cb):
        super().__init__(); self.key=key; self.changed_cb=changed_cb
        self.hue=0.0; self.purity=0.0; self._drag=False
        self.set_size_request(170,170)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK|Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('draw',self._draw); self.connect('button-press-event',self._press); self.connect('button-release-event',self._release); self.connect('motion-notify-event',self._motion)
        self.set_tooltip_text('Drag anywhere in the full disc: angle rotates hue, distance from centre increases purity. Double-click resets.')
    def set_values(self,hue,purity,emit=False):
        self.hue=max(-180.0,min(180.0,float(hue))); self.purity=max(0.0,min(100.0,float(purity))); self.queue_draw()
        if emit and self.changed_cb: self.changed_cb(self.key,self.hue,self.purity)
    def _base_hue(self): return {'red':0.0,'green':1/3,'blue':2/3}[self.key]
    def _rgb(self,h,s=0.9,v=1.0): return colorsys.hsv_to_rgb(h%1.0,s,v)
    def _draw(self,w,cr):
        a=w.get_allocation(); W,H=a.width,a.height; cx,cy=W/2,H/2; R=max(28,min(W,H)/2-12)
        # Full colour field: hue varies with angle around the primary; purity with radius.
        for ring in range(1,19):
            r0=R*(ring-1)/18; r1=R*ring/18+1; sat=0.10+0.90*ring/18
            for i in range(72):
                ang0=2*math.pi*i/72; ang1=2*math.pi*(i+1)/72
                shift=((i/72.0)-0.5)*360.0
                rr,gg,bb=self._rgb(self._base_hue()+shift/360.0,sat,0.98)
                cr.set_source_rgb(rr,gg,bb); cr.move_to(cx+math.cos(ang0)*r0,cy+math.sin(ang0)*r0); cr.line_to(cx+math.cos(ang0)*r1,cy+math.sin(ang0)*r1); cr.line_to(cx+math.cos(ang1)*r1,cy+math.sin(ang1)*r1); cr.line_to(cx+math.cos(ang1)*r0,cy+math.sin(ang1)*r0); cr.close_path(); cr.fill()
        # readable guides
        for frac in (.25,.5,.75,1.0):
            cr.set_source_rgba(1,1,1,.26 if frac<1 else .55); cr.set_line_width(1.2 if frac<1 else 2.0); cr.arc(cx,cy,R*frac,0,2*math.pi); cr.stroke()
        cr.set_source_rgba(1,1,1,.55); cr.set_line_width(1.2); cr.move_to(cx-R,cy);cr.line_to(cx+R,cy);cr.move_to(cx,cy-R);cr.line_to(cx,cy+R);cr.stroke()
        # Neutral is the exact centre. Radius is purity (0..100); angle is hue shift.
        rad=R*(self.purity/100.0); ang=math.radians(self.hue-90)
        x=cx+math.cos(ang)*rad; y=cy+math.sin(ang)*rad
        nx=cx; ny=cy
        # gradient from original primary to current warped primary
        br,bg,bb=self._rgb(self._base_hue(),.88,1); er,eg,eb=self._rgb(self._base_hue()+self.hue/360.0,max(.05,min(1,.72+self.purity/360)),1)
        grad=cairo.LinearGradient(nx,ny,x,y); grad.add_color_stop_rgb(0,br,bg,bb); grad.add_color_stop_rgb(1,er,eg,eb)
        cr.set_source(grad); cr.set_line_width(5); cr.move_to(nx,ny); cr.line_to(x,y); cr.stroke()
        cr.set_source_rgb(1,1,1); cr.arc(nx,ny,5,0,2*math.pi); cr.fill(); cr.set_source_rgb(br,bg,bb); cr.arc(nx,ny,3.2,0,2*math.pi);cr.fill()
        cr.set_source_rgb(1,1,1); cr.arc(x,y,8,0,2*math.pi);cr.fill(); cr.set_source_rgb(er,eg,eb);cr.arc(x,y,5.5,0,2*math.pi);cr.fill()
        return False
    def _from_xy(self,x,y,emit=True):
        a=self.get_allocation(); cx,cy=a.width/2,a.height/2; R=max(28,min(a.width,a.height)/2-12); dx=x-cx;dy=y-cy
        ang=(math.degrees(math.atan2(dy,dx))+90+180)%360-180; self.hue=max(-180.0,min(180.0,ang))
        rad=min(R,math.hypot(dx,dy)); self.purity=max(0.0,min(100.0,(rad/R)*100.0)); self.queue_draw()
        if emit and self.changed_cb:self.changed_cb(self.key,self.hue,self.purity)
    def _press(self,w,e):
        if e.type==Gdk.EventType._2BUTTON_PRESS: self.set_values(0,0,True); return True
        if e.button==1:self._drag=True;self._from_xy(e.x,e.y,True);return True
        return False
    def _motion(self,w,e):
        if self._drag:self._from_xy(e.x,e.y,True);return True
        return False
    def _release(self,w,e): self._drag=False; return True

class GradientGradeBar(Gtk.DrawingArea):
    """Custom colour-grading bar. No Gtk.Scale is exposed."""
    def __init__(self,key,lo,hi,value,changed_cb):
        super().__init__(); self.key=key;self.lo=float(lo);self.hi=float(hi);self.value=float(value);self.changed_cb=changed_cb;self._drag=False
        self.set_size_request(-1,52); self.set_hexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK|Gdk.EventMask.BUTTON_RELEASE_MASK|Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('draw',self._draw);self.connect('button-press-event',self._press);self.connect('button-release-event',self._release);self.connect('motion-notify-event',self._motion)
    def get_value(self): return self.value
    def set_value(self,v,emit=False):
        self.value=max(self.lo,min(self.hi,float(v)));self.queue_draw()
        if emit and self.changed_cb:self.changed_cb(self.key,self.value)
    def _stops(self):
        if self.key=='temperature': return [(0,(.12,.38,1)),(.5,(.82,.82,.78)),(1,(1,.55,.10))]
        if self.key=='tint': return [(0,(.15,.78,.25)),(.5,(.80,.80,.76)),(1,(.92,.18,.78))]
        if self.key=='chroma': return [(0,(.35,.35,.35)),(.25,(.42,.48,.55)),(.5,(.22,.72,.66)),(.75,(.72,.35,.86)),(1,(1,.35,.18))]
        return [(0,(.45,.45,.45)),(.18,(.62,.52,.52)),(.38,(.95,.22,.18)),(.55,(.95,.82,.12)),(.70,(.16,.82,.32)),(.84,(.12,.55,1)),(1,(.82,.20,.92))]
    def _draw(self,w,cr):
        a=w.get_allocation(); x0=8;y0=13;bw=max(40,a.width-16);bh=25
        cr.set_source_rgb(.07,.07,.07);cr.rectangle(x0-2,y0-2,bw+4,bh+4);cr.fill()
        g=cairo.LinearGradient(x0,0,x0+bw,0)
        for pos,c in self._stops():g.add_color_stop_rgb(pos,*c)
        cr.set_source(g);cr.rectangle(x0,y0,bw,bh);cr.fill()
        neutral=100 if self.key=='saturation' else 0
        nt=(neutral-self.lo)/(self.hi-self.lo); nx=x0+nt*bw
        cr.set_source_rgba(1,1,1,.75);cr.set_line_width(1.3);cr.move_to(nx,y0-4);cr.line_to(nx,y0+bh+4);cr.stroke()
        t=(self.value-self.lo)/(self.hi-self.lo);hx=x0+t*bw
        cr.set_source_rgb(.05,.05,.05);cr.arc(hx,y0+bh/2,9,0,2*math.pi);cr.fill();cr.set_source_rgb(1,1,1);cr.set_line_width(2.5);cr.arc(hx,y0+bh/2,9,0,2*math.pi);cr.stroke()
        return False
    def _from_x(self,x,emit=True):
        a=self.get_allocation();x0=8;bw=max(40,a.width-16);t=max(0,min(1,(x-x0)/bw));self.set_value(self.lo+t*(self.hi-self.lo),emit)
    def _press(self,w,e):
        if e.type==Gdk.EventType._2BUTTON_PRESS:self.set_value(100 if self.key=='saturation' else 0,True);return True
        if e.button==1:self._drag=True;self._from_x(e.x,True);return True
        return False
    def _motion(self,w,e):
        if self._drag:self._from_x(e.x,True);return True
        return False
    def _release(self,w,e):self._drag=False;return True

class ColorManagedGradingDialog(_BaseColorGradingDialog):
    def __init__(self,image,drawable):
        super().__init__(image,drawable)
        # Replace the base manager before its queued initialise callback executes.
        self._preview=ManagedPreviewManager(image,drawable)
        self.set_title('Color Managed Grading')
        # Request an actual opening size, not merely GTK's default hint.
        # Some window managers restore/retain the previous dialog size and ignore
        # set_default_size(), so resize() is also issued after realization.
        self.set_default_size(680, 1000)
        self.set_size_request(680, 760)
        self.connect('map-event', self._force_initial_window_size)
        # WM close (title-bar X / Alt+F4) must cancel the current uncommitted
        # preview, never leave its real preview FX layer behind.
        self.connect('delete-event', self._on_window_delete)
        self._initial_resize_done = False
        self._closing_from_window = False
        # Colour-management settings live on the image, not on the preview layer.
        # Snapshot them so Cancel / WM-close can restore the exact pre-dialog state.
        try:
            self._original_simulation_profile = self._image.get_simulation_profile()
            self._have_original_simulation_profile = True
        except Exception:
            self._original_simulation_profile = None
            self._have_original_simulation_profile = False
        try: self._original_simulation_bpc = bool(self._image.get_simulation_bpc())
        except Exception: self._original_simulation_bpc = None
        self._curve_editors={}
        self._hue_editors={}
        self._enhance_tabs()
        # Default to a non-destructive FX group containing the live drawable filters.
        try:
            self._radio_group.set_label('FX layer (live filters)')
            self._radio_group.set_active(True)
            self._radio_flat.set_active(False)
            self._radio_flat.set_no_show_all(True)
            self._radio_flat.hide()
        except Exception: pass

    def _walk(self,w):
        yield w
        if isinstance(w,Gtk.Container):
            for c in w.get_children():
                yield from self._walk(c)

    def _restore_colour_management_on_cancel(self):
        if self._have_original_simulation_profile:
            try: self._image.set_simulation_profile(self._original_simulation_profile)
            except Exception: pass
        if self._original_simulation_bpc is not None:
            try: self._image.set_simulation_bpc(self._original_simulation_bpc)
            except Exception: pass

    def _on_cancel(self, _btn):
        self._cancel_pending_preview_timers()
        self._restore_colour_management_on_cancel()
        self._preview.discard()
        self.destroy()

    def _on_window_delete(self, widget, event):
        # delete-event is emitted only for a window-manager close request.
        # Consume it and route through the same discard path as Cancel.
        if self._closing_from_window:
            return True
        self._closing_from_window = True
        try:
            self._cancel_pending_preview_timers()
            self._restore_colour_management_on_cancel()
            self._preview.discard()
        finally:
            self.destroy()
        return True

    def _force_initial_window_size(self, widget, event):
        if not self._initial_resize_done:
            self._initial_resize_done = True
            # resize(), unlike set_default_size(), is an explicit WM size request.
            GLib.idle_add(self._apply_initial_window_size)
        return False

    def _apply_initial_window_size(self):
        try:
            self.resize(680, 1000)
        except Exception:
            pass
        return False

    def _enhance_tabs(self):
        outer=self.get_content_area()
        children=outer.get_children()
        if not children: return
        old=children[0]; outer.remove(old)
        # Hide old in-page action row; actions now remain permanently visible.
        for w in self._walk(old):
            if isinstance(w,Gtk.Button) and w.get_label() in ('Reset all','Cancel','OK'):
                p=w.get_parent()
                if p: p.set_no_show_all(True); p.hide()
        nb=Gtk.Notebook(); nb.set_scrollable(True); nb.set_hexpand(True); nb.set_vexpand(True)
        nb.append_page(old,Gtk.Label(label='Wheels / Bars'))
        nb.append_page(self._build_curves_page(),Gtk.Label(label='Curves'))
        nb.append_page(self._build_hue_page(),Gtk.Label(label='Hue Curves'))
        nb.append_page(self._build_colour_page(),Gtk.Label(label='Colour'))
        nb.append_page(self._build_cm_page(),Gtk.Label(label='Colour Management'))
        nb.append_page(self._build_scopes_page(),Gtk.Label(label='Scopes'))
        nb.append_page(self._build_presets_page(),Gtk.Label(label='Presets'))
        # Scopes are intentionally lazy. Computing a histogram during dialog
        # construction made the whole plug-in appear to take seconds to open.
        nb.connect('switch-page', self._on_notebook_switch_page)
        outer.pack_start(nb,True,True,0)
        # Permanent global action strip.
        bar=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8); bar.set_border_width(8)
        self._global_preview=Gtk.CheckButton(label='Preview'); self._global_preview.set_active(True); self._global_preview.connect('toggled',self._on_preview_toggled)
        reset=Gtk.Button(label='Reset All…'); reset.connect('clicked',self._confirm_global_reset)
        cancel=Gtk.Button(label='Cancel'); cancel.connect('clicked',self._on_cancel)
        applyb=Gtk.Button(label='Apply'); applyb.get_style_context().add_class('cg-btn-ok'); applyb.connect('clicked',self._on_ok)
        cont=Gtk.Button(label='Apply FX + Continue'); cont.set_tooltip_text('Commit this grade as a live FX layer, then continue grading from the accumulated result'); cont.connect('clicked',self._apply_fx_continue)
        bar.pack_start(self._global_preview,False,False,0); bar.pack_start(reset,False,False,8); bar.pack_end(applyb,False,False,0); bar.pack_end(cont,False,False,0); bar.pack_end(cancel,False,False,0)
        outer.pack_start(bar,False,False,0); self.show_all()

    def _header(self,title,reset_cb):
        b=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8); l=Gtk.Label(label=title); l.get_style_context().add_class('cg-section-label'); b.pack_start(l,False,False,0)
        r=Gtk.Button(label='Reset tab…'); r.set_tooltip_text(f'Reset only {title}'); r.connect('clicked',reset_cb); b.pack_end(r,False,False,0); return b

    def _build_curves_page(self):
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8); root.set_border_width(10); root.pack_start(self._header('Curves',self._confirm_curves_reset),False,False,0)
        chooser=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=4); stack=Gtk.Stack(); stack.set_transition_type(Gtk.StackTransitionType.NONE)
        curve_buttons=[]
        for name in CURVE_CHANNELS:
            btn=Gtk.Button(label=name); btn.get_style_context().add_class('cg-module'); chooser.pack_start(btn,False,False,0); curve_buttons.append((name,btn))
            page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            curve_colours={'Red':(1.0,0.28,0.28),'Green':(0.30,1.0,0.42),'Blue':(0.34,0.58,1.0)}
            ed=CurveEditor(self._drawable,CURVE_ENUMS[name],curve_color=curve_colours.get(name)); ed.connect('curve-changed',self._curve_changed,name); self._curve_editors[name]=ed
            rr=Gtk.Button(label='↺ Reset '+name); rr.set_tooltip_text('Reset only this curve'); rr.connect('clicked',lambda _b,n=name:self._curve_editors[n].reset())
            page.pack_start(ed,True,True,0); page.pack_start(rr,False,False,0)
            help_text={'Luminance':'Adjusts tonal brightness without deliberately targeting a single RGB colour channel.', 'RGB':'Adjusts the composite/master tonal response of the image.', 'Red':'Adjusts the red channel: raising adds red; lowering shifts toward cyan.', 'Green':'Adjusts the green channel: raising adds green; lowering shifts toward magenta.', 'Blue':'Adjusts the blue channel: raising adds blue; lowering shifts toward yellow.'}[name]
            hint=Gtk.Label(label=help_text); hint.set_xalign(0); hint.set_line_wrap(True); hint.get_style_context().add_class('dim-label'); page.pack_start(hint,False,False,3)
            stack.add_named(page,name)
            btn.connect('clicked',lambda b,n=name,st=stack,buttons=curve_buttons:self._select_module_button(b,n,st,buttons))
        warp_btn=Gtk.Button(label='Color Warp'); warp_btn.get_style_context().add_class('cg-module'); chooser.pack_start(warp_btn,False,False,0); curve_buttons.append(('Color Warp',warp_btn))
        warp_page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5); self._color_warp=ColorWarpEditor(); self._color_warp.connect('warp-changed',self._color_warp_changed)
        wr=Gtk.Button(label='↺ Reset Color Warp'); wr.set_tooltip_text('Reset only Color Warp'); wr.connect('clicked',lambda _b:self._color_warp.reset())
        warp_page.pack_start(self._color_warp,True,True,0); warp_page.pack_start(wr,False,False,0)
        wh=Gtk.Label(label='Warp colour families directly: drag around the wheel to shift hue; drag inward/outward to reduce/increase saturation. Six overlapping colour zones keep transitions smooth.'); wh.set_xalign(0); wh.set_line_wrap(True); wh.get_style_context().add_class('dim-label'); warp_page.pack_start(wh,False,False,3)
        stack.add_named(warp_page,'Color Warp'); warp_btn.connect('clicked',lambda b,st=stack,buttons=curve_buttons:self._select_module_button(b,'Color Warp',st,buttons))
        stack.set_visible_child_name('Luminance'); self._mark_module_selected(curve_buttons[0][1], curve_buttons); root.pack_start(chooser,False,False,0); root.pack_start(stack,True,True,0); return root

    def _build_hue_page(self):
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8); root.set_border_width(10); root.pack_start(self._header('Hue Curves',self._confirm_hue_reset),False,False,0)
        chooser=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=4); stack=Gtk.Stack(); stack.set_transition_type(Gtk.StackTransitionType.NONE)
        hue_buttons=[]
        for label,kind in (('Hue vs Hue','hue'),('Hue vs Sat','sat'),('Hue vs Lum','lum')):
            btn=Gtk.Button(label=label); btn.get_style_context().add_class('cg-module'); chooser.pack_start(btn,False,False,0); hue_buttons.append((kind,btn)); page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            ed=CurveEditor(self._drawable,Gimp.HistogramChannel.VALUE,neutral_mid=True); ed.connect('curve-changed',self._hue_changed,kind); self._hue_editors[kind]=ed
            rr=Gtk.Button(label='↺ Reset '+label); rr.connect('clicked',lambda _b,k=kind:self._hue_editors[k].reset())
            page.pack_start(ed,True,True,0); page.pack_start(rr,False,False,0)
            hue_help={'hue':'Remaps selected hues toward neighbouring hues while leaving overall saturation and luminance stages independent.', 'sat':'Changes saturation according to hue — useful for taming or emphasizing specific colour families.', 'lum':'Changes luminance according to hue — brighten or darken selected colour families without changing the RGB curves.'}[kind]
            hint=Gtk.Label(label=hue_help); hint.set_xalign(0); hint.set_line_wrap(True); hint.get_style_context().add_class('dim-label'); page.pack_start(hint,False,False,3)
            stack.add_named(page,kind); btn.connect('clicked',lambda b,k=kind,st=stack,buttons=hue_buttons:self._select_module_button(b,k,st,buttons))
        stack.set_visible_child_name('hue'); self._mark_module_selected(hue_buttons[0][1], hue_buttons); root.pack_start(chooser,False,False,0); root.pack_start(stack,True,True,0)
        note=Gtk.Label(label='Hue curves use GIMP’s native six hue ranges and remain independent of Wheels/Bars and RGB curves.'); note.set_line_wrap(True); note.set_halign(Gtk.Align.START); root.pack_start(note,False,False,0); return root

    def _mark_module_selected(self, selected, buttons):
        # Selection is visual state owned by us, not Gtk.ToggleButton state.
        # This prevents GTK's post-click toggle from making the highlight disagree
        # with the page/mode that was actually selected.
        for _key, button in buttons:
            ctx = button.get_style_context()
            if button is selected:
                ctx.add_class('cg-module-selected')
            else:
                ctx.remove_class('cg-module-selected')

    def _select_module_button(self, button, key, stack, buttons):
        self._mark_module_selected(button, buttons)
        stack.set_visible_child_name(key)

    def _grade_slider(self, label, lo, hi, step, value, digits, callback, tooltip=None):
        row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=10)
        lab=Gtk.Label(label=label); lab.set_xalign(0); lab.set_size_request(112,-1)
        adj=Gtk.Adjustment(value=value,lower=lo,upper=hi,step_increment=step,page_increment=step*10)
        scale=Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,adjustment=adj); scale.set_hexpand(True); scale.set_digits(digits); scale.set_value_pos(Gtk.PositionType.RIGHT)
        if tooltip: scale.set_tooltip_text(tooltip)
        scale.connect('value-changed',callback)
        row.pack_start(lab,False,False,0); row.pack_start(scale,True,True,0)
        return row,scale

    def _build_colour_page(self):
        # Primaries + Global Colour deliberately share one artist-facing page.
        # Keep the established puck/bar dimensions; use the slightly taller dialog
        # rather than shrinking controls or hiding helper text.
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8); root.set_border_width(10)
        root.pack_start(self._header('Colour',self._confirm_colour_reset),False,False,0)

        prim_title=Gtk.Label(); prim_title.set_markup('<b>Primaries</b>'); prim_title.set_xalign(0); root.pack_start(prim_title,False,False,0)
        note=Gtk.Label(label='Shape the underlying RGB palette directly. Drag anywhere in a puck: angle rotates that primary hue; distance from the centre increases purity up to the circumference. Double-click a puck to reset it.')
        note.set_xalign(0); note.set_line_wrap(True); root.pack_start(note,False,False,0)
        self._primary_controls={}; row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=12); row.set_homogeneous(True)
        for key,title in (('red','RED'),('green','GREEN'),('blue','BLUE')):
            card=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            lab=Gtk.Label(); lab.set_markup('<b>'+title+'</b>'); card.pack_start(lab,False,False,0)
            puck=PrimaryPuck(key,self._primary_puck_changed); card.pack_start(puck,False,False,0)
            read=Gtk.Label(label='Hue 0°   Purity 0'); read.get_style_context().add_class('dim-label'); card.pack_start(read,False,False,0)
            self._primary_controls[key]={'puck':puck,'readout':read}; row.pack_start(card,True,True,0)
        root.pack_start(row,False,False,0)
        hint=Gtk.Label(label='The coloured field is the control: angle changes hue, radius changes purity. The gradient spoke shows the original primary → its current position.')
        hint.set_xalign(0); hint.set_line_wrap(True); hint.get_style_context().add_class('dim-label'); root.pack_start(hint,False,False,0)

        sep=Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL); root.pack_start(sep,False,False,3)
        global_title=Gtk.Label(); global_title.set_markup('<b>Global Colour</b>'); global_title.set_xalign(0); root.pack_start(global_title,False,False,0)
        gnote=Gtk.Label(label='Whole-image colour character. Drag directly on each colour field; the centre marker is neutral. Double-click a field to return it to neutral.')
        gnote.set_xalign(0); gnote.set_line_wrap(True); root.pack_start(gnote,False,False,0)
        self._global_colour_controls={}; self._global_colour_readouts={}
        specs=(('temperature','Temperature',-100,100,0),('tint','Tint',-100,100,0),('chroma','Chroma',-100,100,0),('saturation','Saturation',0,200,100))
        for key,label,lo,hi,val in specs:
            head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); name=Gtk.Label(label=label); name.set_xalign(0); value=Gtk.Label(label=str(val)); value.set_xalign(1)
            head.pack_start(name,True,True,0); head.pack_end(value,False,False,0); root.pack_start(head,False,False,0)
            bar=GradientGradeBar(key,lo,hi,val,self._global_bar_changed); root.pack_start(bar,False,False,0); self._global_colour_controls[key]=bar; self._global_colour_readouts[key]=value
        return root

    def _build_primaries_page(self):
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10); root.set_border_width(12); root.pack_start(self._header('Primaries',self._confirm_primaries_reset),False,False,0)
        note=Gtk.Label(label='Shape the underlying RGB palette directly. Drag anywhere in a puck: angle rotates that primary hue; distance from the centre increases purity up to the circumference. Double-click a puck to reset it.')
        note.set_xalign(0); note.set_line_wrap(True); root.pack_start(note,False,False,0)
        self._primary_controls={}; row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=12); row.set_homogeneous(True)
        for key,title in (('red','RED'),('green','GREEN'),('blue','BLUE')):
            card=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            lab=Gtk.Label(); lab.set_markup('<b>'+title+'</b>'); card.pack_start(lab,False,False,0)
            puck=PrimaryPuck(key,self._primary_puck_changed); card.pack_start(puck,True,True,0)
            read=Gtk.Label(label='Hue 0°   Purity 0'); read.get_style_context().add_class('dim-label'); card.pack_start(read,False,False,0)
            self._primary_controls[key]={'puck':puck,'readout':read}; row.pack_start(card,True,True,0)
        root.pack_start(row,False,False,0)
        hint=Gtk.Label(label='The coloured field is the control: angle changes hue, radius changes purity. The gradient spoke shows the original primary → its current position.')
        hint.set_xalign(0);hint.set_line_wrap(True);hint.get_style_context().add_class('dim-label');root.pack_start(hint,False,False,0)
        return root

    def _primary_puck_changed(self,key,hue,purity):
        self._primary_controls[key]['readout'].set_text(f'Hue {hue:+.0f}°   Purity {purity:+.0f}')
        self._primary_changed(key,'hue',hue); self._primary_changed(key,'purity',purity)

    def _build_global_colour_page(self):
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12); root.set_border_width(12); root.pack_start(self._header('Global Colour',self._confirm_global_colour_reset),False,False,0)
        note=Gtk.Label(label='Whole-image colour character. Drag directly on each colour field; the centre marker is neutral. Double-click a field to return it to neutral.')
        note.set_xalign(0); note.set_line_wrap(True); root.pack_start(note,False,False,0)
        self._global_colour_controls={}; self._global_colour_readouts={}
        specs=(('temperature','Temperature',-100,100,0),('tint','Tint',-100,100,0),('chroma','Chroma',-100,100,0),('saturation','Saturation',0,200,100))
        for key,label,lo,hi,val in specs:
            head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); name=Gtk.Label(label=label);name.set_xalign(0);value=Gtk.Label(label=str(val));value.set_xalign(1);head.pack_start(name,True,True,0);head.pack_end(value,False,False,0);root.pack_start(head,False,False,0)
            bar=GradientGradeBar(key,lo,hi,val,self._global_bar_changed);root.pack_start(bar,False,False,0);self._global_colour_controls[key]=bar;self._global_colour_readouts[key]=value
        return root

    def _global_bar_changed(self,key,value):
        if hasattr(self,'_global_colour_readouts'): self._global_colour_readouts[key].set_text(f'{value:.0f}')
        self._global_colour_changed(key,value)

    def _build_cm_page(self):
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10); root.set_border_width(12); root.pack_start(self._header('Colour Management',self._confirm_cm_reset),False,False,0)
        info=Gtk.Label(); info.set_xalign(0); info.set_line_wrap(True)
        try:
            prof=self._image.get_color_profile(); pname=prof.get_label() if prof else 'GIMP built-in / untagged'
        except Exception: pname='GIMP managed image profile'
        info.set_markup('<b>Image profile</b>\n'+GLib.markup_escape_text(str(pname))+'\n\n<b>Proofing</b>\nProof settings are view/output-management state; they do not rewrite the creative grade.')
        root.pack_start(info,False,False,0)
        self._proof_status=Gtk.Label(label='No proof profile selected'); self._proof_status.set_xalign(0); self._proof_status.set_ellipsize(Pango.EllipsizeMode.MIDDLE); root.pack_start(self._proof_status,False,False,0)
        row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        choose=Gtk.Button(label='Choose ICC proof profile…'); choose.connect('clicked',self._choose_proof_profile)
        clear=Gtk.Button(label='Clear'); clear.set_tooltip_text('Remove the proof profile without changing the creative grade'); clear.connect('clicked',self._clear_proof_profile)
        row.pack_start(choose,True,True,0); row.pack_start(clear,False,False,0); root.pack_start(row,False,False,0)
        self._bpc=Gtk.CheckButton(label='Black point compensation'); self._bpc.set_active(self._original_simulation_bpc if self._original_simulation_bpc is not None else True); self._bpc.connect('toggled',self._apply_bpc); root.pack_start(self._bpc,False,False,0)
        try:
            import PyOpenColorIO as OCIO; ocio=f'OpenColorIO {OCIO.__version__} — Available in this GIMP runtime'
        except Exception: ocio='OpenColorIO unavailable to this GIMP Python runtime — ICC/GIMP colour management remains available'
        o=Gtk.Label(label=ocio); o.set_xalign(0); root.pack_start(o,False,False,0); return root

    def _ocio_available(self):
        try: import PyOpenColorIO; return True
        except Exception: return False

    def _build_scopes_page(self):
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8); root.set_border_width(10)
        root.pack_start(_section_label('Scopes'),False,False,0)
        chooser=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=4); self._scope_buttons=[]
        self._scope_display=ScopeDisplay()
        for mode in ScopeDisplay.MODES:
            b=Gtk.Button(label=mode); b.get_style_context().add_class('cg-module'); chooser.pack_start(b,False,False,0); self._scope_buttons.append((mode,b))
            b.connect('clicked',self._scope_mode_clicked,mode)
        self._mark_module_selected(self._scope_buttons[0][1], self._scope_buttons)
        root.pack_start(chooser,False,False,0); root.pack_start(self._scope_display,True,True,4)
        self._scope_status=Gtk.Label(label='Scopes use a reduced rendered preview for responsive analysis.'); self._scope_status.set_xalign(0); self._scope_status.set_line_wrap(True); root.pack_start(self._scope_status,False,False,0)
        refresh=Gtk.Button(label='Refresh Scopes'); refresh.connect('clicked',self._refresh_scope); root.pack_start(refresh,False,False,0)
        return root

    def _scope_mode_clicked(self,button,mode):
        self._mark_module_selected(button, self._scope_buttons)
        self._scope_display.set_mode(mode)

    def _build_presets_page(self):
        # Keep the global Preview / Reset / Cancel / Apply strip outside this
        # scroller.  Only the Presets page scrolls when its contents grow.
        scroll=Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.NONE)
        root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10); root.set_border_width(12)
        scroll.add(root)
        root.pack_start(_section_label('Presets'),False,False,0)
        lab=Gtk.Label(label='Selecting a built-in look REPLACES the previous built-in grade; presets do not stack. Manual edits after selection remain editable. JSON preserves the editable controls; .cube is a baked colour transform.'); lab.set_line_wrap(True); lab.set_xalign(0); root.pack_start(lab,False,False,0)
        reset=Gtk.Button(label='Reset Grade to Neutral'); reset.get_style_context().add_class('cg-btn'); reset.set_tooltip_text('Reset the creative grade without loading another look'); reset.connect('clicked',self._preset_reset); root.pack_start(reset,False,False,2)
        grid=Gtk.FlowBox(); grid.set_selection_mode(Gtk.SelectionMode.NONE); grid.set_min_children_per_line(1); grid.set_max_children_per_line(12); grid.set_row_spacing(8); grid.set_column_spacing(8); grid.set_homogeneous(True)
        presets=[('Warm Film','warm'),('Cool Cinema','cool'),('Soft Portrait','soft'),('Punchy Editorial','punchy'),('Muted Matte','matte'),('Clean Commercial','clean'),('Golden Hour','golden'),('Teal Shadows','teal'),('High Key','highkey'),('Low Key','lowkey'),('Vintage Fade','vintage'),('Landscape Pop','landscape')]
        self._preset_buttons=[]
        for label,key in presets:
            b=Gtk.ToggleButton(label=label); b.set_size_request(150,44); b.get_style_context().add_class('cg-preset'); self._preset_buttons.append((key,b))
            b.connect('toggled',self._preset_toggled,key); grid.add(b)
        root.pack_start(grid,False,False,4)

        sep=Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL); root.pack_start(sep,False,False,6)
        user_head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        uh=_section_label('User Presets'); user_head.pack_start(uh,True,True,0)
        save_user=Gtk.Button(label='Save current as preset…'); save_user.get_style_context().add_class('cg-btn')
        save_user.set_tooltip_text('Save the current editable grading parameters for use on other layers or images')
        save_user.connect('clicked',self._save_user_preset)
        delete_user=Gtk.Button(label='Delete user preset…'); delete_user.get_style_context().add_class('cg-btn')
        delete_user.set_tooltip_text('Choose one or more saved user presets to delete')
        delete_user.connect('clicked',self._delete_user_presets)
        user_head.pack_end(delete_user,False,False,0)
        user_head.pack_end(save_user,False,False,0); root.pack_start(user_head,False,False,0)
        self._user_preset_box=Gtk.FlowBox(); self._user_preset_box.set_selection_mode(Gtk.SelectionMode.NONE); self._user_preset_box.set_min_children_per_line(1); self._user_preset_box.set_max_children_per_line(12); self._user_preset_box.set_row_spacing(8); self._user_preset_box.set_column_spacing(8); self._user_preset_box.set_homogeneous(True)
        root.pack_start(self._user_preset_box,False,False,2)
        self._reload_user_presets()

        io=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        ex=Gtk.Button(label='Export editable preset…'); ex.connect('clicked',self._export_preset)
        im=Gtk.Button(label='Import editable preset…'); im.connect('clicked',self._import_preset)
        io.pack_start(ex,False,False,0); io.pack_start(im,False,False,0); root.pack_start(io,False,False,6)
        lutio=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        lin=Gtk.Button(label='Import .cube LUT…'); lin.set_tooltip_text('Validate a standard .cube LUT file')
        lout=Gtk.Button(label='Export .cube LUT…'); lout.set_tooltip_text('Available only when an exact LUT bake backend is available')
        lin.connect('clicked',self._import_cube_lut); lout.connect('clicked',self._export_cube_lut)
        lutio.pack_start(lin,False,False,0); lutio.pack_start(lout,False,False,0); root.pack_start(lutio,False,False,2)
        self._lut_status=Gtk.Label(label='No external LUT loaded.'); self._lut_status.set_xalign(0); self._lut_status.set_line_wrap(True); root.pack_start(self._lut_status,False,False,4)
        return scroll

    def _user_preset_dir(self):
        path=os.path.join(os.path.expanduser('~'),'.config','GIMP','3.2','color-managed-grading','presets')
        os.makedirs(path,exist_ok=True)
        return path

    def _reload_user_presets(self):
        box=getattr(self,'_user_preset_box',None)
        if box is None: return
        for child in list(box.get_children()): box.remove(child)
        try:
            files=sorted((f for f in os.listdir(self._user_preset_dir()) if f.lower().endswith('.json')),key=str.casefold)
        except Exception:
            files=[]
        if not files:
            empty=Gtk.Label(label='No user presets saved yet.'); empty.set_xalign(0); empty.get_style_context().add_class('dim-label'); box.add(empty)
        else:
            for filename in files:
                path=os.path.join(self._user_preset_dir(),filename)
                try:
                    with open(path,'r',encoding='utf-8') as f: data=json.load(f)
                    label=str(data.get('name') or os.path.splitext(filename)[0])
                except Exception:
                    continue
                b=Gtk.Button(label=label); b.set_size_request(150,44); b.set_hexpand(True); b.get_style_context().add_class('cg-preset')
                b.set_tooltip_text('Apply saved user preset: '+label)
                b.connect('clicked',self._apply_user_preset,path)
                box.add(b)
        box.show_all()

    def _save_user_preset(self,*_):
        d=Gtk.Dialog(title='Save User Preset',transient_for=self,modal=True)
        d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Save Preset',Gtk.ResponseType.OK)
        area=d.get_content_area(); area.set_spacing(8); area.set_border_width(12)
        lab=Gtk.Label(label='Preset name'); lab.set_xalign(0); area.pack_start(lab,False,False,0)
        entry=Gtk.Entry(); entry.set_activates_default(True); entry.set_placeholder_text('My grade'); area.pack_start(entry,False,False,0)
        d.set_default_response(Gtk.ResponseType.OK); d.show_all()
        if d.run()==Gtk.ResponseType.OK:
            name=entry.get_text().strip()
            if name:
                safe=''.join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip().replace(' ','-') or 'preset'
                path=os.path.join(self._user_preset_dir(),safe+'.json')
                try:
                    st=self._grade_state(); st['name']=name
                    with open(path,'w',encoding='utf-8') as f: json.dump(st,f,indent=2)
                    self._reload_user_presets()
                except Exception as exc: Gimp.message('Could not save user preset: '+str(exc))
        d.destroy()

    def _delete_user_presets(self,*_):
        try:
            entries=[]
            for filename in sorted((f for f in os.listdir(self._user_preset_dir()) if f.lower().endswith('.json')), key=str.casefold):
                path=os.path.join(self._user_preset_dir(),filename)
                try:
                    with open(path,'r',encoding='utf-8') as f: data=json.load(f)
                    label=str(data.get('name') or os.path.splitext(filename)[0])
                    entries.append((label,path))
                except Exception:
                    continue
        except Exception:
            entries=[]
        if not entries:
            Gimp.message('There are no user presets to delete.')
            return

        d=Gtk.Dialog(title='Delete User Presets',transient_for=self,modal=True)
        d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Continue…',Gtk.ResponseType.OK)
        d.set_default_size(460,420)
        area=d.get_content_area(); area.set_spacing(8); area.set_border_width(12)
        lab=Gtk.Label(label='Select the user presets to delete:'); lab.set_xalign(0); area.pack_start(lab,False,False,0)
        sw=Gtk.ScrolledWindow(); sw.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC); sw.set_min_content_height(260)
        choices=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4); sw.add(choices); area.pack_start(sw,True,True,0)
        checks=[]
        for label,path in entries:
            cb=Gtk.CheckButton(label=label); cb.set_tooltip_text(path); choices.pack_start(cb,False,False,0); checks.append((cb,label,path))
        d.show_all()
        response=d.run()
        selected=[(label,path) for cb,label,path in checks if cb.get_active()]
        d.destroy()
        if response!=Gtk.ResponseType.OK or not selected:
            return

        names=', '.join(label for label,_path in selected)
        confirm=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.WARNING,
                                  buttons=Gtk.ButtonsType.NONE,text='Delete selected user preset%s?' % ('' if len(selected)==1 else 's'))
        confirm.format_secondary_text(names+'\n\nThis cannot be undone.')
        confirm.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Delete',Gtk.ResponseType.OK)
        answer=confirm.run(); confirm.destroy()
        if answer!=Gtk.ResponseType.OK:
            return

        failed=[]
        for label,path in selected:
            try: os.remove(path)
            except Exception as exc: failed.append(label+': '+str(exc))
        self._reload_user_presets()
        if failed: Gimp.message('Could not delete some user presets:\n'+'\n'.join(failed))

    def _apply_user_preset(self,_button,path):
        try:
            with open(path,'r',encoding='utf-8') as f: st=json.load(f)
            if st.get('format')!='color-managed-grading-preset': raise ValueError('Not a Color Managed Grading preset')
            for _key,button in getattr(self,'_preset_buttons',[]):
                if button.get_active(): button.set_active(False)
            self._apply_grade_state(st)
        except Exception as exc:
            Gimp.message('Could not apply user preset: '+str(exc))

    def _preset_toggled(self, button, key):
        if not button.get_active():
            return
        for other_key, other in self._preset_buttons:
            if other is not button and other.get_active():
                other.set_active(False)
        self._apply_builtin_preset(key)

    def _preset_reset(self, *_):
        for _key, button in getattr(self,'_preset_buttons',[]):
            if button.get_active(): button.set_active(False)
        self._apply_builtin_preset('neutral')

    def _grade_state(self):
        return {
            'format':'color-managed-grading-preset','version':1,
            'bands':{b:list(self._canonical_rgb.get(b,(0,0,0))) for b in BANDS},
            'master':list(self._master_canonical_rgb),
            'saturation':float(self._sat_slider.value),
            'contrast':float(self._con_pivot.contrast),'pivot':float(self._con_pivot.pivot),
            'curves':{n:self._curve_editors[n].points() for n in CURVE_CHANNELS},
            'hue_curves':{k:self._hue_editors[k].points() for k in self._hue_editors},
            'color_warp':self._color_warp.state(),
            'primaries':getattr(self,'_primaries_state',{'red':{'hue':0,'purity':0},'green':{'hue':0,'purity':0},'blue':{'hue':0,'purity':0}}),
            'global_colour':getattr(self,'_global_colour_state',{'temperature':0,'tint':0,'chroma':0,'saturation':100})
        }

    def _apply_grade_state(self,st):
        # Atomic preset transaction: update the model/widgets silently, update all
        # drawable filters, then flush the GIMP canvas exactly once.
        self._preview.begin_batch()
        try:
            bands=st.get('bands',{})
            for band in BANDS:
                rgb=bands.get(band,[0,0,0])
                self._canonical_rgb[band]=tuple(map(float,rgb[:3]))
                self._wheel_values[band]=(0,0,*self._canonical_rgb[band])
                try:
                    self._colour_bars[band].set_rgb(*self._canonical_rgb[band])
                    h,sa=rgb_to_wheel(*self._canonical_rgb[band])
                    self._wheels[band].set_values(h,sa,False)
                    self._update_bars_readout(band,*self._canonical_rgb[band])
                    self._update_wheel_readout(band,h,sa)
                except Exception:
                    pass
            self._preview._band_wheel=dict(self._canonical_rgb)
            self._preview.update_wheels(self._wheel_values)

            m=tuple(map(float,st.get('master',[0,0,0])[:3]))
            self._master_canonical_rgb=m
            self._preview.update_master_rgb(*m)
            try:
                self._master_bars.set_rgb(*m)
                h,sa=rgb_to_wheel(*m)
                self._master_wheel.set_values(h,sa,False)
                self._update_master_readout_rgb(*m)
            except Exception:
                pass

            sat=max(0,min(2,float(st.get('saturation',1))))
            self._sat_slider.value=sat
            self._sat_slider.queue_draw()
            self._sat_readout.set_text(f'{sat:.2f}')
            self._preview.update_saturation(sat)

            con=max(-1,min(1,float(st.get('contrast',0))))
            piv=max(0,min(1,float(st.get('pivot',0.5))))
            self._con_pivot.contrast=con
            self._con_pivot.pivot=piv
            self._con_pivot.queue_draw()
            self._con_readout.set_text(f'C {con:+.2f}   P {piv:.2f}')
            self._preview.update_contrast(con,piv)

            for name,pts in st.get('curves',{}).items():
                if name in self._curve_editors:
                    self._curve_editors[name].set_points(pts,False)
                    self._preview.update_curve(name,pts)
            for kind,pts in st.get('hue_curves',{}).items():
                if kind in self._hue_editors:
                    self._hue_editors[kind].set_points(pts,False)
                    self._preview.update_hue_curve(kind,pts)
            warp=st.get('color_warp',[(0.0,1.0)]*6)
            self._color_warp.set_state(warp,False)
            self._preview.update_color_warp(warp)
            prim=st.get('primaries',{'red':{'hue':0,'purity':0},'green':{'hue':0,'purity':0},'blue':{'hue':0,'purity':0}})
            self._primaries_state={k:{'hue':float(prim.get(k,{}).get('hue',0)),'purity':float(prim.get(k,{}).get('purity',0))} for k in ('red','green','blue')}
            for k,vals in self._primaries_state.items():
                if hasattr(self,'_primary_controls'):
                    self._primary_controls[k]['puck'].set_values(vals['hue'],vals['purity'],False); self._primary_controls[k]['readout'].set_text(f"Hue {vals['hue']:+.0f}°   Purity {vals['purity']:+.0f}")
            self._preview.update_primaries(self._primaries_state)
            gc=st.get('global_colour',{'temperature':0,'tint':0,'chroma':0,'saturation':100})
            self._global_colour_state={'temperature':float(gc.get('temperature',0)),'tint':float(gc.get('tint',0)),'chroma':float(gc.get('chroma',0)),'saturation':float(gc.get('saturation',100))}
            if hasattr(self,'_global_colour_controls'):
                for k,v in self._global_colour_state.items(): self._global_colour_controls[k].set_value(v); self._global_colour_readouts[k].set_text(f'{v:.0f}')
            self._preview.update_global_colour(self._global_colour_state)
        finally:
            self._preview.end_batch()

    def _apply_builtin_preset(self,key):
        # Build the look from a clean state in one pass.  Do not call the UI reset
        # handlers here: those handlers debounce callbacks, which can otherwise fire
        # 80 ms later and silently overwrite the freshly selected preset.
        looks={
          'neutral':{},
          'warm':{'shadows':(-.03,0,.04),'midtones':(.05,.01,-.03),'highlights':(.08,.025,-.05),'master':(.02,.005,-.015),'saturation':1.04,'contrast':.16},
          'cool':{'shadows':(-.04,.01,.08),'midtones':(-.02,0,.04),'highlights':(.025,.01,-.015),'master':(-.015,0,.025),'saturation':.96,'contrast':.20},
          'soft':{'shadows':(.015,.005,.005),'midtones':(.025,.01,-.005),'highlights':(.04,.015,-.015),'saturation':.94,'contrast':-.10},
          'punchy':{'shadows':(-.025,0,.02),'midtones':(.015,0,-.01),'highlights':(.035,.01,-.02),'saturation':1.10,'contrast':.32},
          'matte':{'shadows':(.025,.018,.02),'midtones':(.01,.005,0),'highlights':(-.01,0,.015),'saturation':.88,'contrast':-.05},
          'clean':{'shadows':(-.008,0,.008),'midtones':(.008,.004,-.004),'highlights':(.015,.006,-.008),'saturation':1.02,'contrast':.10},
          'golden':{'shadows':(.01,.0,.01),'midtones':(.06,.025,-.035),'highlights':(.10,.045,-.06),'master':(.025,.01,-.018),'saturation':1.06,'contrast':.12},
          'teal':{'shadows':(-.055,.025,.065),'midtones':(-.01,.005,.012),'highlights':(.055,.022,-.035),'saturation':1.03,'contrast':.18},
          'highkey':{'shadows':(.02,.015,.015),'midtones':(.025,.015,.005),'highlights':(.035,.02,.005),'saturation':.94,'contrast':-.18},
          'lowkey':{'shadows':(-.025,-.005,.02),'midtones':(-.01,0,.008),'highlights':(.015,.005,-.005),'saturation':.96,'contrast':.28},
          'vintage':{'shadows':(.035,.025,.015),'midtones':(.025,.012,-.005),'highlights':(.055,.025,-.035),'saturation':.82,'contrast':-.10},
          'landscape':{'shadows':(-.018,.012,.025),'midtones':(.012,.018,-.005),'highlights':(.035,.018,-.025),'saturation':1.13,'contrast':.20}
        }
        d=looks.get(key,{})
        st={
            'format':'color-managed-grading-preset','version':1,
            'bands':{b:list(d.get(b,(0,0,0))) for b in BANDS},
            'master':list(d.get('master',(0,0,0))),
            'saturation':d.get('saturation',1.0),
            'contrast':d.get('contrast',0.0),'pivot':0.5,
            'curves':{n:_identity_points() for n in CURVE_CHANNELS},
            'hue_curves':{k:[(0.0,0.5),(1.0,0.5)] for k in ('hue','sat','lum')},
            'color_warp':[(0.0,1.0)]*6,
            'primaries':{'red':{'hue':0,'purity':0},'green':{'hue':0,'purity':0},'blue':{'hue':0,'purity':0}},
            'global_colour':{'temperature':0,'tint':0,'chroma':0,'saturation':100}
        }
        self._apply_grade_state(st)

    def _confirm_free_reset(self):
        # Internal neutralisation used by presets; unlike Reset All it does not prompt.
        super()._on_reset(None)
        for e in self._curve_editors.values(): e.reset()
        for e in self._hue_editors.values(): e.reset()
        self._color_warp.reset()

    def _export_preset(self,*_):
        d=Gtk.FileChooserDialog(title='Export Color Managed Grading preset',parent=self,action=Gtk.FileChooserAction.SAVE); d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Export',Gtk.ResponseType.OK); d.set_current_name('grading-preset.json'); d.set_do_overwrite_confirmation(True)
        if d.run()==Gtk.ResponseType.OK:
            try:
                with open(d.get_filename(),'w',encoding='utf-8') as f: json.dump(self._grade_state(),f,indent=2)
            except Exception as exc: Gimp.message('Preset export failed: '+str(exc))
        d.destroy()

    def _import_preset(self,*_):
        d=Gtk.FileChooserDialog(title='Import Color Managed Grading preset',parent=self,action=Gtk.FileChooserAction.OPEN); d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Import',Gtk.ResponseType.OK); flt=Gtk.FileFilter(); flt.set_name('Color Managed Grading presets (*.json)'); flt.add_pattern('*.json'); d.add_filter(flt)
        if d.run()==Gtk.ResponseType.OK:
            try:
                with open(d.get_filename(),'r',encoding='utf-8') as f: st=json.load(f)
                if st.get('format')!='color-managed-grading-preset': raise ValueError('Not a Color Managed Grading preset')
                self._apply_grade_state(st)
            except Exception as exc: Gimp.message('Preset import failed: '+str(exc))
        d.destroy()

    def _parse_cube(self,path):
        size=None; domain_min=(0.0,0.0,0.0); domain_max=(1.0,1.0,1.0); rows=[]
        with open(path,'r',encoding='utf-8-sig') as f:
            for raw in f:
                line=raw.strip()
                if not line or line.startswith('#'): continue
                up=line.upper()
                if up.startswith('TITLE '): continue
                if up.startswith('LUT_3D_SIZE '): size=int(line.split()[1]); continue
                if up.startswith('LUT_1D_SIZE '): raise ValueError('1D .cube LUTs are not supported by this grading stage yet.')
                if up.startswith('DOMAIN_MIN '): domain_min=tuple(map(float,line.split()[1:4])); continue
                if up.startswith('DOMAIN_MAX '): domain_max=tuple(map(float,line.split()[1:4])); continue
                parts=line.split()
                if len(parts)==3:
                    rows.append(tuple(map(float,parts)))
        if not size: raise ValueError('Missing LUT_3D_SIZE.')
        if len(rows)!=size**3: raise ValueError(f'Expected {size**3} RGB rows for a {size}³ LUT, found {len(rows)}.')
        return {'size':size,'domain_min':domain_min,'domain_max':domain_max,'rows':rows,'path':path}

    def _import_cube_lut(self,*_):
        d=Gtk.FileChooserDialog(title='Import .cube LUT',parent=self,action=Gtk.FileChooserAction.OPEN); d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Import',Gtk.ResponseType.OK)
        flt=Gtk.FileFilter(); flt.set_name('3D LUT (*.cube)'); flt.add_pattern('*.cube'); d.add_filter(flt)
        if d.run()==Gtk.ResponseType.OK:
            try:
                lut=self._parse_cube(d.get_filename()); self._loaded_cube=lut
                # Do not pretend the LUT is active: this build has no verified native
                # 3D-LUT drawable-filter operation.  Keep the validated LUT ready and
                # say so explicitly rather than silently changing colours incorrectly.
                self._lut_status.set_text(f"Validated {lut['size']}³ LUT: {d.get_filename()} — not applied: this GIMP runtime exposes no verified native 3D-LUT live-filter backend.")
            except Exception as exc: self._lut_status.set_text('LUT import failed: '+str(exc))
        d.destroy()

    def _export_cube_lut(self,*_):
        # A .cube must contain the *baked result* of every active stage.  Writing an
        # approximation of GIMP color-balance/hue-range behaviour would create a LUT
        # that does not match the live preview, so refuse rather than export bad colour.
        self._lut_status.set_text('Exact .cube export is unavailable in this runtime: the grade must be baked through the same GIMP/GEGL pipeline before LUT samples can be written. JSON preset export remains exact and editable.')

    def _curve_changed(self,_ed,pts,name): self._preview.update_curve(name,pts)
    def _color_warp_changed(self,_ed,nodes): self._preview.update_color_warp(nodes)
    def _hue_changed(self,_ed,pts,kind): self._preview.update_hue_curve(kind,pts)

    def _primary_changed(self,key,prop,value):
        if not hasattr(self,'_primaries_state'):
            self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        self._primaries_state[key][prop]=float(value); self._preview.update_primaries(self._primaries_state)

    def _global_colour_changed(self,key,value):
        if not hasattr(self,'_global_colour_state'):
            self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
        self._global_colour_state[key]=float(value); self._preview.update_global_colour(self._global_colour_state)

    def _confirm_colour_reset(self,*_):
        if not self._ask('Reset Colour?','Primaries and Global Colour will return to neutral.','Reset Colour'): return
        self._primaries_state={c:{'hue':0.0,'purity':0.0} for c in ('red','green','blue')}
        if hasattr(self,'_primary_controls'):
            for c in ('red','green','blue'):
                self._primary_controls[c]['puck'].set_values(0,0,False)
                self._primary_controls[c]['readout'].set_text('Hue 0°   Purity 0')
        self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
        if hasattr(self,'_global_colour_controls'):
            for k,v in self._global_colour_state.items():
                self._global_colour_controls[k].set_value(v)
                self._global_colour_readouts[k].set_text(f'{v:.0f}')
        self._preview.update_primaries(self._primaries_state)
        self._preview.update_global_colour(self._global_colour_state)

    def _confirm_primaries_reset(self,*_):
        if not self._ask('Reset Primaries?','Red, Green and Blue primary Hue/Purity controls will return to neutral.','Reset Primaries'): return
        self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        self._preview.begin_batch()
        try:
            for k in self._primaries_state:
                self._primary_controls[k]['puck'].set_values(0,0,False); self._primary_controls[k]['readout'].set_text('Hue 0°   Purity 0')
            self._preview.update_primaries(self._primaries_state)
        finally: self._preview.end_batch()

    def _confirm_global_colour_reset(self,*_):
        if not self._ask('Reset Global Colour?','Temperature, Tint, Chroma and Saturation will return to neutral.','Reset Global Colour'): return
        self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
        self._preview.begin_batch()
        try:
            for k,v in self._global_colour_state.items(): self._global_colour_controls[k].set_value(v); self._global_colour_readouts[k].set_text(f'{v:.0f}')
            self._preview.update_global_colour(self._global_colour_state)
        finally: self._preview.end_batch()

    def _apply_fx_continue(self,*_):
        if not getattr(self._preview,'_initialised',False): return
        self._flush_pending_preview_state()
        if not self._preview.has_active_grade():
            return
        self._preview.set_preview_enabled(True)
        committed=self._preview._preview_layer
        self._preview.commit(False)
        if committed is None: return
        self._drawable=committed
        self._preview=ManagedPreviewManager(self._image,committed)
        self._preview.initialise()
        if not self._preview._initialised:
            Gimp.message('The grade was committed, but a new live preview could not be created.')
            return
        self._apply_builtin_preset('neutral')
        self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
        self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
        self._preview.update_primaries(self._primaries_state); self._preview.update_global_colour(self._global_colour_state)
        self._global_preview.set_active(True)
        try: self._scope_display.data=None
        except Exception: pass

    def _ask(self,title,text,ok):
        d=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.WARNING,buttons=Gtk.ButtonsType.NONE,text=title); d.format_secondary_text(text); d.add_button('Cancel',Gtk.ResponseType.CANCEL); d.add_button(ok,Gtk.ResponseType.OK); r=d.run(); d.destroy(); return r==Gtk.ResponseType.OK

    def _confirm_curves_reset(self,*_):
        if self._ask('Reset Curves?','Luminance, RGB, Red, Green, Blue and Color Warp will return to neutral. Other grading tabs are retained.','Reset Curves'):
            for e in self._curve_editors.values(): e.reset()
            self._color_warp.reset()

    def _confirm_hue_reset(self,*_):
        if self._ask('Reset Hue Curves?','All Hue Curves will return to neutral. Other grading tabs are retained.','Reset Hue Curves'):
            for e in self._hue_editors.values(): e.reset()

    def _confirm_cm_reset(self,*_):
        if self._ask('Reset Colour Management?','Proof/simulation settings changed in this dialog will return to neutral. Creative grading is retained.','Reset Colour Management'):
            self._clear_proof_profile()
            self._bpc.set_active(True)

    def _confirm_global_reset(self,*_):
        if not self._ask('Reset the entire grade?','Wheels/Bars, tonal ranges, saturation, contrast, RGB/Luminance curves and Hue Curves will all return to neutral.','Reset All'): return
        super()._on_reset(None)
        for e in self._curve_editors.values(): e.reset()
        for e in self._hue_editors.values(): e.reset()
        self._color_warp.reset()
        if hasattr(self,'_primary_controls'):
            self._primaries_state={k:{'hue':0.0,'purity':0.0} for k in ('red','green','blue')}
            for k in self._primaries_state: self._primary_controls[k]['puck'].set_values(0,0,False); self._primary_controls[k]['readout'].set_text('Hue 0°   Purity 0')
            self._preview.update_primaries(self._primaries_state)
        if hasattr(self,'_global_colour_controls'):
            self._global_colour_state={'temperature':0.0,'tint':0.0,'chroma':0.0,'saturation':100.0}
            for k,v in self._global_colour_state.items(): self._global_colour_controls[k].set_value(v); self._global_colour_readouts[k].set_text(f'{v:.0f}')
            self._preview.update_global_colour(self._global_colour_state)

    def _clear_proof_profile(self,*_):
        try: self._image.set_simulation_profile(None)
        except Exception: pass
        self._proof_status.set_text('No proof profile selected')

    def _choose_proof_profile(self,*_):
        # Guard against accidental/re-entrant activation (seen with some GTK/WM setups).
        if getattr(self,'_proof_chooser_open',False): return
        self._proof_chooser_open=True
        d=None
        try:
            d=Gtk.FileChooserDialog(title='Choose ICC proof profile',parent=self,action=Gtk.FileChooserAction.OPEN)
            d.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Open',Gtk.ResponseType.OK)
            f=Gtk.FileFilter(); f.set_name('ICC profiles'); f.add_pattern('*.icc'); f.add_pattern('*.icm'); d.add_filter(f)
            if d.run()==Gtk.ResponseType.OK:
                path=d.get_filename()
                try:
                    from gi.repository import Gio
                    prof=Gimp.ColorProfile.new_from_file(Gio.File.new_for_path(path)); self._image.set_simulation_profile(prof); self._proof_status.set_text(path)
                except Exception as exc: self._proof_status.set_text('Could not load proof profile: '+str(exc))
        finally:
            if d is not None: d.destroy()
            self._proof_chooser_open=False

    def _apply_bpc(self,*_):
        try: self._image.set_simulation_bpc(self._bpc.get_active())
        except Exception: pass

    def _on_notebook_switch_page(self, notebook, page, page_num):
        # Scopes is page 5 after the combined Colour page. Refresh only on entry.
        if page_num == 5:
            GLib.idle_add(self._initial_scope_refresh)

    def _initial_scope_refresh(self):
        self._refresh_scope()
        return False

    def _refresh_scope(self,*_):
        try:
            src=getattr(self._preview,'_preview_layer',None) or self._drawable
            self._scope_display.refresh(src)
            self._scope_status.set_text('Live FX scopes — reduced rendered preview; measurement only.')
            self._scope_status.get_style_context().remove_class('cg-scope-error')
        except Exception as exc:
            msg='SCOPE ERROR — '+str(exc)
            self._scope_display.data=None; self._scope_display.error=msg; self._scope_display.queue_draw()
            self._scope_status.set_text(msg); self._scope_status.get_style_context().add_class('cg-scope-error')


# ─────────────────────────────────────────────────────────────────────────────
# GIMP plugin registration
# ─────────────────────────────────────────────────────────────────────────────

def _run(procedure, run_mode, image, drawables, config, data):
    GimpUi.init(PLUGIN_NAME)
    if not drawables:
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR,GLib.Error('No drawable selected'))
    dialog=ColorManagedGradingDialog(image,drawables[0])
    if dialog._ready:
        dialog.connect('destroy',lambda *_:Gtk.main_quit()); dialog.show_all(); Gtk.main()
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS,GLib.Error())


class ColorManagedGradingPlugin(Gimp.PlugIn):
    def do_set_i18n(self, procedure_name):
        # Explicitly disable gettext for this single-file plug-in; avoids GIMP's
        # missing locale/catalog warning without requiring an empty locale tree.
        return False

    def do_query_procedures(self): return [PLUGIN_NAME]

    def do_create_procedure(self,name):
        proc=Gimp.ImageProcedure.new(self,name,Gimp.PDBProcType.PLUGIN,_run,None)
        proc.set_image_types('RGB*'); proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
        proc.set_menu_label(MENU_LABEL); proc.add_menu_path('<Image>/Colors')
        proc.set_documentation('Colour-managed grading with synchronized Wheels/Bars, curves, hue curves, proofing and scopes','Non-destructive colour-managed grading built on GIMP native drawable filters',name)
        proc.set_attribution('Zarir Madon','Zarir Madon','2026'); return proc


Gimp.main(ColorManagedGradingPlugin.__gtype__,sys.argv)
