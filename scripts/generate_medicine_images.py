#!/usr/bin/env python3
"""
Generate realistic pill/capsule/inhaler/injection PNG images for all medicines
in the MediBot medicine database. Uses only PIL/Pillow — no other dependencies.

Output: 200x200 px images saved to assets/medicines/<medicine_id>.png
Run from the workspace root:  python3 scripts/generate_medicine_images.py
"""

import os
import math
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Output directory — resolve relative to this script's location
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(WORKSPACE_DIR, "assets", "medicines")
SIZE = (200, 200)
WHITE_BG = (255, 255, 255, 255)


def hex_to_rgb(h: str) -> tuple:
    """Convert '#RRGGBB' to (R, G, B)."""
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def darken(rgb: tuple, factor: float = 0.65) -> tuple:
    return tuple(max(0, int(c * factor)) for c in rgb)


def lighten(rgb: tuple, factor: float = 1.35) -> tuple:
    return tuple(min(255, int(c * factor)) for c in rgb)


def add_label(draw: ImageDraw.Draw, text: str, cx: int, cy: int,
              font_size: int = 12, color: tuple = (50, 50, 50)):
    """Draw centred text at (cx, cy). Falls back to default font if no TTF available."""
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text((cx - w // 2, cy - h // 2), text, fill=color, font=font)


# ---------------------------------------------------------------------------
# Shape drawing helpers
# ---------------------------------------------------------------------------

def draw_oval_pill(draw: ImageDraw.Draw, color_hex: str, label: str,
                   score: bool = True, cx: int = 100, cy: int = 100,
                   rx: int = 70, ry: int = 38):
    """Generic oval pill with optional score line."""
    rgb = hex_to_rgb(color_hex)
    dark = darken(rgb)
    outline = (100, 100, 100)

    # Shadow
    draw.ellipse([cx - rx + 4, cy - ry + 4, cx + rx + 4, cy + ry + 4],
                 fill=(200, 200, 200, 180))
    # Pill body
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                 fill=rgb + (255,), outline=outline, width=2)
    # Highlight
    draw.ellipse([cx - rx + 8, cy - ry + 6, cx + 20, cy - 4],
                 fill=lighten(rgb) + (120,))
    # Score line
    if score:
        draw.line([(cx, cy - ry + 6), (cx, cy + ry - 6)],
                  fill=dark + (200,), width=2)
    # Label
    text_color = (30, 30, 30) if sum(rgb) > 400 else (240, 240, 240)
    add_label(draw, label, cx, cy, font_size=10, color=text_color)


def draw_round_tablet(draw: ImageDraw.Draw, color_hex: str, label: str,
                      score: bool = True, cx: int = 100, cy: int = 100,
                      r: int = 55):
    """Round tablet with optional cross-score."""
    rgb = hex_to_rgb(color_hex)
    dark = darken(rgb)
    outline = (100, 100, 100)

    # Shadow
    draw.ellipse([cx - r + 4, cy - r + 4, cx + r + 4, cy + r + 4],
                 fill=(200, 200, 200, 180))
    # Body
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 fill=rgb + (255,), outline=outline, width=2)
    # Highlight
    draw.ellipse([cx - r + 8, cy - r + 8, cx + 5, cy - 5],
                 fill=lighten(rgb) + (130,))
    if score:
        draw.line([(cx - r + 8, cy), (cx + r - 8, cy)],
                  fill=dark + (200,), width=2)
    text_color = (30, 30, 30) if sum(rgb) > 400 else (240, 240, 240)
    add_label(draw, label, cx, cy + 18, font_size=9, color=text_color)


def draw_capsule(draw: ImageDraw.Draw, color1_hex: str, color2_hex: str,
                 label: str, cx: int = 100, cy: int = 100,
                 rx: int = 68, ry: int = 30):
    """Two-colour capsule (left half + right half)."""
    rgb1 = hex_to_rgb(color1_hex)
    rgb2 = hex_to_rgb(color2_hex)
    outline = (90, 90, 90)

    # Shadow
    draw.ellipse([cx - rx + 4, cy - ry + 4, cx + rx + 4, cy + ry + 4],
                 fill=(200, 200, 200, 180))

    # Right half (color2)
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                 fill=rgb2 + (255,), outline=outline, width=2)
    # Left half (color1) — draw rectangle + left dome
    draw.rectangle([cx - rx, cy - ry, cx, cy + ry],
                   fill=rgb1 + (255,))
    draw.ellipse([cx - rx, cy - ry, cx - rx + ry * 2, cy + ry],
                 fill=rgb1 + (255,))
    # Re-draw outline
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                 outline=outline, width=2)
    draw.line([(cx, cy - ry), (cx, cy + ry)], fill=outline, width=2)

    # Highlights
    draw.ellipse([cx - rx + 8, cy - ry + 5, cx - 10, cy - 4],
                 fill=lighten(rgb1) + (100,))
    draw.ellipse([cx + 10, cy - ry + 5, cx + rx - 8, cy - 4],
                 fill=lighten(rgb2) + (100,))

    add_label(draw, label, cx, cy + 2, font_size=10, color=(30, 30, 30))


def draw_oblong_tablet(draw: ImageDraw.Draw, color_hex: str, label: str,
                       cx: int = 100, cy: int = 100):
    """Oblong (stadium-shaped) tablet — wider than oval pill."""
    draw_oval_pill(draw, color_hex, label, score=True, cx=cx, cy=cy,
                   rx=75, ry=32)


def draw_inhaler(draw: ImageDraw.Draw, color_hex: str, label: str,
                 cx: int = 100, cy: int = 100):
    """Pressurised metered-dose inhaler (pMDI) shape."""
    rgb = hex_to_rgb(color_hex)
    dark = darken(rgb)
    outline = (40, 40, 120)

    # Canister body (rounded rectangle)
    body_x0, body_y0 = cx - 28, cy - 60
    body_x1, body_y1 = cx + 28, cy + 55
    r_corner = 14
    draw.rounded_rectangle([body_x0, body_y0, body_x1, body_y1],
                            radius=r_corner, fill=rgb + (255,), outline=outline, width=2)
    # Mouthpiece
    mp_x0, mp_y0 = cx - 22, cy + 55
    mp_x1, mp_y1 = cx + 22, cy + 80
    draw.rounded_rectangle([mp_x0, mp_y0, mp_x1, mp_y1],
                            radius=6, fill=darken(rgb, 0.8) + (255,),
                            outline=outline, width=2)
    # Actuation button on top
    draw.rounded_rectangle([cx - 12, cy - 75, cx + 12, cy - 58],
                            radius=5, fill=dark + (255,), outline=outline, width=1)
    # Highlight stripe
    draw.rectangle([body_x0 + 4, body_y0 + 10, body_x0 + 10, body_y1 - 10],
                   fill=lighten(rgb) + (120,))
    # Label text
    add_label(draw, "MDI", cx, cy - 15, font_size=14, color=(255, 255, 255))
    add_label(draw, label, cx, cy + 15, font_size=9, color=(255, 255, 255))


def draw_vial(draw: ImageDraw.Draw, label: str,
              cx: int = 100, cy: int = 100):
    """Insulin / injection vial — clear glass vial with blue label band."""
    glass_color = (220, 235, 245, 200)
    label_color = (30, 80, 160)
    outline = (80, 120, 180)

    # Vial body
    vx0, vy0 = cx - 30, cy - 52
    vx1, vy1 = cx + 30, cy + 55
    draw.rounded_rectangle([vx0, vy0, vx1, vy1], radius=12,
                            fill=glass_color, outline=outline, width=2)
    # Rubber stopper / cap at top
    draw.rounded_rectangle([cx - 20, cy - 65, cx + 20, cy - 48],
                            radius=6, fill=(70, 130, 180, 255), outline=outline, width=2)
    # Blue label band
    draw.rectangle([vx0 + 2, cy - 15, vx1 - 2, cy + 30],
                   fill=label_color + (220,))
    # Liquid fill (clear, light tint)
    draw.rectangle([vx0 + 4, cy + 30, vx1 - 4, vy1 - 10],
                   fill=(200, 220, 240, 180))
    # Highlight on glass
    draw.rectangle([vx0 + 5, vy0 + 8, vx0 + 10, vy1 - 12],
                   fill=(255, 255, 255, 160))
    # Text on label
    add_label(draw, label, cx, cy + 8, font_size=9, color=(255, 255, 255))
    add_label(draw, "100IU/mL", cx, cy + 22, font_size=8, color=(200, 230, 255))


# ---------------------------------------------------------------------------
# Per-medicine render functions
# ---------------------------------------------------------------------------

MEDICINES = {
    # id: (render_func_name, args...)
    "metformin_500mg":    ("oval_pill",   "#E87722", "MET\n500mg", True),
    "paracetamol_500mg":  ("oval_pill",   "#FFFFFF",  "PARA\n500mg", True),
    "aspirin_75mg":       ("round",       "#F5F5F5",  "ASP\n75mg",  True),
    "atorvastatin_10mg":  ("oval_pill",   "#FFFFFF",  "ATOR\n10mg", False),
    "amlodipine_5mg":     ("round",       "#F8F8F8",  "AML\n5mg",   False),
    "lisinopril_5mg":     ("oval_pill",   "#FFB6C1",  "LIS\n5mg",   True),
    "omeprazole_20mg":    ("capsule",     "#6A0DAD",  "#C8A0DC", "OME\n20mg"),
    "amoxicillin_500mg":  ("capsule",     "#FFD700",  "#FFFFFF",    "AMOX\n500mg"),
    "cetirizine_10mg":    ("oblong",      "#FFFFFF",  "CET\n10mg"),
    "ibuprofen_400mg":    ("oval_pill",   "#8B2500",  "IBU\n400mg", True),
    "losartan_50mg":      ("oval_pill",   "#90EE90",  "LOS\n50mg",  False),
    "metoprolol_25mg":    ("round",       "#FFB6CB",  "MET\n25mg",  True),
    "pantoprazole_40mg":  ("oval_pill",   "#FFD700",  "PAN\n40mg",  False),
    "clopidogrel_75mg":   ("round",       "#FF69B4",  "CLO\n75mg",  True),
    "furosemide_40mg":    ("round",       "#FFFFFF",  "FUR\n40mg",  True),
    "prednisolone_5mg":   ("round",       "#CC0000",  "PRED\n5mg",  False),
    "vitamin_d3_1000iu":  ("capsule",     "#FFEC00",  "#FFA500",    "VIT D3\n1000IU"),
    "calcium_500mg":      ("oval_pill",   "#F0F0F0",  "CAL\n500mg", False),
    "insulin_glargine":   ("vial",),
    "salbutamol_inhaler": ("inhaler",     "#1E90FF",  "SALB"),
}


def render_medicine(med_id: str, spec: tuple) -> Image.Image:
    img = Image.new("RGBA", SIZE, WHITE_BG)
    draw = ImageDraw.Draw(img, "RGBA")

    shape = spec[0]

    if shape == "oval_pill":
        _, color, label_raw, score = spec
        label = label_raw.replace("\n", " ")
        draw_oval_pill(draw, color, label, score=score)

    elif shape == "round":
        _, color, label_raw, score = spec
        label = label_raw.replace("\n", " ")
        draw_round_tablet(draw, color, label, score=score)

    elif shape == "capsule":
        _, c1, c2, label_raw = spec
        label = label_raw.replace("\n", " ")
        draw_capsule(draw, c1, c2, label)

    elif shape == "oblong":
        _, color, label_raw = spec
        label = label_raw.replace("\n", " ")
        draw_oblong_tablet(draw, color, label)

    elif shape == "inhaler":
        _, color, label_raw = spec
        label = label_raw.replace("\n", " ")
        draw_inhaler(draw, color, label)

    elif shape == "vial":
        draw_vial(draw, "INSULIN\nGLARGINE")

    return img.convert("RGBA")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generated = []
    failed = []

    for med_id, spec in MEDICINES.items():
        out_path = os.path.join(OUTPUT_DIR, f"{med_id}.png")
        try:
            img = render_medicine(med_id, spec)
            img.save(out_path, "PNG")
            generated.append(med_id)
            print(f"  [OK]  {med_id}.png")
        except Exception as exc:
            failed.append((med_id, str(exc)))
            print(f"  [ERR] {med_id}: {exc}")

    print(f"\nDone: {len(generated)} images generated, {len(failed)} failed.")
    if failed:
        for mid, err in failed:
            print(f"  FAILED: {mid} — {err}")


if __name__ == "__main__":
    main()
