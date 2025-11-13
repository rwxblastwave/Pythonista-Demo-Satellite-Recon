# coding: utf-8
# Map Snapshot Studio — Compact Address (Address, City, ZIP, Country) — ZIP fix, no county leakage
# Optimized: snapshot/rotation/text caches + async geocoding
# Pythonista 3 (iPhone 14 Pro Max, portrait)

import ui, location, photos, dialogs, tempfile, os, time, math, console, requests, threading

# ===== Theme =====
BG_COLOR        = '#f2f2f7'
CARD_BG         = '#ffffff'
CARD_SHADOW     = (0, 0, 0, 0.08)
ACCENT_BLUE     = '#0a84ff'
ACCENT_GREEN    = '#34c759'
TEXT_PRIMARY    = '#111111'
TEXT_SECONDARY  = '#6c6c70'
ACTIVITY_STYLE  = getattr(ui, 'ACTIVITY_INDICATOR_STYLE_GRAY', getattr(ui, 'ACTIVITY_INDICATOR_STYLE_WHITE', 0))

# ===== Appearance =====
GRID_ALPHA       = 0.10
CHIP_ALPHA       = 0.06
CROSSHAIR_ALPHA  = 0.85
WHITE            = (1, 1, 1, 1)
RED              = (1, 0.2, 0.2, 1)
CAPTION_OFFSET_Y = 26

MAP_TYPES = ['standard', 'satellite', 'hybrid']

SCREEN_W, SCREEN_H = 430, 932
MIN_METERS, MAX_METERS = 150.0, 6000.0
DEFAULT_METERS = 800.0
COVERAGE_STEP = 50.0
ROT_STEP = 1.0

# ===== Fonts & text metrics =====
FONT_CHIP = ('<System>', 12)
FONT_CAPTION = ('<System>', 12)
FONT_SCALE = ('<System>', 13)
_, _chip_lh = ui.measure_string('Ag', font=FONT_CHIP)
CHIP_LINE_H = max(18, _chip_lh)

def _apply_card_style(view):
    view.bg_color = CARD_BG
    view.corner_radius = 18
    view.border_width = 0
    view.flex = 'W'
    view.shadow_color = CARD_SHADOW
    view.shadow_offset = (0, 8)
    view.shadow_radius = 16

def _apply_primary_button(btn):
    btn.font = ('<System-Bold>', 17)
    btn.background_color = ACCENT_BLUE
    btn.tint_color = 'white'
    btn.corner_radius = 10

def _apply_outline_button(btn, color):
    btn.font = ('<System-Semibold>', 16)
    btn.corner_radius = 10
    btn.border_width = 1
    btn.border_color = color
    btn.tint_color = color

# ===== Caches =====
_last_snap_key = None
_last_snap_img = None
_last_rot_src = None
_last_rot_deg = None
_last_rot_img = None
_geocode_cache = {}       # {(lat5,lon5): "compact two-line address"}
_measure_cache = {}       # {(font_name, size, text): (w,h)}

# ---------- Utilities ----------
def request_location(timeout=6.0, poll=0.5):
    location.start_updates()
    t0 = time.time()
    try:
        while time.time() - t0 < timeout:
            loc = location.get_location()
            if loc and 'latitude' in loc and 'longitude' in loc:
                return (float(loc['latitude']), float(loc['longitude']))
            time.sleep(poll)
        return None
    finally:
        location.stop_updates()

def meters_label(m):
    return f'{int(m)} m' if m < 1000 else f'{m/1000.0:.1f} km'

def nice_scale_length(mpp, max_px=140):
    for L in [25,50,100,200,250,500,1000,2000,2500,5000,10000,20000][::-1]:
        if L / mpp <= max_px:
            return L
    return 25

def cov_slider_to_meters(v):
    m = MIN_METERS + v * (MAX_METERS - MIN_METERS)
    return max(MIN_METERS, min(MAX_METERS, round(m / COVERAGE_STEP) * COVERAGE_STEP))

def meters_to_cov_slider(m):
    m = max(MIN_METERS, min(MAX_METERS, m))
    return (m - MIN_METERS) / (MAX_METERS - MIN_METERS)

def rot_slider_to_degrees(v):
    d = round((v * 360.0) / ROT_STEP) * ROT_STEP
    return max(0.0, min(360.0, d))

def degrees_to_rot_slider(d):
    return max(0.0, min(360.0, d)) / 360.0

# ---------- Fast measurement with cache ----------
def _measure(text, font):
    key = (font[0], font[1], text)
    m = _measure_cache.get(key)
    if m:
        return m
    w, h = ui.measure_string(text, font=font)
    _measure_cache[key] = (w, h)
    return w, h

# ---------- Imaging ----------
def rotate_image_fill_square(img, degrees):
    """Rotate around center on a square canvas; skip or reuse when possible."""
    global _last_rot_src, _last_rot_deg, _last_rot_img
    if abs(degrees) < 0.01:
        return img
    if _last_rot_src is img and _last_rot_deg == degrees and _last_rot_img is not None:
        return _last_rot_img
    w, h = img.size
    side = int(min(w, h))
    theta = math.radians(degrees)
    scale = abs(math.cos(theta)) + abs(math.sin(theta))
    with ui.ImageContext(side, side) as ctx:
        ui.concat_ctm(ui.Transform.translation(side/2.0, side/2.0))
        ui.concat_ctm(ui.Transform.rotation(theta))
        img.draw(-side*scale/2.0, -side*scale/2.0, side*scale, side*scale)
        out = ctx.get_image()
    _last_rot_src, _last_rot_deg, _last_rot_img = img, degrees, out
    return out

# ---------- Caption ----------
def draw_caption_bottom_right(w, h, caption_text):
    pad = 14
    cap_pad = 8
    text_w, text_h = _measure(caption_text, FONT_CAPTION)
    text_h = max(18, text_h)
    box_w, box_h = text_w + 2*cap_pad, text_h + 2*cap_pad
    bx, by = w - box_w - pad, h - box_h - pad - CAPTION_OFFSET_Y
    ui.set_color((0, 0, 0, CHIP_ALPHA))
    ui.Path.rounded_rect(bx, by, box_w, box_h, 8).fill()
    ui.draw_string(caption_text,
                   rect=(bx + cap_pad, by + cap_pad, text_w, text_h),
                   font=FONT_CAPTION, color=WHITE, alignment=ui.ALIGN_LEFT,
                   line_break_mode=ui.LB_TRUNCATE_TAIL)
    return (bx, by, box_w, box_h)

# ---------- Multiline address chip (wrapping) ----------
def _wrap_text_to_width(text, font, max_text_w):
    words = text.split()
    lines, cur = [], ''
    for w in words:
        trial = (cur + ' ' + w).strip()
        tw, _ = _measure(trial, font)
        if tw <= max_text_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            # Hard-break long words by binary search
            while True:
                tw, _ = _measure(w, font)
                if tw <= max_text_w:
                    break
                lo, hi = 1, len(w); fit = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    ttw, _ = _measure(w[:mid], font)
                    if ttw <= max_text_w:
                        fit = mid; lo = mid + 1
                    else:
                        hi = mid - 1
                lines.append(w[:fit]); w = w[fit:]
                if not w: break
            cur = w if w else ''
    if cur:
        lines.append(cur)
    return lines

def draw_address_top_left(w, h, addr_text):
    """Draw compact two-line address chip at top-left with wrapping."""
    pad = 14
    cap_pad = 8
    max_chip_w = min(int(w * 0.80), 320)
    max_text_w = max_chip_w - 2 * cap_pad

    paragraphs = [p.strip() for p in addr_text.split('\n') if p.strip()]
    lines = []
    for para in paragraphs:
        lines.extend(_wrap_text_to_width(para, FONT_CHIP, max_text_w))

    text_w = 0
    for line in lines:
        lw, _ = _measure(line, FONT_CHIP)
        text_w = max(text_w, min(lw, max_text_w))
    text_h = CHIP_LINE_H * max(1, len(lines))

    box_w, box_h = min(max_chip_w, text_w + 2*cap_pad), text_h + 2*cap_pad
    bx, by = pad, pad

    ui.set_color((0, 0, 0, CHIP_ALPHA))
    ui.Path.rounded_rect(bx, by, box_w, box_h, 8).fill()

    ui.set_color(WHITE)
    tx, ty = bx + cap_pad, by + cap_pad
    for line in lines:
        ui.draw_string(line, rect=(tx, ty, max_text_w, CHIP_LINE_H),
                       font=FONT_CHIP, color=WHITE, alignment=ui.ALIGN_LEFT,
                       line_break_mode=ui.LB_TRUNCATE_TAIL)
        ty += CHIP_LINE_H

    return (bx, by, box_w, box_h)

# ---------- Overlays ----------
def draw_overlays(base_img, meters, map_type, lat, lon,
                  rotation_deg=0.0, show_grid=True,
                  show_crosshair=True, grid_divisions=4, show_caption=True, full_addr=None):
    w, h = base_img.size
    mpp = meters / float(w)
    pad = 14
    with ui.ImageContext(w, h) as ctx:
        base_img.draw(0, 0, w, h)

        if full_addr:
            draw_address_top_left(w, h, full_addr)

        if show_grid and grid_divisions > 0:
            ui.set_color((1, 1, 1, GRID_ALPHA))
            for i in range(1, grid_divisions):
                x = i * (w / grid_divisions)
                p = ui.Path(); p.move_to(x, 0); p.line_to(x, h)
                p.line_width = 0.8; p.stroke()
            for i in range(1, grid_divisions):
                y = i * (h / grid_divisions)
                p = ui.Path(); p.move_to(0, y); p.line_to(w, y)
                p.line_width = 0.8; p.stroke()

        # Scale bar
        bar_len_m = nice_scale_length(mpp, max_px=min(180, int(w*0.4)))
        bar_len_px = bar_len_m / mpp
        y_bar = h - 20 - pad
        ui.set_color(WHITE)
        ui.Path.rect(pad*1.5, y_bar, bar_len_px, 6).fill()
        title = f'{int(bar_len_m)} m' if bar_len_m < 1000 else f'{bar_len_m/1000:.1f} km'
        ui.draw_string(title, rect=(pad*1.5, y_bar - 18, bar_len_px, 18),
                       font=FONT_SCALE, color=WHITE, alignment=ui.ALIGN_CENTER)

        # North arrow
        na_size = 44; na_x = w - na_size - pad; na_y = pad
        ui.set_color(WHITE)
        cx0, cy0, r = na_x + na_size/2.0, na_y + na_size/2.0, na_size*0.36
        tri = [(0, -r), (-r*0.72, r*0.72), (r*0.72, r*0.72)]
        th = math.radians(rotation_deg); ct, st = math.cos(th), math.sin(th)
        def rot(pt): return (pt[0]*ct - pt[1]*st + cx0, pt[0]*st + pt[1]*ct + cy0)
        p1, p2, p3 = map(rot, tri)
        p = ui.Path(); p.move_to(*p1); p.line_to(*p2); p.line_to(*p3); p.close(); p.fill()
        ui.draw_string('N', rect=(na_x, na_y + na_size - 18, na_size, 18),
                       font=('<System-Bold>', 14), color=RED, alignment=ui.ALIGN_CENTER)

        if show_caption:
            rot_txt = f' • Rot {rotation_deg:.0f}°' if abs(rotation_deg) >= 1.0 else ''
            caption = f'{map_type.capitalize()} • {meters_label(meters)} • Lat {lat:.5f}, Lon {lon:.5f}{rot_txt}'
            draw_caption_bottom_right(w, h, caption)

        if show_crosshair:
            cx, cy = w/2.0, h/2.0
            ui.set_color((1, 1, 1, CROSSHAIR_ALPHA))
            for (x1, y1, x2, y2) in [(cx-18, cy, cx+18, cy), (cx, cy-18, cx, cy+18)]:
                p = ui.Path(); p.move_to(x1, y1); p.line_to(x2, y2)
                p.line_width = 1.2; p.stroke()
            ui.Path.oval(cx-2, cy-2, 4, 4).fill()

        return ctx.get_image()

# ---------- Address formatting (Address, City, ZIP, Country ONLY) ----------
def _fmt_nonempty(parts, sep=' '):
    return sep.join([p for p in parts if p])

def _first_nonempty(*vals):
    for v in vals:
        if v:
            return v
    return None

def _format_compact_apple(d):
    """Apple placemark -> 'house+street' on first line, 'City ZIP, Country' on second."""
    # Street/house
    house = _first_nonempty(d.get('SubThoroughfare'), d.get('HouseNumber'))
    street = _first_nonempty(d.get('Thoroughfare'), d.get('Street'))
    addr_line = _fmt_nonempty([house, street])

    # City: prefer Locality/City; avoid county (SubAdministrativeArea) unless nothing else exists.
    city = _first_nonempty(
        d.get('Locality'),
        d.get('City'),
        d.get('SubLocality'),            # neighborhoods like Mitte (only if no City)
        None                             # explicit stop: don't auto-pick county/state below
    )
    if not city:
        # Last-resort fallback — if there's absolutely no city; still avoid showing county if possible
        city = _first_nonempty(d.get('AdministrativeArea')) or _first_nonempty(d.get('SubAdministrativeArea'))

    # ZIP: Apple may use PostalCode or legacy ZIP
    zipc = _first_nonempty(d.get('PostalCode'), d.get('ZIP'))

    country = d.get('Country')

    top = addr_line or ''
    city_zip = _fmt_nonempty([city, zipc]) if (city or zipc) else ''
    bottom = _fmt_nonempty([city_zip, country], sep=', ')
    text = (top + '\n' + bottom).strip()
    return text if text else None

def _format_compact_osm(data):
    """OSM -> same two-line format."""
    a = data.get('address') or {}
    # Address line
    house = a.get('house_number')
    road = _first_nonempty(a.get('road'), a.get('pedestrian'), a.get('footway'), a.get('path'))
    addr_line = _fmt_nonempty([house, road])

    # City
    city = _first_nonempty(a.get('city'), a.get('town'), a.get('village'), a.get('hamlet'),
                           a.get('municipality'), a.get('suburb'))

    # ZIP
    zipc = a.get('postcode')

    country = a.get('country')

    top = addr_line or ''
    city_zip = _fmt_nonempty([city, zipc]) if (city or zipc) else ''
    bottom = _fmt_nonempty([city_zip, country], sep=', ')
    text = (top + '\n' + bottom).strip()
    return text if text else None

# ---------- Geocoding (Apple first, OSM fallback) ----------
def reverse_geocode_compact(lat, lon):
    """Return compact two-line address string or None. Cached by rounded coords."""
    key = (round(lat, 5), round(lon, 5))
    if key in _geocode_cache:
        return _geocode_cache[key]
    # Apple
    try:
        arr = location.reverse_geocode({'latitude': lat, 'longitude': lon})
        if arr:
            txt = _format_compact_apple(arr[0]) or None
            if txt:
                _geocode_cache[key] = txt
                return txt
    except Exception:
        pass
    # OSM fallback
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        headers = {'User-Agent': 'MapSnapshotStudio/1.0 (contact: you@example.com)'}
        params = {'format': 'json', 'lat': lat, 'lon': lon, 'zoom': 18, 'addressdetails': 1}
        r = requests.get(url, headers=headers, params=params, timeout=6)
        if r.ok:
            data = r.json()
            txt = _format_compact_osm(data)
            if txt:
                _geocode_cache[key] = txt
                return txt
    except Exception:
        pass
    return None

# ---------- Snapshot (with cache) ----------
def get_snapshot(lat, lon, meters, map_type, img_w):
    global _last_snap_key, _last_snap_img, _last_rot_src, _last_rot_deg, _last_rot_img
    key = (round(lat, 5), round(lon, 5), int(meters), map_type, int(img_w))
    if key == _last_snap_key and _last_snap_img is not None:
        return _last_snap_img
    snap = location.render_map_snapshot(
        lat, lon,
        width=int(meters), height=int(meters),
        map_type=map_type,
        img_width=int(img_w), img_height=int(img_w)
    )
    _last_snap_key, _last_snap_img = key, snap
    _last_rot_src = None; _last_rot_deg = None; _last_rot_img = None
    return snap

# ---------- App ----------
class MapStudio(ui.View):
    def __init__(self):
        super().__init__(frame=(0,0,SCREEN_W,SCREEN_H), bg_color=BG_COLOR)
        self.name = 'Map Snapshot Studio'
        self.latlon = None
        self.meters = DEFAULT_METERS
        self.rotation = 0.0
        self.show_grid = True
        self.grid_divisions = 4
        self.show_crosshair = True
        self.show_caption = True
        self.last_tempfile = None
        self.current_map_type = MAP_TYPES[1]
        self.status_text = 'Ready to capture your first snapshot.'
        self._build()
        self._layout()

    def _build(self):
        self.hero_lbl = ui.Label(text='Satellite Recon Studio', alignment=0)
        self.hero_lbl.font = ('<System-Bold>', 26)
        self.hero_lbl.text_color = TEXT_PRIMARY

        self.subtitle_lbl = ui.Label(text='Capture beautiful, annotated Apple Maps imagery in seconds.', alignment=0)
        self.subtitle_lbl.font = ('<System>', 15)
        self.subtitle_lbl.text_color = TEXT_SECONDARY
        self.subtitle_lbl.number_of_lines = 2

        self.controls_card = ui.View()
        _apply_card_style(self.controls_card)

        self.preview_card = ui.View()
        _apply_card_style(self.preview_card)

        self.preview_title = ui.Label(text='Live Preview', alignment=0)
        self.preview_title.font = ('<System-Semibold>', 16)
        self.preview_title.text_color = TEXT_PRIMARY

        self.loc_btn = ui.Button(title='Use My Location', action=self.on_pick)
        _apply_outline_button(self.loc_btn, ACCENT_BLUE)

        self.reset_btn = ui.Button(title='Reset Controls', action=self.on_reset)
        _apply_outline_button(self.reset_btn, TEXT_SECONDARY)

        self.coord_lbl = ui.Label(text='No location yet', alignment=0, number_of_lines=2)
        self.coord_lbl.font = ('<System>',15); self.coord_lbl.text_color = TEXT_PRIMARY

        self.type_seg = ui.SegmentedControl(segments=['Standard','Satellite','Hybrid'])
        self.type_seg.selected_index = 1
        self.type_seg.action = self.on_type

        self.m_slider = ui.Slider(action=self.on_cov)
        self.m_slider.value = meters_to_cov_slider(self.meters)
        self.m_label = ui.Label(text=f'Coverage: {meters_label(self.meters)} × {meters_label(self.meters)}', alignment=1)
        self.m_label.font = ('<System>',13); self.m_label.text_color = TEXT_SECONDARY

        self.rot_slider = ui.Slider(action=self.on_rot)
        self.rot_slider.value = degrees_to_rot_slider(self.rotation)
        self.rot_label = ui.Label(text=f'Rotation: {self.rotation:.0f}°', alignment=1)
        self.rot_label.font = ('<System>',13); self.rot_label.text_color = TEXT_SECONDARY

        self.grid_switch = ui.Switch(value=True, action=self.on_toggle_grid)
        self.grid_lbl = ui.Label(text='Grid Overlay', alignment=0)
        self.grid_lbl.font = ('<System>',13); self.grid_lbl.text_color = TEXT_PRIMARY

        self.grid_seg = ui.SegmentedControl(segments=['2×2','3×3','4×4','5×5'], action=self.on_grid_divisions)
        self.grid_seg.selected_index = 2

        self.cross_switch = ui.Switch(value=True, action=self.on_toggle_crosshair)
        self.cross_lbl = ui.Label(text='Crosshair', alignment=0)
        self.cross_lbl.font = ('<System>',13); self.cross_lbl.text_color = TEXT_PRIMARY

        self.caption_switch = ui.Switch(value=True, action=self.on_toggle_caption)
        self.caption_lbl = ui.Label(text='Caption Box', alignment=0)
        self.caption_lbl.font = ('<System>',13); self.caption_lbl.text_color = TEXT_PRIMARY

        self.render_btn = ui.Button(title='Render Snapshot', action=self.on_render)
        _apply_primary_button(self.render_btn)

        self.save_btn = ui.Button(title='Save to Photos', action=self.on_save)
        self.save_btn.enabled = False
        _apply_outline_button(self.save_btn, ACCENT_BLUE)

        self.share_btn = ui.Button(title='Share...', action=self.on_share)
        self.share_btn.enabled = False
        _apply_outline_button(self.share_btn, ACCENT_GREEN)

        self.imgv = ui.ImageView(content_mode=ui.CONTENT_SCALE_ASPECT_FIT)
        self.imgv.bg_color = (0.95,0.95,0.95)
        self.imgv.corner_radius = 12
        self.imgv.flex = 'WH'

        self.preview_hint = ui.Label(text='Render a snapshot to see it here.', alignment=0)
        self.preview_hint.text_color = TEXT_SECONDARY
        self.preview_hint.font = ('<System>', 14)
        self.preview_hint.number_of_lines = 1

        self.status_lbl = ui.Label(text=self.status_text, alignment=0)
        self.status_lbl.text_color = TEXT_SECONDARY
        self.status_lbl.font = ('<System>', 13)
        self.status_lbl.number_of_lines = 2

        self.activity = ui.ActivityIndicator(style=ACTIVITY_STYLE)
        self.activity.hides_when_stopped = True

        self.controls_card.add_subview(self.loc_btn)
        self.controls_card.add_subview(self.reset_btn)
        self.controls_card.add_subview(self.coord_lbl)
        self.controls_card.add_subview(self.type_seg)
        self.controls_card.add_subview(self.m_slider)
        self.controls_card.add_subview(self.m_label)
        self.controls_card.add_subview(self.rot_slider)
        self.controls_card.add_subview(self.rot_label)
        self.controls_card.add_subview(self.grid_lbl)
        self.controls_card.add_subview(self.grid_switch)
        self.controls_card.add_subview(self.grid_seg)
        self.controls_card.add_subview(self.cross_lbl)
        self.controls_card.add_subview(self.cross_switch)
        self.controls_card.add_subview(self.caption_lbl)
        self.controls_card.add_subview(self.caption_switch)
        self.controls_card.add_subview(self.render_btn)
        self.controls_card.add_subview(self.save_btn)
        self.controls_card.add_subview(self.share_btn)

        self.preview_card.add_subview(self.preview_title)
        self.preview_card.add_subview(self.imgv)
        self.preview_card.add_subview(self.preview_hint)

        for v in (self.hero_lbl,self.subtitle_lbl,self.controls_card,self.preview_card,self.status_lbl,self.activity):
            self.add_subview(v)

        self._set_preview_image(None)

    def _layout(self):
        pad = 20
        width = self.width - 2*pad
        y = 32

        self.hero_lbl.frame = (pad, y, width, 32)
        y += 34
        self.subtitle_lbl.frame = (pad, y, width, 40)
        y += 46

        self.controls_card.frame = (pad, y, width, 10)
        inner = 18
        card_w = width - 2*inner
        cy = inner

        half = (card_w - 8) / 2
        self.loc_btn.frame = (inner, cy, half, 38)
        self.reset_btn.frame = (inner + half + 8, cy, half, 38)
        cy += 46

        self.coord_lbl.frame = (inner, cy, card_w, 40)
        cy += 46

        self.type_seg.frame = (inner, cy, card_w, 32)
        cy += 40

        self.m_slider.frame = (inner, cy, card_w, 28)
        cy += 28
        self.m_label.frame = (inner, cy, card_w, 20)
        cy += 28

        self.rot_slider.frame = (inner, cy, card_w, 28)
        cy += 28
        self.rot_label.frame = (inner, cy, card_w, 20)
        cy += 32

        self.grid_lbl.frame = (inner, cy, card_w*0.6, 26)
        self.grid_switch.frame = (inner + card_w - 64, cy, 64, 26)
        cy += 32
        self.grid_seg.frame = (inner, cy, card_w, 30)
        cy += 38

        self.cross_lbl.frame = (inner, cy, card_w*0.6, 26)
        self.cross_switch.frame = (inner + card_w - 64, cy, 64, 26)
        cy += 32

        self.caption_lbl.frame = (inner, cy, card_w*0.6, 26)
        self.caption_switch.frame = (inner + card_w - 64, cy, 64, 26)
        cy += 40

        self.render_btn.frame = (inner, cy, card_w, 44)
        cy += 52

        half = (card_w - 12) / 2
        self.save_btn.frame = (inner, cy, half, 40)
        self.share_btn.frame = (inner + half + 12, cy, half, 40)
        cy += 52

        self.controls_card.height = cy + inner
        y = self.controls_card.y + self.controls_card.height + 18

        self.preview_card.frame = (pad, y, width, 10)
        inner_p = 16
        self.preview_title.frame = (inner_p, inner_p, width - 2*inner_p, 22)
        img_size = width - 2*inner_p
        img_y = inner_p + 30
        max_img_height = max(220, min(img_size, self.height - (self.preview_card.y + 160)))
        img_size = min(img_size, max_img_height)
        self.imgv.frame = (inner_p, img_y, img_size, img_size)
        self.preview_hint.frame = (self.imgv.x, self.imgv.y + self.imgv.height/2 - 10, self.imgv.width, 20)
        self.preview_card.height = self.imgv.y + self.imgv.height + inner_p
        y = self.preview_card.y + self.preview_card.height + 12

        self.status_lbl.frame = (pad, y, width - 40, 40)
        self.activity.frame = (pad + width - 30, y + 6, 24, 24)

    def layout(self): self._layout()

    # ---------- Actions ----------
    def on_pick(self, s):
        loc = request_location()
        if loc:
            self.latlon = loc
            self.coord_lbl.text = f'Lat: {loc[0]:.6f}\nLon: {loc[1]:.6f}'
            self.set_status('Location lock acquired. Ready to render!')
        else:
            self.coord_lbl.text = 'Location unavailable'
            self.set_status('Unable to determine your current location.')

    def on_type(self, s):
        self.current_map_type = MAP_TYPES[self.type_seg.selected_index]

    def on_cov(self, s):
        self.meters = cov_slider_to_meters(s.value)
        self.m_label.text = f'Coverage: {meters_label(self.meters)} × {meters_label(self.meters)}'

    def on_rot(self, s):
        self.rotation = rot_slider_to_degrees(s.value)
        self.rot_label.text = f'Rotation: {self.rotation:.0f}°'

    def on_toggle_grid(self, s):
        self.show_grid = bool(s.value)

    def on_grid_divisions(self, s):
        self.grid_divisions = 2 + s.selected_index

    def on_toggle_crosshair(self, s):
        self.show_crosshair = bool(s.value)

    def on_toggle_caption(self, s):
        self.show_caption = bool(s.value)

    def on_reset(self, s):
        self.meters = DEFAULT_METERS
        self.m_slider.value = meters_to_cov_slider(self.meters)
        self.m_label.text = f'Coverage: {meters_label(self.meters)} × {meters_label(self.meters)}'
        self.rotation = 0.0
        self.rot_slider.value = degrees_to_rot_slider(self.rotation)
        self.rot_label.text = f'Rotation: {self.rotation:.0f}°'
        self.grid_switch.value = True; self.show_grid = True
        self.cross_switch.value = True; self.show_crosshair = True
        self.caption_switch.value = True; self.show_caption = True
        self.grid_seg.selected_index = 2; self.grid_divisions = 4
        self.type_seg.selected_index = 1; self.current_map_type = MAP_TYPES[1]
        self.latlon = None
        self.coord_lbl.text = 'No location yet'
        self.save_btn.enabled = False
        self.share_btn.enabled = False
        self.last_tempfile = None
        self.set_status('Controls reset. Tap “Use My Location” to start over.')
        self._set_preview_image(None)

    def _encode_temp(self, img):
        try:
            if self.last_tempfile and os.path.exists(self.last_tempfile):
                try: os.remove(self.last_tempfile)
                except Exception: pass
            data = img.to_png()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
            tmp.write(data); tmp.flush(); tmp.close()
            self.last_tempfile = tmp.name
            return tmp.name
        except Exception as e:
            dialogs.alert('Temp File Error', str(e), 'OK', hide_cancel_button=True)
            return None

    def _set_busy(self, busy=True, status=None):
        for v in (self.loc_btn, self.type_seg, self.m_slider, self.rot_slider, self.render_btn, self.save_btn, self.share_btn):
            v.enabled = not busy
        self.render_btn.title = 'Rendering...' if busy else 'Render Snapshot'
        if busy:
            self.activity.start()
        else:
            self.activity.stop()
        if status:
            self.set_status(status)

    def set_status(self, text):
        self.status_text = text
        self.status_lbl.text = text

    def _set_preview_image(self, img):
        self.imgv.image = img
        self.preview_hint.hidden = bool(img)

    def _compose_image(self, snap_img, addr_text=None):
        lat, lon = self.latlon
        rotated = rotate_image_fill_square(snap_img, self.rotation)
        return draw_overlays(rotated, int(self.meters), self.current_map_type, lat, lon,
                             rotation_deg=self.rotation,
                             show_grid=self.show_grid,
                             show_crosshair=self.show_crosshair,
                             grid_divisions=self.grid_divisions,
                             show_caption=self.show_caption,
                             full_addr=addr_text)

    @ui.in_background
    def on_render(self, s):
        if not self.latlon:
            dialogs.alert('Location Needed','Tap “Use My Location” first.','OK',hide_cancel_button=True)
            return
        self._set_busy(True, status='Rendering fresh imagery…')
        lat, lon = self.latlon
        meters = int(self.meters)
        img_w = int(max(256, round(self.imgv.width)))

        # 1) Show map quickly without address
        try:
            snap = get_snapshot(lat, lon, meters, self.current_map_type, img_w)
            quick_img = self._compose_image(snap, addr_text=None)
            self._set_preview_image(quick_img)
            self.save_btn.enabled = True
            self.share_btn.enabled = True
            self.set_status('Snapshot ready. Looking up address details…')
        except Exception as e:
            self._set_busy(False)
            dialogs.alert('Render Failed', str(e), 'OK', hide_cancel_button=True)
            return

        # 2) Fetch compact address on a worker thread; then redraw bubble only
        def _addr_worker():
            addr = reverse_geocode_compact(lat, lon)
            if addr:
                img = self._compose_image(snap, addr_text=addr)
                ui.delay(lambda: self._set_preview_image(img), 0.0)
                ui.delay(lambda: self.set_status('Address updated. Snapshot ready to export.'), 0.0)
            else:
                ui.delay(lambda: self.set_status('Snapshot ready. Address lookup unavailable.'), 0.0)
            ui.delay(lambda: self._set_busy(False), 0.0)

        threading.Thread(target=_addr_worker, daemon=True).start()

    def on_save(self, s):
        if not self.imgv.image:
            dialogs.alert('Nothing to Save','Render a snapshot first.','OK',hide_cancel_button=True); return
        path = self.last_tempfile or self._encode_temp(self.imgv.image)
        if path:
            photos.create_image_asset(path)
            dialogs.alert('Saved','Image saved to Photos.','OK',hide_cancel_button=True)

    def on_share(self, s):
        path = self.last_tempfile or (self._encode_temp(self.imgv.image) if self.imgv.image else None)
        if path: console.quicklook(path)
        else: dialogs.alert('Nothing to Share','Render a snapshot first.','OK',hide_cancel_button=True)

def main():
    MapStudio().present('fullscreen', hide_title_bar=False)

if __name__ == '__main__':
    main()
