# Pixel mascots

Brand-true 20×20 pixel logos drawn programmatically (Pillow, one pixel per cell) for the LCD renderer; the mascot() renderer scales them 1:1 then doubles to 40 px, so every cell in these files is a single square pixel on the clock — no resampling artifacts.

| Asset | Design |
|---|---|
| claude-pixel.png | Eight-ray sunburst (the Anthropic/Claude mark) — 2 px cardinal rays that reach the frame edge with flared tips, 6-cell diagonals, 2×2 hub. Shaded NW-light (#EEA06E) / base (#D97757) / SE-deep (#A35237). |
| ollama-pixel.png | Llama head profile — two ears with a notch, full-width fluffy face with muzzle, thin neck descending to the bottom tint (ear notch shading and lower rim in #C9CDD3, black eye pixel). |
| antigravity-pixel.png | Sparkle pair — dominant white four-point star (centre-left, #F5F7FA with #9AA7B6 lower-right rim and a light-blue #8AB4F8 lower tip) plus a deep-blue #1A73E8 companion diamond at the top right. |

Bounded to exactly 20×20 with edge-touching content, so mascot()'s box-crop → scale path performs no interpolation. (Earlier versions were image_gen- or hand-drawn 32×32 sprites that lost pixels when the renderer shrank them to 20.)

## Animation phases

The usage GIF's gauge shimmer walks four frames; the mascots ride along. `mascot(..., phase=k)` loads `<provider>-pixel-k.png` (base sprite when k=0 or the file is missing), so each provider also ships `-pixel-1.png` … `-pixel-3.png` — same 20×20 full-bleed bbox, small per-frame deltas derived from the base:

| Provider | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| claude-pixel-N | the light/deep shading quarter-turns clockwise | …another quarter | …another (full orbit per 1.2 s cycle) |
| ollama-pixel-N | wool flecks lighten on the neck | eye closes (blink) | flecks lighten again |
| antigravity-pixel-N | star's left edge pinches in, tips dim, diamond's outer ring drops | rims glint, diamond ring tints light blue | right edge pinches in, top tip dims, ring wobbles back |

Every phase keeps the exact base bounding box (a pinched box would re-introduce interpolation), verified in the generator.

To redraw: the generator matrices are pixel grids of palette letters ('.' = transparent); keep the full-bleed 20×20 bounding box so the 1:1 scale guarantee holds, and keep per-phase deltas inside it.