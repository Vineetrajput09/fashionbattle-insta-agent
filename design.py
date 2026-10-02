"""
FashionBattle post design
=========================
Renders Instagram-ready 1080x1350 images with the brand frame, the logo in the
top-left corner and, optionally, a price badge. Adjust colors and sizes in the
SETTINGS section below.
"""

import math, os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

# ---------------- SETTINGS ----------------
W, H = 1080, 1350                      # Instagram portrait (4:5)
FRAME_OUTER = (0, 139, 153)            # Teal, matches the logo
FRAME_LINE = (249, 171, 2)             # Orange/gold, matches the logo
BACKGROUND = (245, 236, 226)           # Light beige
BADGE_COLOR = (107, 30, 120)           # Purple price badge
BADGE_TEXT = (255, 255, 255)
OUTER = 30                             # Teal border thickness
LINE_GAP = 22                          # Gap between the teal border and the inner line
LINE_W = 5                             # Inner line thickness


def font(name, size):
    return ImageFont.truetype(os.path.join(ASSETS, name), size)


def fit_text(draw, text, name, max_w, start, min_size):
    """Largest font size (from start down to min_size) that fits max_w."""
    size = start
    while size > min_size:
        f = font(name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(name, min_size)


def wrap_title(draw, title, name, size, max_w, max_lines=2):
    """Wrap the title to at most max_lines, adding an ellipsis if it is too long."""
    f = font(name, size)
    words, lines, cur = title.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=f) > max_w and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines, f


def scallop_badge(size, color):
    """Scalloped badge, drawn at 4x and downscaled for smooth edges."""
    s = 4
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = big / 2
    R, bumps, depth = big * 0.44, 12, big * 0.035
    pts = []
    for i in range(720):
        t = 2 * math.pi * i / 720
        r = R + depth * abs(math.cos(bumps * t / 2)) * 2 - depth
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    d.polygon(pts, fill=color)
    return img.resize((size, size), Image.LANCZOS)


def draw_badge(canvas, title, price, discount):
    """Draw the price badge in the bottom-right corner."""
    size = 330
    badge = scallop_badge(size, BADGE_COLOR + (255,))

    # Soft drop shadow
    shadow = Image.new("RGBA", (size + 40, size + 40), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (20, 26), badge.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))

    d = ImageDraw.Draw(badge)
    inner_w = size * 0.70
    lines, tf = wrap_title(d, title, "Poppins-Medium.ttf", 28, inner_w) if title else ([], None)
    pf = fit_text(d, price, "Poppins-Bold.ttf", inner_w, 70, 40)
    df = font("Poppins-Bold.ttf", 26)
    disc_text = f"{discount}% OFF" if discount else ""

    heights = [tf.getbbox(l)[3] - tf.getbbox(l)[1] + 8 for l in lines] if lines else []
    ph = pf.getbbox(price)[3] - pf.getbbox(price)[1]
    dh = (df.getbbox(disc_text)[3] - df.getbbox(disc_text)[1] + 10) if disc_text else 0
    total = sum(heights) + (12 if lines else 0) + ph + dh
    y = (size - total) / 2

    for l, h in zip(lines, heights):
        tw = d.textlength(l, font=tf)
        d.text(((size - tw) / 2, y - tf.getbbox(l)[1]), l, font=tf, fill=BADGE_TEXT)
        y += h
    if lines:
        y += 12
    tw = d.textlength(price, font=pf)
    d.text(((size - tw) / 2, y - pf.getbbox(price)[1]), price, font=pf, fill=BADGE_TEXT)
    y += ph + 16
    if disc_text:
        tw = d.textlength(disc_text, font=df)
        pad = 12
        d.rounded_rectangle(((size - tw) / 2 - pad, y - 4, (size + tw) / 2 + pad, y + dh + 2),
                            radius=14, fill=FRAME_LINE)
        d.text(((size - tw) / 2, y - df.getbbox(disc_text)[1] + 1), disc_text, font=df, fill=(40, 20, 0))

    x = W - OUTER - LINE_GAP - size - 18
    yb = H - OUTER - LINE_GAP - size - 18
    canvas.alpha_composite(shadow, (x - 20, yb - 20))
    canvas.alpha_composite(badge, (x, yb))


def draw_logo(canvas):
    """Logo without a background box; a soft shadow keeps it visible on any photo."""
    logo = Image.open(os.path.join(ASSETS, "logo.png")).convert("RGBA")
    logo.thumbnail((120, 120), Image.LANCZOS)
    pad = 20
    shadow = Image.new("RGBA", (logo.width + 2 * pad, logo.height + 2 * pad), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), (pad + 2, pad + 4), logo.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    pos = OUTER + LINE_GAP + 28
    canvas.alpha_composite(shadow, (pos - pad, pos - pad))
    canvas.alpha_composite(logo, (pos, pos))


def make_post_image(photo_path, out_path, title=None, price=None, discount=None):
    """Create the final post image. The badge is drawn only when a price is given."""
    canvas = Image.new("RGBA", (W, H), FRAME_OUTER + (255,))
    d = ImageDraw.Draw(canvas)
    d.rectangle((OUTER, OUTER, W - OUTER - 1, H - OUTER - 1), fill=BACKGROUND)
    lg = OUTER + LINE_GAP
    d.rectangle((lg, lg, W - lg - 1, H - lg - 1), outline=FRAME_LINE, width=LINE_W)

    # Fit the product photo inside the inner line without cropping
    area = lg + LINE_W + 8
    aw, ah = W - 2 * area, H - 2 * area
    photo = Image.open(photo_path).convert("RGB")
    scale = min(aw / photo.width, ah / photo.height)   # Upscale small photos as well
    photo = photo.resize((int(photo.width * scale), int(photo.height * scale)), Image.LANCZOS)
    canvas.paste(photo, (area + (aw - photo.width) // 2, area + (ah - photo.height) // 2))

    draw_logo(canvas)
    if price:
        draw_badge(canvas, title, price, discount)
    canvas.convert("RGB").save(out_path, "JPEG", quality=92)
