# Pixel mascots

User-provided references were adapted with the image_gen tool to transparent pixel mascots. The supplied references are treated as Claude (orange Clawd), Codex (purple terminal blossom), and Antigravity (a Gemini twin-star sparkle); this mapping is a project design assumption.

Selected generated outputs were cropped to their visible bounds and resized with nearest-neighbor sampling to 32×32 PNGs for the LCD renderer. Claude uses the second generated variant, which removes the first variant’s glow. Codex retains discrete purple shades. Antigravity is drawn to the same grid as one white four-point sparkle with a smaller Google-blue companion star, so it needs no light backing tile.

| Asset | Generation |
|---|---|
| claude-pixel.png | exec-23c46822-b44a-4aa3-87b5-577912a8f79d.png |
| codex-pixel.png | exec-494a638e-5e74-415d-ad92-829e7c1d59f5.png |
| antigravity-pixel.png | drawn to the 32×32 mascot spec (white + Google-blue twin sparkle) |

## Generation instructions

The image_gen requests preserve the reference silhouette, use a logical 32×32 pixel grid, hard edges, transparent background, a few original colors, and no text or extra elements.

Final Claude prompt:

> Make this exact orange pixel crab into a crisp flat game UI sprite. Use only ONE opaque solid peach-orange color (#D9845E) and opaque black rectangular eyes. Identical stepped silhouette, two side arms and four short square legs. This is a flat 32 by 32 logical pixel sprite, enlarged with nearest-neighbor square pixels. Transparent empty canvas. Absolutely NO glow, NO light, NO smoke, NO shadows, NO texture, NO gradients, NO highlights, NO blur, NO scene or background, NO white outline. Center the icon with a little transparent margin. Preserve the original simple pixel geometry. Pure solid opaque fills and hard right-angle edges. Output just the sprite.
