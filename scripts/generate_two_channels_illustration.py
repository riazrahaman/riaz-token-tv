"""Generate high-resolution illustration comparing Channel 1 and Channel 2 for TokenTV."""
import os
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

WIDTH = 1600
HEIGHT = 1000

# Paths
ROOT = Path(__file__).resolve().parent.parent
COMBO_FACE_PATH = ROOT / "site" / "tokentv" / "assets" / "face-combo.png"
OUTPUT_SITE = ROOT / "site" / "tokentv" / "assets" / "two-channels-illustration.png"
OUTPUT_DOCS = ROOT / "docs" / "assets" / "two-channels-illustration.png"

# Fonts
FONT_DIR = ROOT / "token_tv" / "web"
F_MANROPE = str(FONT_DIR / "manrope.ttf")
F_ORBITRON = str(FONT_DIR / "orbitron.ttf")
F_PIXEL = str(FONT_DIR / "press-start-2p.ttf")

SYS_SF_BOLD = "/Library/Fonts/SF-Pro-Rounded-Bold.otf"
SYS_SF_MEDIUM = "/Library/Fonts/SF-Pro-Rounded-Medium.otf"
SYS_SF_TEXT = "/Library/Fonts/SF-Pro-Text-Semibold.otf"
SYS_MENLO = "/System/Library/Fonts/Menlo.ttc"

def get_font(path, size):
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.truetype(F_MANROPE, size)
    except Exception:
        return ImageFont.load_default()

font_title = get_font(SYS_SF_BOLD, 38)
font_subtitle = get_font(SYS_SF_MEDIUM, 18)
font_tag = get_font(F_ORBITRON, 12)

font_badge = get_font(SYS_SF_BOLD, 14)
font_unit_head = get_font(SYS_SF_BOLD, 23)
font_unit_sub = get_font(SYS_SF_MEDIUM, 15)

font_spec_label = get_font(SYS_SF_BOLD, 14)
font_spec_val = get_font(SYS_SF_MEDIUM, 14)

font_clock_big = get_font(F_ORBITRON, 54)
font_clock_sec = get_font(F_ORBITRON, 22)
font_date = get_font(SYS_SF_BOLD, 15)
font_weather = get_font(SYS_SF_MEDIUM, 16)
font_pixel_tiny = get_font(F_PIXEL, 9)

font_banner_title = get_font(SYS_SF_BOLD, 15)
font_banner_body = get_font(SYS_SF_MEDIUM, 14)
font_callout_title = get_font(SYS_SF_BOLD, 13)
font_callout_sub = get_font(SYS_SF_MEDIUM, 12)
font_slot_tag = get_font(SYS_SF_BOLD, 11)

def draw_rounded_rect(draw, bbox, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=width)

def draw_chassis(draw, center_x, center_y, width=410, height=410, radius=36):
    """Draw a realistic white retro mini TV cube chassis with soft shadow and bezel."""
    left = center_x - width // 2
    top = center_y - height // 2
    right = center_x + width // 2
    bottom = center_y + height // 2

    # Rubber feet (drawn clearly below the bottom chassis line)
    foot_w = 54
    foot_h = 16
    draw_rounded_rect(draw, [left + 46, bottom - 4, left + 46 + foot_w, bottom + foot_h], 6, fill=(35, 42, 56), outline=(20, 25, 35), width=1)
    draw_rounded_rect(draw, [right - 46 - foot_w, bottom - 4, right - 46, bottom + foot_h], 6, fill=(35, 42, 56), outline=(20, 25, 35), width=1)

    # Chassis soft drop shadow
    for offset in range(12, 0, -2):
        draw_rounded_rect(draw, [left - offset//2, top + offset//2, right + offset//2, bottom + offset], radius + 2,
                          fill=None, outline=(14, 18, 28))

    # White chassis body
    draw_rounded_rect(draw, [left, top, right, bottom], radius, fill=(245, 247, 250), outline=(210, 215, 225), width=2)

    # Top highlight sheen (gentle gloss)
    draw_rounded_rect(draw, [left + 18, top + 4, right - 18, top + 15], 5, fill=(255, 255, 255))

    # Subtle bottom bevel shading
    draw_rounded_rect(draw, [left + 24, bottom - 8, right - 24, bottom - 4], 2, fill=(218, 222, 232))

    # Screen Bezel (Dark metallic slate frame)
    bezel_inset = 40
    b_left = left + bezel_inset
    b_top = top + bezel_inset
    b_right = right - bezel_inset
    b_bottom = bottom - bezel_inset
    draw_rounded_rect(draw, [b_left, b_top, b_right, b_bottom], 18, fill=(18, 22, 30), outline=(10, 13, 18), width=3)

    # Inner active IPS display area (scaled 240x240 inside)
    scr_inset = 10
    s_left = b_left + scr_inset
    s_top = b_top + scr_inset
    s_right = b_right - scr_inset
    s_bottom = b_bottom - scr_inset
    draw_rounded_rect(draw, [s_left, s_top, s_right, s_bottom], 8, fill=(8, 12, 22), outline=(35, 45, 65), width=1)

    return (s_left, s_top, s_right, s_bottom)

def draw_spaceman_sprite(draw, cx, cy, scale=3):
    """Draw cute 8-bit pixel astronaut floating."""
    grid = [
        "....WWWWWW......",
        "...WWWWWWWW.....",
        "..WWGGGGGGWW....",
        "..WWGGGGGGWW....",
        "..WWGGGGGGWW....",
        "..WWWWWWWWWW....",
        ".SWWWWWWWWWWS...",
        ".SWWRWWWWWWWS...",
        ".SWWWWWWWWWWS...",
        "..WW..WW..WW....",
        "..WW..WW..WW....",
        "..WW..WW..WW....",
        "..WW......WW....",
        "..WW......WW....",
        ".WWW......WWW...",
        "................"
    ]
    color_map = {
        'W': (245, 248, 255),
        'G': (255, 195, 45),
        'S': (120, 135, 160),
        'R': (245, 75, 75),
        'C': (85, 225, 255),
    }
    ox = cx - (8 * scale)
    oy = cy - (8 * scale)
    for r, row in enumerate(grid):
        for c, char in enumerate(row):
            if char in color_map:
                px = ox + c * scale
                py = oy + r * scale
                draw.rectangle([px, py, px + scale - 1, py + scale - 1], fill=color_map[char])

def generate():
    img = Image.new("RGBA", (WIDTH, HEIGHT), (11, 15, 23, 255))

    # 1. Soft Ambient Backlights (rendered into a separate image and blurred)
    glow_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    # Emerald glow behind left unit
    glow_draw.ellipse([230, 240, 630, 640], fill=(16, 185, 129, 32))
    # Amber glow behind right unit
    glow_draw.ellipse([970, 240, 1370, 640], fill=(245, 158, 11, 28))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(60))
    img.paste(glow_layer, (0, 0), glow_layer)

    draw = ImageDraw.Draw(img)

    # 2. Blueprint Grid Background
    grid_color = (22, 30, 44)
    for x in range(0, WIDTH, 40):
        draw.line([(x, 0), (x, HEIGHT)], fill=grid_color, width=1)
    for y in range(0, HEIGHT, 40):
        draw.line([(0, y), (WIDTH, y)], fill=grid_color, width=1)

    # 3. Main Header
    tag_text = "FIRMWARE ARCHITECTURE  |  240x240 IPS DISPLAY"
    tag_w = draw.textlength(tag_text, font_tag)
    tag_x = (WIDTH - tag_w) // 2
    draw_rounded_rect(draw, [tag_x - 16, 26, tag_x + tag_w + 16, 52], 13, fill=(18, 26, 40), outline=(45, 65, 95), width=1)
    draw.text((tag_x, 33), tag_text, fill=(110, 185, 255), font=font_tag)

    title_text = "The Two Factory Channels & The Hidden GIF Slot"
    title_w = draw.textlength(title_text, font_title)
    draw.text(((WIDTH - title_w) // 2, 64), title_text, fill=(255, 255, 255), font=font_title)

    sub_text = "The ESP8266 vendor firmware is strictly split into two mutually exclusive operating modes."
    sub_w = draw.textlength(sub_text, font_subtitle)
    draw.text(((WIDTH - sub_w) // 2, 112), sub_text, fill=(148, 163, 184), font=font_subtitle)

    # 4. UNIT 1: Channel 1 (Photo Mode / TokenTV)
    cx1 = 430
    cy1 = 445

    # Badge 1
    b1_text = "CHANNEL 1  •  PHOTO MODE"
    b1_w = draw.textlength(b1_text, font_badge)
    draw_rounded_rect(draw, [cx1 - b1_w // 2 - 18, 150, cx1 + b1_w // 2 + 18, 180], 15, fill=(6, 78, 59), outline=(16, 185, 129), width=2)
    draw.text((cx1 - b1_w // 2, 157), b1_text, fill=(52, 211, 153), font=font_badge)

    head1 = "Full 240x240 TokenTV Canvas"
    head1_w = draw.textlength(head1, font_unit_head)
    draw.text((cx1 - head1_w // 2, 192), head1, fill=(255, 255, 255), font=font_unit_head)

    desc1 = "The host renders the entire IPS panel with multi-layer animation"
    desc1_w = draw.textlength(desc1, font_unit_sub)
    draw.text((cx1 - desc1_w // 2, 222), desc1, fill=(148, 163, 184), font=font_unit_sub)

    # Draw Chassis 1
    sx1, sy1, sx2, sy2 = draw_chassis(draw, cx1, cy1, width=410, height=410)
    sw = sx2 - sx1
    sh = sy2 - sy1

    # Screen 1 Content: Paste scaled face-combo.png
    if COMBO_FACE_PATH.exists():
        face_img = Image.open(COMBO_FACE_PATH).convert("RGBA")
        face_scaled = face_img.resize((sw, sh), Image.Resampling.NEAREST)
        img.paste(face_scaled, (sx1, sy1), face_scaled)
    else:
        draw.rectangle([sx1, sy1, sx2, sy2], fill=(8, 12, 22))

    # Subtle glossy top corner reflection
    draw.line([(sx1 + 4, sy1 + 4), (sx2 - 30, sy1 + 4)], fill=(255, 255, 255, 70), width=2)

    # Specifications under Unit 1
    specs1 = [
        ("Full Canvas:", "240x240 pixels (100% active screen)"),
        ("Render Location:", "Host computer via Python + Pillow"),
        ("Delivery:", "Pushed over Wi-Fi HTTP to LittleFS"),
        ("Features:", "AI quotas, live tickers, 8-bit mascots")
    ]
    u1_bottom = cy1 + 205 + 24
    for i, (k, v) in enumerate(specs1):
        yy = u1_bottom + i * 24
        draw.ellipse([cx1 - 185, yy + 6, cx1 - 177, yy + 14], fill=(52, 211, 153))
        draw.text((cx1 - 170, yy), k, fill=(52, 211, 153), font=font_spec_label)
        draw.text((cx1 - 45, yy), v, fill=(220, 228, 240), font=font_spec_val)

    # 5. UNIT 2: Channel 2 (Clock Mode / Factory Firmware)
    cx2 = 1170
    cy2 = 445

    # Badge 2
    b2_text = "CHANNEL 2  •  CLOCK MODE"
    b2_w = draw.textlength(b2_text, font_badge)
    draw_rounded_rect(draw, [cx2 - b2_w // 2 - 18, 150, cx2 + b2_w // 2 + 18, 180], 15, fill=(120, 53, 15), outline=(245, 158, 11), width=2)
    draw.text((cx2 - b2_w // 2, 157), b2_text, fill=(251, 191, 36), font=font_badge)

    head2 = "Factory Clock, Weather & 80x80 GIF"
    head2_w = draw.textlength(head2, font_unit_head)
    draw.text((cx2 - head2_w // 2, 192), head2, fill=(255, 255, 255), font=font_unit_head)

    desc2 = "Stock firmware local timekeeping with a tiny animated sticker slot"
    desc2_w = draw.textlength(desc2, font_unit_sub)
    draw.text((cx2 - desc2_w // 2, 222), desc2, fill=(148, 163, 184), font=font_unit_sub)

    # Draw Chassis 2
    sx1, sy1, sx2, sy2 = draw_chassis(draw, cx2, cy2, width=410, height=410)
    sw = sx2 - sx1
    sh = sy2 - sy1

    # Render Channel 2 Screen Content (Factory Firmware UI)
    draw.rectangle([sx1, sy1, sx2, sy2], fill=(10, 14, 24))

    # Top status bar
    draw.text((sx1 + 18, sy1 + 14), "NTP SYNC: OK", fill=(110, 130, 160), font=font_pixel_tiny)
    draw.text((sx2 - 95, sy1 + 14), "WIFI: [||||]", fill=(90, 175, 245), font=font_pixel_tiny)
    draw.line([(sx1 + 15, sy1 + 28), (sx2 - 15, sy1 + 28)], fill=(32, 44, 68), width=1)

    # Big Digital Clock
    draw.text((sx1 + 22, sy1 + 40), "10:42", fill=(245, 248, 255), font=font_clock_big)
    draw.text((sx1 + 215, sy1 + 46), "38", fill=(251, 191, 36), font=font_clock_sec)
    draw.text((sx1 + 215, sy1 + 72), "AM", fill=(148, 163, 184), font=font_clock_sec)

    # Date and Weather
    draw.text((sx1 + 22, sy1 + 114), "WEDNESDAY, OCT 15", fill=(148, 163, 184), font=font_date)
    draw.text((sx1 + 22, sy1 + 138), "72°F SUNNY  |  DUBAI", fill=(255, 214, 102), font=font_weather)

    # THE 80x80 GIF SLOT (Scaled: 80/240 of 308px = ~103px)
    slot_size = int(sw * (80.0 / 240.0))
    slot_x1 = sx1 + 20
    slot_y1 = sy2 - slot_size - 18
    slot_x2 = slot_x1 + slot_size
    slot_y2 = slot_y1 + slot_size

    # Background of 80x80 slot
    draw.rectangle([slot_x1, slot_y1, slot_x2, slot_y2], fill=(5, 8, 16))

    # Twinkling stars inside slot
    stars_pos = [(14, 16), (48, 14), (82, 28), (22, 68), (86, 78), (58, 88)]
    for px, py in stars_pos:
        draw.point((slot_x1 + px, slot_y1 + py), fill=(180, 215, 255))
        draw.point((slot_x1 + px + 1, slot_y1 + py), fill=(255, 255, 255))

    # Floating astronaut inside 80x80 slot
    draw_spaceman_sprite(draw, slot_x1 + slot_size // 2, slot_y1 + slot_size // 2, scale=3)

    # Glowing neon cyan dashed border for 80x80 slot
    border_color = (6, 182, 212)
    dash_len = 6
    for x in range(slot_x1, slot_x2, dash_len * 2):
        draw.line([(x, slot_y1), (min(x + dash_len, slot_x2), slot_y1)], fill=border_color, width=2)
        draw.line([(x, slot_y2), (min(x + dash_len, slot_x2), slot_y2)], fill=border_color, width=2)
    for y in range(slot_y1, slot_y2, dash_len * 2):
        draw.line([(slot_x1, y), (slot_x1, min(y + dash_len, slot_y2))], fill=border_color, width=2)
        draw.line([(slot_x2, y), (slot_x2, min(y + dash_len, slot_y2))], fill=border_color, width=2)

    # Corner brackets
    blen = 8
    draw.line([(slot_x1, slot_y1), (slot_x1 + blen, slot_y1)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x1, slot_y1), (slot_x1, slot_y1 + blen)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x2 - blen, slot_y1), (slot_x2, slot_y1)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x2, slot_y1), (slot_x2, slot_y1 + blen)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x1, slot_y2 - blen), (slot_x1, slot_y2)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x1, slot_y2), (slot_x1 + blen, slot_y2)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x2 - blen, slot_y2), (slot_x2, slot_y2)], fill=(255, 255, 255), width=2)
    draw.line([(slot_x2, slot_y2 - blen), (slot_x2, slot_y2)], fill=(255, 255, 255), width=2)

    # Small label tag on top of the slot
    draw_rounded_rect(draw, [slot_x1, slot_y1 - 18, slot_x1 + 84, slot_y1 - 3], 3, fill=(6, 182, 212))
    draw.text((slot_x1 + 8, slot_y1 - 17), "80×80 GIF", fill=(0, 24, 34), font=font_slot_tag)

    # External Callout box to the right of the 80x80 slot
    callout_box_x = slot_x2 + 20
    callout_box_y = slot_y1 + 10
    draw_rounded_rect(draw, [callout_box_x, callout_box_y, callout_box_x + 134, callout_box_y + 75], 8,
                      fill=(14, 24, 40), outline=(6, 182, 212), width=1)
    # Arrow pointer to the 80x80 slot
    draw.line([(callout_box_x, callout_box_y + 35), (slot_x2 + 4, callout_box_y + 35)], fill=(6, 182, 212), width=2)
    draw.polygon([(slot_x2 + 4, callout_box_y + 35), (slot_x2 + 10, callout_box_y + 31), (slot_x2 + 10, callout_box_y + 39)], fill=(6, 182, 212))

    draw.text((callout_box_x + 10, callout_box_y + 10), "HIDDEN SLOT", fill=(6, 182, 212), font=font_callout_title)
    draw.text((callout_box_x + 10, callout_box_y + 30), "Custom sticker", fill=(255, 255, 255), font=font_callout_sub)
    draw.text((callout_box_x + 10, callout_box_y + 48), "(Factory Spaceman)", fill=(148, 163, 184), font=font_callout_sub)

    # Glossy top reflection for screen 2
    draw.line([(sx1 + 4, sy1 + 4), (sx2 - 30, sy1 + 4)], fill=(255, 255, 255, 70), width=2)

    # Specifications under Unit 2
    specs2 = [
        ("GIF Slot Size:", "80x80 pixels (only ~11% screen surface)"),
        ("Render Location:", "ESP8266 stock firmware internal loop"),
        ("Features:", "Hardware NTP sync + OpenWeatherMap"),
        ("Limitation:", "Firmware cannot overlay GIF onto Channel 1")
    ]
    u2_bottom = cy2 + 205 + 24
    for i, (k, v) in enumerate(specs2):
        yy = u2_bottom + i * 24
        draw.ellipse([cx2 - 185, yy + 6, cx2 - 177, yy + 14], fill=(251, 191, 36))
        draw.text((cx2 - 170, yy), k, fill=(251, 191, 36), font=font_spec_label)
        draw.text((cx2 - 40, yy), v, fill=(220, 228, 240), font=font_spec_val)

    # 6. BOTTOM ARCHITECTURAL CARD: The RAM Wall & Solution
    banner_x1 = 70
    banner_y1 = 825
    banner_x2 = WIDTH - 70
    banner_y2 = 965

    # Dark glassmorphic container
    draw_rounded_rect(draw, [banner_x1, banner_y1, banner_x2, banner_y2], 16, fill=(16, 22, 34), outline=(38, 52, 74), width=2)

    # Left Column: The RAM Wall
    col1_x = banner_x1 + 32
    # Red badge
    draw_rounded_rect(draw, [col1_x, banner_y1 + 16, col1_x + 236, banner_y1 + 42], 6, fill=(127, 29, 29), outline=(239, 68, 68), width=1)
    draw.text((col1_x + 12, banner_y1 + 21), "THE RAM WALL · ~40 KB HEAP", fill=(254, 202, 202), font=font_badge)

    ram_desc = (
        "A 240x240 16-bit frame requires 115,200 bytes. With only ~40 KB of usable heap, the ESP8266\n"
        "cannot buffer or composite graphics layers in memory. The factory firmware strictly isolates\n"
        "Channel 1 (raw photo stream) from Channel 2 (built-in clock + 80x80 sticker). You cannot overlay them."
    )
    draw.text((col1_x, banner_y1 + 52), ram_desc, fill=(203, 213, 225), font=font_banner_body)

    # Vertical divider
    div_x = banner_x1 + 725
    draw.line([(div_x, banner_y1 + 18), (div_x, banner_y2 - 18)], fill=(38, 52, 74), width=1)

    # Right Column: The Solution
    col2_x = div_x + 32
    # Emerald badge
    draw_rounded_rect(draw, [col2_x, banner_y1 + 16, col2_x + 286, banner_y1 + 42], 6, fill=(6, 78, 59), outline=(16, 185, 129), width=1)
    draw.text((col2_x + 12, banner_y1 + 21), "TOKENTV HOST-SIDE COMPOSITING", fill=(167, 243, 208), font=font_badge)

    sol_desc = (
        "TokenTV completely bypasses the hardware RAM wall: Python on your computer pre-composites\n"
        "multi-provider AI quotas, financial tickers, starfields, and walking 8-bit mascots into finished\n"
        "240x240 animated GIF frames, streaming them straight to Channel 1 with zero clock lag."
    )
    draw.text((col2_x, banner_y1 + 52), sol_desc, fill=(203, 213, 225), font=font_banner_body)

    # Save to disk
    OUTPUT_SITE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DOCS.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT_SITE, "PNG")
    img.save(OUTPUT_DOCS, "PNG")
    print(f"Generated successfully: {OUTPUT_SITE}")

if __name__ == "__main__":
    generate()
