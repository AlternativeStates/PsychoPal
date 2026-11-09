#!/usr/bin/env python3
"""Draws the PsychoPal icon (gradient tile + white brain-wave line) as SVG and PNGs, with no image libraries. Run once."""
import math, os, struct, zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site", "icons")
C1, C2 = (0x6d, 0x4b, 0xd8), (0x0f, 0x9f, 0x95)

def wave(x):  # x in [0,1] -> y in [0,1]: a damped "EEG burst" sine
    env = math.exp(-((x - 0.5) ** 2) / 0.06)
    return 0.5 - 0.22 * env * math.sin(2 * math.pi * 3 * (x - 0.5))

def svg():
    pts = " ".join(f"{12 + 76 * i / 120:.2f},{100 * wave(i / 120):.2f}" for i in range(121))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6d4bd8"/><stop offset="1" stop-color="#0f9f95"/></linearGradient></defs>
<rect width="100" height="100" rx="22" fill="url(#g)"/>
<polyline points="{pts}" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="80" cy="22" r="6" fill="#fff" opacity=".9"/>
</svg>
'''

def png(size, maskable=False):
    ss = 3
    N = size * ss
    curve = [(12 + 76 * i / 600, 100 * wave(i / 600)) for i in range(601)]
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    x = (px * ss + sx + 0.5) / N * 100
                    y = (py * ss + sy + 0.5) / N * 100
                    r = 0 if maskable else 22
                    inside = True
                    for cx, cy in ((r, r), (100 - r, r), (r, 100 - r), (100 - r, 100 - r)):
                        if (x < r or x > 100 - r) and (y < r or y > 100 - r):
                            if (x - (r if x < 50 else 100 - r)) ** 2 + (y - (r if y < 50 else 100 - r)) ** 2 > r * r:
                                inside = False
                            break
                    if not inside:
                        continue
                    t = (x + y) / 200
                    col = [C1[i] + (C2[i] - C1[i]) * t for i in range(3)]
                    xs = 12 + (x - 12) * (0.78 if maskable else 1)
                    d = min(math.hypot(x - qx, y - qy) for qx, qy in curve[max(0, int((x - 12) / 76 * 600) - 40): int((x - 12) / 76 * 600) + 40] or curve[:1])
                    if d <= 3.5 or math.hypot(x - 80, y - 22) <= 6:
                        col = [255, 255, 255]
                    acc[0] += col[0]; acc[1] += col[1]; acc[2] += col[2]; acc[3] += 255
            n = ss * ss
            row += bytes([int(acc[0] / max(1, acc[3] / 255)), int(acc[1] / max(1, acc[3] / 255)), int(acc[2] / max(1, acc[3] / 255)), int(acc[3] / n)])
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "icon.svg"), "w", encoding="utf-8").write(svg())
    for s in (180, 192, 512):
        open(os.path.join(OUT, f"icon-{s}.png"), "wb").write(png(s))
    open(os.path.join(OUT, "icon-maskable-512.png"), "wb").write(png(512, maskable=True))
    print("icons written to", OUT)
