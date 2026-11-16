"""Odak sırası ile görsel okuma sırası arasındaki sapma (KLV-04).

Saf fonksiyonlardır; tarayıcı gerektirmez.

Soldan sağa (LTR) okumada ardışık iki odak durağı ``a → b`` için:

- ``b`` tümüyle ``a``'nın üstündeyse ve yeni bir sütuna (``a``'nın sağına)
  geçilmiyorsa sapmadır. Örnek: altbilgiden başlığa geri sıçrama.
- ``b``, ``a`` ile aynı satırdaysa (dikeyde belirgin örtüşme) ve tümüyle
  ``a``'nın solundaysa sapmadır. Örnek: ters sıralanmış araç çubuğu.

Aşağı-sola geçiş yeni satırdır, yukarı-sağa geçiş yeni sütundur; ikisi de
normaldir. Böylece kenar çubuklu ve çok sütunlu düzenler alarm üretmez.
Sağdan sola (RTL) sayfalarda yatay yön aynalanır.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


def is_deviation(a: Rect, b: Rect, *, rtl: bool, x_tol: float, y_tol: float) -> bool:
    """``a``'dan ``b``'ye odak geçişi görsel okuma sırasına aykırı mı?"""
    above = b.bottom <= a.y + y_tol
    overlap = min(a.bottom, b.bottom) - max(a.y, b.y)
    same_row = overlap >= min(a.height, b.height) / 2
    if rtl:
        new_column = b.right <= a.x + x_tol
        backward_in_row = b.x >= a.right - x_tol
    else:
        new_column = b.x >= a.right - x_tol
        backward_in_row = b.right <= a.x + x_tol
    if above and not new_column:
        return True
    return same_row and backward_in_row


def order_deviations(
    rects: list[Rect | None], *, rtl: bool, x_tol: float, y_tol: float
) -> list[int]:
    """Sapma olan adımların sırası (``i``: ``rects[i-1] → rects[i]`` geçişi).

    Konumu olmayan (gizli, sıfır boyutlu) adımlar atlanır; karşılaştırma bir
    önceki konumlu adımla yapılır.
    """
    out: list[int] = []
    prev: Rect | None = None
    for i, rect in enumerate(rects):
        if rect is None or rect.width <= 0 or rect.height <= 0:
            continue
        if prev is not None and is_deviation(prev, rect, rtl=rtl, x_tol=x_tol, y_tol=y_tol):
            out.append(i)
        prev = rect
    return out
