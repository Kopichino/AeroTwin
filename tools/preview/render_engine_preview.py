#!/usr/bin/env python3
"""Render a static HTML preview of the engine detail view.

Reads captured API snapshots and produces a self-contained page. The SVG scene
mirrors the React Three Fiber layout and colour ramp exactly -- same module
extents, same green/amber/red interpolation -- so the preview shows what the
real view shows rather than an approximation of it.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Mirrors MODULE_EXTENTS in apps/web/three/turbofan.tsx.
EXTENTS = [
    ("FAN", "Fan", 0.34, 1.5, 1.5, 0.0),
    ("LPC", "LP Compressor", 0.7, 0.95, 0.8, 0.14),
    ("HPC", "HP Compressor", 0.85, 0.8, 0.55, 0.06),
    ("COMBUSTOR", "Combustor", 0.4, 0.6, 0.62, 0.06),
    ("HPT", "HP Turbine", 0.35, 0.66, 0.74, 0.06),
    ("LPT", "LP Turbine", 0.5, 0.8, 0.95, 0.06),
    ("NOZZLE", "Nozzle", 0.45, 0.9, 0.62, 0.06),
]

BAND_VARS = {
    "HEALTHY": "--health-good",
    "WATCH": "--health-watch",
    "WARNING": "--health-warn",
    "CRITICAL": "--health-crit",
}


def build_layout() -> list[tuple[str, str, float, float, float, float]]:
    total = (
        sum(
            length * 2 + gap
            for *_, length, _, _, gap in [(a, b, c, d, e, f) for a, b, c, d, e, f in EXTENTS]
        )
        - EXTENTS[0][5]
    )
    cursor = -total / 2
    layout = []
    for index, (mid, label, length, rf, rb, gap) in enumerate(EXTENTS):
        if index > 0:
            cursor += gap
        layout.append((mid, label, cursor + length, length, rf, rb))
        cursor += length * 2
    return layout


def health_colour(score: float) -> str:
    """Green -> amber -> red ramp, identical to `healthColour` in turbofan.tsx."""
    critical, warning, healthy = (255, 77, 77), (245, 185, 66), (34, 201, 138)
    t = max(0.0, min(100.0, score)) / 100.0

    def lerp(a, b, u):
        return tuple(a[i] + (b[i] - a[i]) * u for i in range(3))

    rgb = lerp(critical, warning, t / 0.5) if t < 0.5 else lerp(warning, healthy, (t - 0.5) / 0.5)
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


def render_scene(components: dict[str, float], anomaly: str | None) -> tuple[str, int, int]:
    layout = build_layout()
    width, height = 460, 330
    min_x = layout[0][2] - layout[0][3] - 0.4
    max_x = layout[-1][2] + layout[-1][3] + 0.4
    sx = lambda x: (x - min_x) / (max_x - min_x) * width  # noqa: E731
    sr = lambda r: r / 1.7 * (height / 3.0)  # noqa: E731
    cy = height * 0.52

    parts = [
        f'<line x1="0" x2="{width}" y1="{cy}" y2="{cy}" '
        f'stroke="rgba(255,255,255,.08)" stroke-dasharray="4 4"/>',
        f'<path d="M{sx(layout[0][2] - 0.4)},{cy - sr(1.62)} '
        f"L{sx(layout[-1][2])},{cy - sr(1.35)} L{sx(layout[-1][2])},{cy + sr(1.35)} "
        f'L{sx(layout[0][2] - 0.4)},{cy + sr(1.62)} Z" '
        f'fill="#8fa3c4" fill-opacity="0.07" stroke="#8fa3c4" stroke-opacity="0.22"/>',
    ]

    for mid, _label, x, length, rf, rb in layout:
        score = components.get(mid, 100.0)
        colour = health_colour(score)
        x1, x2 = sx(x - length), sx(x + length)
        r1, r2 = sr(rf), sr(rb)
        opacity = 0.30 if score >= 60 else 0.55
        parts.append(
            f'<polygon points="{x1},{cy - r1} {x2},{cy - r2} {x2},{cy + r2} {x1},{cy + r1}" '
            f'fill="{colour}" fill-opacity="{opacity}" stroke="{colour}" stroke-width="1.4"/>'
        )
        parts.append(
            f'<ellipse cx="{x2}" cy="{cy}" rx="{max(2, (x2 - x1) * 0.10):.1f}" ry="{r2}" '
            f'fill="{colour}" fill-opacity="0.22" stroke="{colour}" stroke-opacity="0.5"/>'
        )
        parts.append(
            f'<text x="{(x1 + x2) / 2}" y="{cy + r2 + 15}" text-anchor="middle" '
            f'fill="var(--text-tertiary)" style="font:9px \'JetBrains Mono\',monospace">{mid}</text>'
        )
        parts.append(
            f'<text x="{(x1 + x2) / 2}" y="{cy - r1 - 7}" text-anchor="middle" fill="{colour}" '
            f"style=\"font:10px 'JetBrains Mono',monospace\">{score:.0f}</text>"
        )
        if mid == anomaly:
            cx = (x1 + x2) / 2
            parts.append(f'<circle cx="{cx}" cy="{cy - r1 - 24}" r="6" fill="#a855f7"/>')
            parts.append(
                f'<circle cx="{cx}" cy="{cy - r1 - 24}" r="11" fill="none" '
                f'stroke="#a855f7" stroke-opacity="0.5"/>'
            )

    fan_x, fan_r = sx(layout[0][2]), sr(1.05)
    for index in range(18):
        angle = index / 18 * 2 * math.pi
        parts.append(
            f'<line x1="{fan_x}" y1="{cy}" x2="{fan_x + math.cos(angle) * 6:.1f}" '
            f'y2="{cy + math.sin(angle) * fan_r:.1f}" stroke="#c9d2e0" '
            f'stroke-opacity="0.75" stroke-width="2"/>'
        )
    parts.append(f'<circle cx="{fan_x}" cy="{cy}" r="{sr(0.28)}" fill="#8b94a6"/>')

    return "".join(parts), width, height


def render_chart(points, colour, band=None, height=150, y_domain=None) -> str:
    if len(points) < 2:
        return ""
    width = 720
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    all_y = list(ys) + ([v for t in (band or []) for v in t[1:]] if band else [])
    low, high = y_domain if y_domain else (min(all_y), max(all_y))
    if low == high:
        high = low + 1
    pad = 0 if y_domain else (high - low) * 0.05
    low -= pad
    high += pad

    px = lambda x: (x - min(xs)) / ((max(xs) - min(xs)) or 1) * width  # noqa: E731
    py = lambda y: height - (y - low) / ((high - low) or 1) * height  # noqa: E731

    d = " ".join(
        f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y):.1f}" for i, (x, y) in enumerate(points)
    )
    band_path = ""
    if band:
        up = " ".join(
            f"{'M' if i == 0 else 'L'}{px(c):.1f},{py(u):.1f}" for i, (c, _l, u) in enumerate(band)
        )
        down = " ".join(f"L{px(c):.1f},{py(l):.1f}" for c, l, _u in reversed(band))
        band_path = f'<path d="{up} {down} Z" fill="{colour}" opacity="0.14"/>'

    grid = "".join(
        f'<line x1="0" x2="{width}" y1="{f * height}" y2="{f * height}" '
        f'stroke="rgba(255,255,255,.05)"/>'
        for f in (0, 0.25, 0.5, 0.75, 1)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'style="width:100%;height:{height}px">{grid}{band_path}'
        f'<path d="{d} L{width},{height} L0,{height} Z" fill="{colour}" opacity="0.12"/>'
        f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.75" '
        f'vector-effect="non-scaling-stroke"/></svg>'
        f'<div class="axis"><span>{low:.0f}</span>'
        f"<span>cycle {min(xs)}–{max(xs)}</span><span>{high:.0f}</span></div>"
    )


def main() -> int:
    detail = json.loads(Path("/tmp/m8_detail.json").read_text())
    history = json.loads(Path("/tmp/m8_history.json").read_text())["samples"]
    explanation = json.loads(Path("/tmp/m8_explain.json").read_text())

    components = detail["components"]
    anomaly = (detail.get("anomaly") or {}).get("module")
    scene, scene_w, scene_h = render_scene(components, anomaly)

    css = re.sub(r"@tailwind [^;]+;", "", (REPO / "apps/web/app/globals.css").read_text())
    css = re.sub(r"@layer \w+ \{", "", css).replace(
        "\n}\n\n/* Every animation", "\n/* Every animation"
    )

    layout_order = [entry[0] for entry in build_layout()]
    health = detail["health_index"]
    band = detail["health_band"]
    colour = f"var({BAND_VARS[band]})"

    rul = [(s["cycle"], s["rul_p50"]) for s in history if s["rul_p50"] is not None]
    rul_band = [
        (s["cycle"], s["rul_p10"], s["rul_p90"]) for s in history if s["rul_p10"] is not None
    ]
    health_points = [(s["cycle"], s["health_index"]) for s in history]

    attributions = explanation.get("attributions", [])[:7]
    peak = max((a["value"] for a in attributions), default=1) or 1
    attribution_html = "".join(
        f'<li><span class="an">{a["name"]}</span><span class="abar">'
        f'<i style="width:{a["value"] / peak * 100:.0f}%;'
        f'background:{"var(--health-warn)" if a["direction"] == "up" else "var(--accent)"}"></i>'
        f'</span><span class="ad">{"↑" if a["direction"] == "up" else "↓"}</span>'
        f'<span class="am">{a["module"]}</span></li>'
        for a in attributions
    )

    chips = "".join(
        f'<button class="chip{" on" if mid == detail["worst_module"] else ""}" '
        f'style="color:{health_colour(components[mid])}">{mid} {components[mid]:.0f}</button>'
        for mid in layout_order
        if mid in components
    )

    failure = "".join(
        f'<div class="fp"><div class="fpl">within {h} cycles</div><div class="fpv" '
        f'style="color:{"var(--health-crit)" if p > 0.5 else "var(--health-warn)" if p > 0.2 else "var(--health-good)"}">'
        f"{p * 100:.0f}%</div></div>"
        for h, p in sorted(detail.get("failure_prob", {}).items(), key=lambda kv: int(kv[0]))
    )

    styles = (REPO / "tools/preview/preview.css").read_text()
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AeroTwin — {detail["tail_number"]} · 3D digital twin</title>
<style>{css}{styles}</style></head><body><div class="wrap">
<a class="back" href="#">← Back to fleet</a>
<div class="panel hdr">
<div><h1>{detail["tail_number"]}</h1><div class="sub">{detail["external_ref"]} · cycle {detail["cycle"]} / {detail["total_cycles"]}</div></div>
<div style="display:flex;align-items:center;gap:12px">
<span class="pill" style="color:{colour};background:color-mix(in srgb,{colour} 12%,transparent)"><span class="sw" style="background:{colour}"></span>{band}</span>
<span style="font:600 18px 'JetBrains Mono',monospace;color:{colour}">{health:.1f}</span>
<span class="hbar"><i style="width:{max(2, health):.0f}%;background:{colour}"></i></span></div>
<div class="metrics">
<div><div class="l">Remaining life</div><div class="v">{round(detail["rul_p50"])} cycles</div></div>
<div><div class="l">80% interval</div><div class="v" style="color:var(--text-secondary)">{round(detail["rul_p10"])}–{round(detail["rul_p90"])}</div></div>
<div><div class="l">Worst module</div><div class="v">{detail["worst_module"]}</div></div></div></div>
<div class="grid">
<section class="panel" style="padding:16px">
<div class="ph"><h2>Digital twin</h2><span class="rpm">fan {detail["sensors"].get("s8", 0):.0f} rpm</span></div>
<div class="tools"><button class="tbtn">Exploded (E)</button><button class="tbtn">X-ray (X)</button></div>
<div class="canvas"><svg viewBox="0 0 {scene_w} {scene_h}" style="width:100%;height:{scene_h}px">{scene}</svg>
<span class="hint">drag to orbit · scroll to zoom · E exploded · X x-ray</span></div>
<div class="chips">{chips}</div>
<p class="note">Module colour is component health on a continuous green→amber→red ramp,
derived from thermodynamic efficiency and flow-capacity proxies measured against this
engine's own healthy baseline. The fan turns at the physical fan speed; a critical module
pulses; the purple marker is an active anomaly.</p></section>
<section class="panel" style="overflow:hidden">
<div class="tabs"><div class="tab">Overview</div><div class="tab">Sensors</div>
<div class="tab on">Prediction &amp; XAI</div><div class="tab">Components</div></div>
<div class="body">
<div class="block"><div class="bt"><h3>Remaining useful life</h3><span>shaded band is the conformal 80% interval</span></div>{render_chart(rul, "var(--accent)", rul_band)}</div>
<div class="block"><h3 style="font-size:12px;font-weight:600;margin-bottom:8px">Why this prediction</h3><ul class="attr">{attribution_html}</ul></div>
<div class="block"><h3 style="font-size:12px;font-weight:600">Failure probability</h3><div class="fps">{failure}</div></div>
<div class="block"><div class="bt"><h3>Health index</h3><span>0–100, EWMA-smoothed</span></div>{render_chart(health_points, colour, None, 130, (0, 100))}</div>
</div></section></div></div></body></html>"""

    output = REPO / "docs/preview/engine-3d-preview.html"
    output.write_text(html)
    print(
        f"wrote {output.relative_to(REPO)}: {detail['tail_number']} "
        f"HI={health:.1f} {band} worst={detail['worst_module']} anomaly={anomaly}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
