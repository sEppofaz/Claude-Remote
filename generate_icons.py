#!/usr/bin/env python3
"""Generiert PNG-Icons für Claude Remote PWA – „CR"-Monogramm auf Lila."""
import struct, zlib, os

OUT = os.path.join(os.path.dirname(__file__), "static")

# 5×7 Pixel-Bitmaps
C = ["01110", "10001", "10000", "10000", "10000", "10001", "01110"]
R = ["11110", "10001", "10001", "11110", "10100", "10010", "10001"]

PURPLE = (124, 58, 237)
WHITE  = (255, 255, 255)


def png(w, h, rows):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def make_icon(size):
    # Buchstaben füllen ~58% der Breite (11 Einheiten: C + Lücke + R)
    scale = max(1, int(size * 0.58 / 11))
    lw = 5 * scale       # Buchstabenbreite
    lh = 7 * scale       # Buchstabenhöhe
    gap = scale
    ox = (size - (2 * lw + gap)) // 2   # horizontaler Offset
    oy = (size - lh) // 2               # vertikaler Offset

    grid = [[False] * size for _ in range(size)]

    def draw(bitmap, sx):
        for ri, row in enumerate(bitmap):
            for ci, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            x, y = sx + ci * scale + dx, oy + ri * scale + dy
                            if 0 <= x < size and 0 <= y < size:
                                grid[y][x] = True

    draw(C, ox)
    draw(R, ox + lw + gap)

    rows = []
    for row in grid:
        flat = []
        for white in row:
            flat += list(WHITE if white else PURPLE)
        rows.append(flat)
    return png(size, size, rows)


for size, name in [(192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")]:
    path = os.path.join(OUT, name)
    with open(path, "wb") as f:
        f.write(make_icon(size))
    kb = os.path.getsize(path) // 1024
    print(f"  ✓  {name} ({size}×{size}, {kb} KB)")

print("Icons fertig.")
