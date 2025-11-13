# 🛰 Pythonista Demo — Satellite Recon Map Snapshot Studio

**Repo:** `Pythonista-Demo-Satellite-Recon`  
**Script:** `satellite_recon.py`

> A compact “satellite recon” snapshot studio built with Pythonista 3 for iOS, turning Apple Maps snapshots into clean, annotated imagery with compact address chips, scale bar, north arrow, crosshair, and export tools.

![Satellite Recon Preview](SatelliteRecon_preview.png)

---

## 📖 Overview

This project is a self-contained Pythonista app that captures a square Apple Maps snapshot around your current GPS position, overlays recon-style information, and lets you export the final composite as a PNG to Photos or the iOS share sheet.

The app is tuned for **iPhone 14 Pro Max in portrait**, but the layout is simple enough to work well on other iPhones running Pythonista 3.

---

## ✨ Core Features

- **Map modes**
  - Standard, Satellite, and Hybrid map types via a segmented control.
- **Refined interface**
  - Full-height card layout with status messaging, live preview panel, and inline activity indicator.
  - Dedicated reset button to quickly return to pristine defaults.
  - Live status text keeps users informed about GPS, rendering, and geocoding progress.
- **Mobile-ready live preview**
  - Scrollable preview canvas with pinch-to-zoom, panning, and double-tap reset so you can inspect every pixel before exporting.
  - Clear hint text reminds you to interact just like any native iOS photo viewer.
- **Coverage control**
  - Adjustable square coverage from roughly **150 m up to 6 km**.
  - Coverage label shows both width and height using smart units (`123 m` or `1.2 km`).
- **Rotation control**
  - Smooth 0–360° rotation slider.
  - Snapshot is rotated into a square canvas so content doesn’t get clipped.
- **Visual overlays**
  - Optional **grid** (2×2 to 5×5, default 4×4) with light lines over the map.
  - **Scale bar** in meters/km in the lower-left, with auto-sized length and label.
  - **North arrow** in the upper-right, rotated to reflect the current rotation, with an “N” marker.
  - Toggleable **crosshair** at the exact center of the snapshot (lines + small dot).
  - Toggleable **caption box** in the bottom-right:
    - `MapType • Coverage • Lat • Lon • Rot°`
    - Example: `Satellite • 800 m × 800 m • Lat 30.33218, Lon -81.65565 • Rot 45°`
- **Compact address chip (top-left)**
  - Two-line address chip with soft rounded rectangle background:
    - Line 1: address / house number + street.
    - Line 2: city and ZIP, plus country when available.
  - Carefully formatted to **avoid county “noise”** and keep text compact.
  - Smart wrapping and truncation to keep the chip tidy.
- **Geocoding pipeline with caching**
  - **Apple reverse geocoding** via `location.reverse_geocode` as the primary source.
  - **OpenStreetMap / Nominatim** fallback via `requests`:
    - `https://nominatim.openstreetmap.org/reverse`
    - Custom User-Agent string (edit in the script with your own contact info).
  - Results cached by rounded coordinates `(lat5, lon5)` to reduce calls.
- **Snapshot & rotation caching**
  - Snapshot cache keyed by:
    - rounded latitude/longitude
    - coverage (meters)
    - map type
    - target image width
  - Rotation cache keyed by source image and rotation angle, so repeated rotations are cheap.
- **High-fidelity rendering**
  - Quality presets (Standard 1024 px, High 1536 px, Ultra 2048 px) keep exports razor sharp while balancing render time.
  - Controls are wired directly to `location.render_map_snapshot` per the Pythonista 3 documentation, so you always capture at the maximum supported resolution.
- **Asynchronous address fetch**
  - Snapshot renders immediately without blocking on geocoding.
  - Address lookup runs on a background thread; when it returns, the app recomposites with the address chip and updates the image on the UI thread.
- **Export tools**
  - **Save to Photos** via `photos.create_image_asset`.
  - **Share** the temp PNG using `console.quicklook`, invoking the iOS share sheet.

---

## 🧱 Project Structure

Single-file app plus docs:

```text
Pythonista-Demo-Satellite-Recon/
├── README.md
├── satellite_recon.py   # The Pythonista app
└── (optional) LICENSE   # Recommended: MIT or similar