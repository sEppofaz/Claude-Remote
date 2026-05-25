#!/usr/bin/env python3
"""Generiert PNG-Icons für Claude Agent PWA. Nur stdlib, kein Pillow."""
import struct, zlib, os

OUT = os.path.join(os.path.dirname(__file__), "static")

def png(w, h, rows):
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    raw = b""
    for row in rows:
        raw += b"\x00" + bytes(row)
    compressed = zlib.compress(raw, 9)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )

def make_icon(size):
    rows = []
    cx, cy, r = size // 2, size // 2, size // 2
    # Lila Hintergrund
    bg = (124, 58, 237)
    # Weißes Roboter-Symbol (vereinfacht: Kreis + Rechteck)
    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            if dx*dx + dy*dy <= r*r:
                # Kopf (oberes Drittel)
                head_top = size * 2 // 10
                head_bot = size * 5 // 10
                head_l   = size * 3 // 10
                head_r   = size * 7 // 10
                # Körper (mittleres Drittel)
                body_top = size * 5 // 10
                body_bot = size * 8 // 10
                body_l   = size * 25 // 100
                body_r   = size * 75 // 100
                # Augen
                eye_y    = size * 35 // 100
                eye_r    = size * 5  // 100
                eye1_x   = size * 38 // 100
                eye2_x   = size * 62 // 100
                # Antenne
                ant_x    = cx
                ant_bot  = size * 2  // 10
                ant_top  = size * 5  // 100
                ant_w    = size * 2  // 100

                in_head  = (head_l <= x < head_r and head_top <= y < head_bot)
                in_body  = (body_l <= x < body_r and body_top <= y < body_bot)
                in_eye1  = ((x - eye1_x)**2 + (y - eye_y)**2 <= eye_r**2)
                in_eye2  = ((x - eye2_x)**2 + (y - eye_y)**2 <= eye_r**2)
                in_ant   = (abs(x - ant_x) <= ant_w and ant_top <= y < ant_bot)

                if in_ant or in_head or in_body:
                    if in_eye1 or in_eye2:
                        row += list(bg)
                    else:
                        row += [255, 255, 255]
                else:
                    row += list(bg)
            else:
                row += [255, 255, 255]
        rows.append(row)
    return png(size, size, rows)

for size, name in [(192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")]:
    path = os.path.join(OUT, name)
    with open(path, "wb") as f:
        f.write(make_icon(size))
    print(f"  ✓  {name} ({size}x{size})")

print("Icons fertig.")
