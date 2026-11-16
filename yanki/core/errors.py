"""Tarama hataları (TAR-05).

Her hata makinece okunabilir bir ``code`` ve Türkçe bir ``message`` taşır.
Arayüz katmanı bu sınıflara göre çıkış kodu seçer (CLI-03):

- ``TargetError``: girdi geçersiz ya da politika gereği reddedildi (kullanım hatası).
- ``ScanError``: sayfa açılamadı ya da tarama tamamlanamadı.
"""

from __future__ import annotations


class YankiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class TargetError(YankiError):
    """Hedef geçersiz ya da güvenlik politikasıyla reddedildi."""


class ScanError(YankiError):
    """Sayfa açılamadı ya da tarama yapılamadı."""
