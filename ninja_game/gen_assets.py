# Generates every pixel-art asset for the ninja platformer into ./assets/
# Sheets (all 32px cells): ninja.png [idle0,idle1,run0..5,jump,fall]
#   enemy.png [walk0,walk1,squash]  tiles.png [ground,dirt,brick,qblock,used,stone]
#   items.png [coin0..3,shuriken0,shuriken1]  flag.png 32x96  bg.png 768x224
import math
import os
import random
from PIL import Image, ImageDraw, ImageChops

S = 32
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# ---- palette ----
OUTLINE  = (16, 16, 28, 255)
SUIT     = (58, 68, 108, 255)
SUIT_SH  = (40, 47, 80, 255)
SUIT_DK  = (28, 33, 58, 255)
SUIT_HI  = (82, 94, 138, 255)
FEET     = (22, 26, 44, 255)
SKIN     = (232, 184, 138, 255)
SKIN_SH  = (198, 148, 105, 255)
EYE      = (15, 15, 20, 255)
RED      = (200, 60, 48, 255)
RED_DK   = (150, 40, 36, 255)
BLADE    = (70, 160, 225, 255)
BLADE_L  = (150, 210, 245, 255)
GUARD    = (241, 196, 15, 255)
GRIP     = (60, 44, 36, 255)


def add_outline(img):
    a = img.getchannel("A")
    mask = a.point(lambda v: 255 if v > 0 else 0)
    dil = Image.new("L", img.size, 0)
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        dil = ImageChops.lighter(dil, ImageChops.offset(mask, dx, dy))
    edge = ImageChops.subtract(dil, mask)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", img.size, OUTLINE), (0, 0), edge)
    out.alpha_composite(img)
    return out


def limb(d, p1, p2, base, shadow):
    """3px-thick limb segment: 2px base with a 1px shadow along the lower edge."""
    d.line([p1, p2], fill=shadow, width=3)
    d.line([(p1[0], p1[1] - 1), (p2[0], p2[1] - 1)], fill=base, width=2)


# ---------------- ninja ----------------
def draw_ninja(back_leg, front_leg, bob, hand, tails, back_hand):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    b = bob
    hip = (15, 21 + b)
    sho = (16, 15 + b)

    # back arm
    limb(d, (15, 15 + b), (back_hand[0], back_hand[1] + b), SUIT_SH, SUIT_DK)
    # back leg
    (kx, ky), (fx, fy) = back_leg
    limb(d, hip, (kx, ky + b), SUIT_SH, SUIT_DK)
    limb(d, (kx, ky + b), (fx, fy), SUIT_SH, SUIT_DK)
    d.rectangle([fx, fy - 1, fx + 2, fy], fill=FEET)

    # torso with side shadow + edge highlight
    d.polygon([(13, 14 + b), (19, 14 + b), (18, 21 + b), (13, 21 + b)], fill=SUIT)
    d.line([(18, 15 + b), (17, 21 + b)], fill=SUIT_SH, width=2)
    d.line([(14, 15 + b), (14, 20 + b)], fill=SUIT_HI)
    # belt + knot
    d.rectangle([12, 19 + b, 18, 19 + b], fill=RED)
    d.rectangle([12, 20 + b, 18, 20 + b], fill=RED_DK)
    d.point((11, 20 + b), fill=RED_DK)
    d.point((11, 22 + b), fill=RED)

    # hood
    d.rectangle([12, 4 + b, 21, 13 + b], fill=SUIT)
    for px in [(12, 4 + b), (21, 4 + b), (12, 13 + b), (21, 13 + b)]:
        d.point(px, fill=(0, 0, 0, 0))
    d.line([(13, 5 + b), (19, 5 + b)], fill=SUIT_HI)
    d.line([(21, 8 + b), (21, 12 + b)], fill=SUIT_SH)
    d.line([(13, 13 + b), (20, 13 + b)], fill=SUIT_SH)
    # headband
    d.rectangle([12, 6 + b, 21, 6 + b], fill=RED)
    d.rectangle([12, 7 + b, 21, 7 + b], fill=RED_DK)
    # face opening + eye
    d.rectangle([17, 9 + b, 20, 10 + b], fill=SKIN)
    d.rectangle([17, 11 + b, 20, 11 + b], fill=SKIN_SH)
    d.rectangle([19, 9 + b, 19, 10 + b], fill=EYE)
    # fluttering tails
    pts = [(12, 6 + b)] + [(x, y + b) for x, y in tails]
    for i in range(len(pts) - 1):
        d.line([pts[i], pts[i + 1]], fill=RED, width=1)

    # front leg
    (kx, ky), (fx, fy) = front_leg
    limb(d, hip, (kx, ky + b), SUIT, SUIT_SH)
    limb(d, (kx, ky + b), (fx, fy), SUIT, SUIT_SH)
    d.rectangle([fx, fy - 1, fx + 2, fy], fill=FEET)

    # front arm + hand
    hx, hy = hand[0], hand[1] + b
    limb(d, (17, 15 + b), (hx - 1, hy), SUIT, SUIT_SH)
    d.rectangle([hx - 1, hy - 1, hx, hy], fill=SUIT_HI)

    # sword: wrapped grip, gold tsuba, 3-tone blade
    d.line([(hx - 2, hy + 2), (hx + 1, hy - 1)], fill=GRIP, width=2)
    d.point((hx - 1, hy + 1), fill=RED)
    d.polygon([(hx + 2, hy - 4), (hx + 4, hy - 2), (hx + 2, hy), (hx, hy - 2)], fill=GUARD)
    d.line([(hx + 3, hy - 3), (hx + 8, hy - 8)], fill=BLADE, width=2)
    d.line([(hx + 3, hy - 4), (hx + 8, hy - 9)], fill=BLADE_L)
    d.point((hx + 9, hy - 10), fill=(255, 255, 255, 255))
    return add_outline(img)


RUN_CYCLE = [
    ((19, 25), (22, 29)),
    ((17, 26), (17, 29)),
    ((13, 26), (11, 28)),
    ((12, 25), (9, 27)),
    ((13, 26), (12, 26)),
    ((17, 25), (19, 26)),
]
RUN_TAILS = [
    [(10, 5), (8, 5), (6, 6)],
    [(10, 6), (8, 6), (6, 7)],
    [(10, 6), (8, 7), (6, 7)],
    [(10, 5), (8, 6), (6, 6)],
    [(10, 6), (8, 7), (6, 8)],
    [(10, 5), (8, 5), (6, 5)],
]
RUN_BACK_HAND = [10, 11, 12, 12, 11, 10]


def build_ninja_sheet():
    frames = []
    for bob in (0, 1):  # idle x2
        frames.append(draw_ninja(((14, 25), (13, 29)), ((17, 25), (17, 29)), bob,
                                 (21, 18), [(10, 6), (8, 7), (6, 7)], (12, 20)))
    for f in range(6):  # run x6
        bob = [0, 1, 0, 0, 1, 0][f]
        frames.append(draw_ninja(RUN_CYCLE[(f + 3) % 6], RUN_CYCLE[f], bob,
                                 (22, 16), RUN_TAILS[f], (RUN_BACK_HAND[f], 19)))
    # jump (tucked), fall (legs split)
    frames.append(draw_ninja(((13, 24), (11, 26)), ((18, 24), (16, 27)), 0,
                             (22, 14), [(10, 7), (8, 8), (6, 9)], (10, 16)))
    frames.append(draw_ninja(((12, 25), (10, 28)), ((19, 25), (21, 28)), 0,
                             (22, 17), [(10, 4), (8, 3), (6, 3)], (10, 17)))
    save_sheet(frames, "ninja.png")


# ---------------- enemy (oni) ----------------
ONI    = (198, 70, 58, 255)
ONI_HI = (225, 105, 85, 255)
ONI_DK = (148, 42, 38, 255)
BELLY  = (222, 168, 122, 255)
HORN   = (240, 230, 200, 255)
HORN_S = (190, 178, 146, 255)
CLUB   = (74, 78, 92, 255)


def draw_oni(frame):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if frame == 2:  # squashed
        d.ellipse([3, 19, 28, 29], fill=ONI)
        d.ellipse([7, 24, 24, 29], fill=ONI_DK)
        d.polygon([(9, 20), (11, 15), (13, 20)], fill=HORN)
        d.polygon([(18, 20), (20, 15), (22, 20)], fill=HORN)
        d.line([(10, 23), (13, 23)], fill=EYE)
        d.line([(18, 23), (21, 23)], fill=EYE)
        return add_outline(img)
    step = frame
    # body: 3-tone + belly
    d.ellipse([5, 8, 26, 27], fill=ONI)
    d.ellipse([7, 19, 24, 27], fill=ONI_DK)
    d.ellipse([10, 17, 21, 26], fill=BELLY)
    d.ellipse([12, 22, 19, 26], fill=(196, 138, 96, 255))
    d.arc([6, 9, 25, 26], 200, 320, fill=ONI_HI, width=2)
    # feet (alternate per frame)
    d.rectangle([7 + step * 2, 27, 12 + step * 2, 29], fill=ONI_DK)
    d.rectangle([19 - step * 2, 27, 24 - step * 2, 29], fill=ONI_DK)
    # horns, shaded
    d.polygon([(9, 10), (11, 3), (13, 10)], fill=HORN)
    d.polygon([(18, 10), (20, 3), (22, 10)], fill=HORN)
    d.line([(11, 4), (12, 9)], fill=HORN_S)
    d.line([(20, 4), (21, 9)], fill=HORN_S)
    # angry eyes + brows
    d.rectangle([9, 13, 13, 16], fill=(255, 255, 255, 255))
    d.rectangle([18, 13, 22, 16], fill=(255, 255, 255, 255))
    d.rectangle([12, 14, 13, 16], fill=EYE)
    d.rectangle([18, 14, 19, 16], fill=EYE)
    d.line([(9, 12), (13, 13)], fill=OUTLINE, width=2)
    d.line([(22, 12), (18, 13)], fill=OUTLINE, width=2)
    # mouth + tusks
    d.line([(12, 20), (19, 20)], fill=OUTLINE)
    d.rectangle([12, 18, 13, 19], fill=HORN)
    d.rectangle([18, 18, 19, 19], fill=HORN)
    # kanabo club held at the side, studded
    d.line([(25, 24), (30, 10)], fill=CLUB, width=3)
    d.point((27, 14), fill=(150, 156, 170, 255))
    d.point((28, 18), fill=(150, 156, 170, 255))
    d.point((26, 21), fill=(150, 156, 170, 255))
    d.ellipse([23, 21, 27, 25], fill=ONI_DK)
    return add_outline(img)


# ---------------- tiles ----------------
def draw_tile(kind):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "ground":
        d.rectangle([0, 0, 31, 31], fill=(98, 102, 116, 255))
        for x, y in [(6, 12), (18, 15), (26, 22), (10, 25), (22, 9)]:
            d.rectangle([x, y, x + 3, y + 2], fill=(84, 88, 102, 255))
            d.line([(x, y), (x + 3, y)], fill=(112, 116, 130, 255))
        d.line([(8, 14), (12, 20)], fill=(70, 74, 88, 255))
        d.line([(23, 12), (20, 18)], fill=(70, 74, 88, 255))
        d.rectangle([0, 0, 31, 5], fill=(86, 142, 92, 255))
        d.rectangle([0, 0, 31, 0], fill=(128, 186, 118, 255))
        for x, h in [(4, 3), (14, 2), (24, 4)]:  # hanging moss
            d.rectangle([x, 5, x + 2, 5 + h], fill=(86, 142, 92, 255))
        d.line([(0, 6), (31, 6)], fill=(66, 108, 72, 255))
        d.line([(31, 7), (31, 31)], fill=(80, 84, 98, 255))
    elif kind == "dirt":
        d.rectangle([0, 0, 31, 31], fill=(70, 74, 88, 255))
        for x, y in [(3, 5), (13, 2), (24, 7), (7, 15), (19, 18), (27, 23), (4, 25), (14, 27), (23, 13)]:
            d.rectangle([x, y, x + 3, y + 2], fill=(54, 58, 72, 255))
            d.line([(x, y), (x + 3, y)], fill=(86, 90, 104, 255))
    elif kind == "brick":
        d.rectangle([0, 0, 31, 31], fill=(150, 80, 58, 255))
        m = (84, 46, 38, 255)
        cells = []
        for i, y in enumerate((2, 12, 22)):
            joints = [15] if i % 2 == 0 else [7, 23]
            xs = [0] + [j + 2 for j in joints]
            xe = [j - 1 for j in joints] + [31]
            for a, bx in zip(xs, xe):
                cells.append((a, y, bx, y + 7))
        d.rectangle([0, 0, 31, 1], fill=m)
        for y in (10, 20, 30):
            d.rectangle([0, y, 31, y + 1], fill=m)
        for i, y in enumerate((2, 12, 22)):
            for j in ([15] if i % 2 == 0 else [7, 23]):
                d.rectangle([j, y, j + 1, y + 7], fill=m)
        for a, y, bx, by in cells:
            d.line([(a, y), (bx, y)], fill=(178, 102, 74, 255))
            d.line([(a, by), (bx, by)], fill=(122, 62, 46, 255))
    elif kind in ("qblock", "used"):
        gold = kind == "qblock"
        base = (236, 180, 64, 255) if gold else (128, 104, 84, 255)
        hi   = (255, 224, 130, 255) if gold else (92, 74, 58, 255)
        lo   = (176, 122, 32, 255) if gold else (156, 130, 106, 255)
        bd   = (110, 72, 18, 255) if gold else (72, 58, 46, 255)
        d.rectangle([0, 0, 31, 31], fill=base)
        d.rectangle([0, 0, 31, 31], outline=bd)
        d.line([(1, 1), (30, 1)], fill=hi); d.line([(1, 1), (1, 30)], fill=hi)
        d.line([(1, 30), (30, 30)], fill=lo); d.line([(30, 2), (30, 30)], fill=lo)
        for x, y in [(3, 3), (26, 3), (3, 26), (26, 26)]:
            d.rectangle([x, y, x + 2, y + 2], fill=bd)
            d.point((x, y), fill=hi)
        if gold:
            q = ["0110", "1001", "0001", "0010", "0010", "0000", "0010"]
            for r, row in enumerate(q):
                for c, ch in enumerate(row):
                    if ch == "1":
                        d.rectangle([13 + c * 2, 10 + r * 2, 14 + c * 2, 11 + r * 2], fill=lo)
                        d.rectangle([12 + c * 2, 9 + r * 2, 13 + c * 2, 10 + r * 2], fill=(94, 58, 12, 255))
    else:  # stone
        d.rectangle([0, 0, 31, 31], fill=(96, 102, 118, 255))
        d.line([(0, 0), (31, 0)], fill=(128, 134, 150, 255)); d.line([(0, 0), (0, 31)], fill=(128, 134, 150, 255))
        d.line([(0, 31), (31, 31)], fill=(64, 70, 86, 255)); d.line([(31, 1), (31, 31)], fill=(64, 70, 86, 255))
        d.line([(8, 10), (14, 18), (12, 26)], fill=(70, 76, 92, 255))
        d.line([(22, 6), (19, 13)], fill=(70, 76, 92, 255))
    return img


# ---------------- items ----------------
def draw_coin(width):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, x1 = 16 - width // 2, 16 + width // 2
    d.ellipse([x0, 6, x1, 26], fill=(242, 204, 80, 255), outline=(168, 118, 26, 255))
    if width >= 8:
        d.arc([x0 + 1, 7, x1 - 1, 25], 160, 300, fill=(255, 236, 150, 255), width=2)
        hw = 2 if width >= 12 else 1  # square hole (mon coin)
        d.rectangle([16 - hw, 16 - 3, 16 + hw, 16 + 2], fill=(0, 0, 0, 0))
        d.rectangle([16 - hw, 16 - 3, 16 + hw, 16 + 2], outline=(168, 118, 26, 255))
    return add_outline(img)


def draw_shuriken(rot):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = 16, 16

    def star(r_out, r_in, color):
        pts = []
        for i in range(8):
            ang = math.pi / 4 * i + (math.pi / 8 if rot else 0)
            r = r_out if i % 2 == 0 else r_in
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        d.polygon(pts, fill=color)

    star(12, 4, (58, 63, 76, 255))
    star(9, 3, (92, 98, 112, 255))
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(120, 126, 140, 255))
    d.ellipse([cx - 1, cy - 1, cx + 1, cy + 1], fill=(30, 33, 44, 255))
    return add_outline(img)


def build_flag():
    img = Image.new("RGBA", (S, 96), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, 6, 95], fill=(66, 70, 84, 255))
    d.line([(4, 4), (4, 95)], fill=(96, 100, 116, 255))
    d.ellipse([2, 0, 8, 6], fill=GUARD)
    d.point((3, 1), fill=(255, 236, 150, 255))
    d.polygon([(7, 8), (29, 15), (7, 24)], fill=RED)
    d.polygon([(13, 10), (16, 15), (13, 21)], fill=RED_DK)
    d.polygon([(20, 12), (23, 15), (20, 19)], fill=RED_DK)
    d.line([(7, 8), (26, 14)], fill=(222, 90, 70, 255))
    d.ellipse([8, 11, 14, 17], fill=(240, 240, 240, 255))
    d.ellipse([10, 13, 12, 15], fill=RED)
    return add_outline(img).save(os.path.join(OUT, "flag.png"))


# ---------------- background (tileable 768x224) ----------------
def build_bg():
    W, H = 768, 224
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rng = random.Random(11)
    for _ in range(45):  # stars
        x, y = rng.randint(0, W - 1), rng.randint(0, 95)
        d.point((x, y), fill=rng.choice([(200, 205, 225, 255), (235, 238, 250, 255), (160, 168, 200, 255)]))
    # moon with craters
    d.ellipse([596, 14, 660, 78], fill=(238, 230, 205, 255))
    d.ellipse([608, 26, 626, 44], fill=(210, 202, 178, 255))
    d.ellipse([634, 48, 646, 60], fill=(210, 202, 178, 255))
    for cx, cy, w in [(110, 58, 74), (390, 36, 96), (620, 92, 62)]:
        d.ellipse([cx, cy, cx + w, cy + 18], fill=(196, 202, 220, 255))
        d.ellipse([cx + w // 4, cy - 9, cx + 3 * w // 4, cy + 10], fill=(196, 202, 220, 255))
    far, near = (52, 60, 88, 255), (38, 44, 68, 255)
    for x in range(W):  # far range, ridge-lit
        t = 2 * math.pi * x / W
        y = int(120 + 26 * math.sin(2 * t) + 13 * math.sin(5 * t + 1.3))
        d.line([(x, y), (x, H)], fill=far)
        d.point((x, y), fill=(66, 74, 104, 255))
    # pagoda with lit windows
    px, base = 500, 178
    dark = (30, 34, 56, 255)
    for tier in range(3):
        w = 76 - tier * 20
        ty = base - tier * 34
        d.polygon([(px - w // 2 - 8, ty), (px + w // 2 + 8, ty),
                   (px + w // 2 - 6, ty - 12), (px - w // 2 + 6, ty - 12)], fill=dark)
        d.rectangle([px - w // 2 + 8, ty - 34, px + w // 2 - 8, ty], fill=dark)
        d.rectangle([px - 8, ty - 26, px - 5, ty - 20], fill=(255, 196, 110, 255))
        d.rectangle([px + 5, ty - 26, px + 8, ty - 20], fill=(255, 196, 110, 255))
    d.polygon([(px - 6, base - 102), (px + 6, base - 102), (px, base - 116)], fill=dark)
    for x in range(W):  # near hills
        t = 2 * math.pi * x / W
        y = int(170 + 18 * math.sin(3 * t + 0.7) + 9 * math.sin(7 * t))
        d.line([(x, y), (x, H)], fill=near)
    # torii gate silhouette on a near crest
    tor = (26, 30, 48, 255)
    d.rectangle([172, 150, 175, 178], fill=tor)
    d.rectangle([193, 150, 196, 178], fill=tor)
    d.rectangle([166, 146, 202, 149], fill=tor)
    d.point((166, 144), fill=tor); d.point((165, 145), fill=tor)
    d.point((202, 144), fill=tor); d.point((203, 145), fill=tor)
    d.rectangle([170, 154, 198, 156], fill=tor)
    img.save(os.path.join(OUT, "bg.png"))


def save_sheet(frames, name):
    sheet = Image.new("RGBA", (S * len(frames), S), (0, 0, 0, 0))
    for i, fr in enumerate(frames):
        sheet.paste(fr, (i * S, 0))
    sheet.save(os.path.join(OUT, name))


def main():
    os.makedirs(OUT, exist_ok=True)
    build_ninja_sheet()
    save_sheet([draw_oni(0), draw_oni(1), draw_oni(2)], "enemy.png")
    save_sheet([draw_tile(k) for k in ("ground", "dirt", "brick", "qblock", "used", "stone")], "tiles.png")
    save_sheet([draw_coin(w) for w in (12, 8, 4, 8)] + [draw_shuriken(0), draw_shuriken(1)], "items.png")
    build_flag()
    build_bg()
    names = ["ninja.png", "enemy.png", "tiles.png", "items.png", "flag.png", "bg.png"]
    imgs = [Image.open(os.path.join(OUT, n)) for n in names]
    pw = max(i.width for i in imgs) * 2
    ph = sum(i.height * 2 + 8 for i in imgs)
    prev = Image.new("RGBA", (pw, ph), (43, 45, 58, 255))
    y = 0
    for im in imgs:
        big = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
        prev.alpha_composite(big, (0, y))
        y += big.height + 8
    prev.convert("RGB").save(os.path.join(OUT, "preview_all.png"))
    print("assets done")


if __name__ == "__main__":
    main()
