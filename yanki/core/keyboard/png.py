"""Odak göstergesi karşılaştırması için küçük PNG çözücü (KLV-03).

Yalnızca standart kütüphane (``zlib``) kullanılır; yeni bağımlılık yoktur.
Chromium ekran görüntüleri 8 bit RGB ya da RGBA, interlace'siz PNG'dir.
Diğer biçimler ``PngError`` yükseltir; çağıran bunu "değerlendirilemedi"
olarak raporlar (NFR-16).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {2: 3, 6: 4}  # renk türü → kanal sayısı (RGB, RGBA)


class PngError(ValueError):
    """PNG çözülemedi ya da desteklenmeyen biçimde."""


@dataclass(frozen=True)
class Image:
    width: int
    height: int
    channels: int
    pixels: bytes
    """Satır satır, filtresi çözülmüş ham örnekler."""


def decode_png(data: bytes) -> Image:
    if not data.startswith(_SIGNATURE):
        raise PngError("PNG imzası yok")
    pos = len(_SIGNATURE)
    header: tuple[int, ...] | None = None
    idat: list[bytes] = []
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat.append(chunk)
        elif ctype == b"IEND":
            break
    if header is None:
        raise PngError("IHDR yok")
    width, height, depth, color, _comp, _filter, interlace = header
    if depth != 8 or color not in _CHANNELS or interlace != 0:
        raise PngError(f"desteklenmeyen PNG: derinlik={depth} renk={color} interlace={interlace}")
    channels = _CHANNELS[color]
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise PngError(f"IDAT çözülemedi: {exc}") from exc
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise PngError("IDAT boyutu beklenenle uyuşmuyor")
    out = bytearray(height * stride)
    prev = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        ftype = raw[start]
        line = bytearray(raw[start + 1 : start + 1 + stride])
        _unfilter(ftype, line, prev, channels)
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return Image(width, height, channels, bytes(out))


def _unfilter(ftype: int, line: bytearray, prev: bytearray, bpp: int) -> None:
    n = len(line)
    if ftype == 0:
        return
    if ftype == 1:  # Sub
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif ftype == 2:  # Up
        for i in range(n):
            line[i] = (line[i] + prev[i]) & 0xFF
    elif ftype == 3:  # Average
        for i in range(n):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
    elif ftype == 4:  # Paeth
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                pred = a
            elif pb <= pc:
                pred = b
            else:
                pred = c
            line[i] = (line[i] + pred) & 0xFF
    else:
        raise PngError(f"bilinmeyen filtre türü: {ftype}")


def changed_pixels(a: Image, b: Image, delta: int) -> int:
    """İki görüntüde en az bir renk kanalı ``delta`` kadar farklı olan piksel sayısı.

    Boyutlar farklıysa tüm pikseller değişmiş sayılır.
    """
    if (a.width, a.height) != (b.width, b.height):
        return max(a.width * a.height, b.width * b.height)
    if a.pixels == b.pixels and a.channels == b.channels:
        return 0
    pa, pb, ca, cb = a.pixels, b.pixels, a.channels, b.channels
    count = 0
    for i in range(a.width * a.height):
        ia, ib = i * ca, i * cb
        if (
            abs(pa[ia] - pb[ib]) >= delta
            or abs(pa[ia + 1] - pb[ib + 1]) >= delta
            or abs(pa[ia + 2] - pb[ib + 2]) >= delta
        ):
            count += 1
    return count


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Testler için en basit RGB PNG kodlayıcı (filtre 0)."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(ctype: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(ctype + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )
