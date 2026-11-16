"""Yankı komut satırı (Typer). Yalnızca çekirdeği çağıran ince katmandır.

Çıkış kodları (CLI-03):

- 0: başarılı
- 1: eşik aşıldı (Faz 3: ``--min-score`` / ``--fail-on``)
- 2: kullanım hatası (geçersiz ya da politika gereği reddedilen hedef)
- 3: tarama yapılamadı (TAR-05)
"""

from __future__ import annotations

import asyncio
import sys
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from yanki import __version__
from yanki.config import YankiConfig, load_config
from yanki.core.errors import ScanError, TargetError
from yanki.core.scan import run_scan


class ExitCode(IntEnum):
    OK = 0
    THRESHOLD = 1
    USAGE = 2
    SCAN_FAILED = 3


SUPPORTED_FORMATS = {"json"}
PLANNED_FORMATS = {"html", "pdf"}  # Faz 3 (RAP-01)

app = typer.Typer(
    name="yanki",
    help="Yankı: yapay zeka destekli web erişilebilirlik denetim ve puanlama aracı.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"yanki {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Sürümü yazdır."),
    ] = False,
) -> None:
    """Yankı komutları."""


def _fail(code: ExitCode, message: str) -> typer.Exit:
    typer.echo(f"Hata: {message}", err=True)
    return typer.Exit(int(code))


def _parse_viewport(value: str) -> tuple[int, int]:
    try:
        width, height = (int(x) for x in value.lower().split("x", 1))
    except ValueError as exc:
        raise typer.BadParameter("Biçim GENİŞLİKxYÜKSEKLİK olmalı, ör. 1280x800.") from exc
    if width <= 0 or height <= 0:
        raise typer.BadParameter("Görünüm alanı boyutları pozitif olmalı.")
    return width, height


@app.command()
def scan(
    target: Annotated[
        str, typer.Argument(help="Taranacak URL (http/https) ya da yerel HTML dosyası.")
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Raporun yazılacağı dosya. Verilmezse standart çıktı."),
    ] = None,
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="Rapor biçimi. Faz 0'da yalnızca 'json'.")
    ] = "json",
    config_path: Annotated[
        Path | None, typer.Option("--config", "-c", help="TOML yapılandırma dosyası.")
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", min=0.1, help="Sayfa yükleme zaman aşımı (saniye)."),
    ] = None,
    viewport: Annotated[
        str | None, typer.Option("--viewport", help="Görünüm alanı, ör. 1280x800.")
    ] = None,
    locale: Annotated[
        str | None, typer.Option("--locale", help="Tarayıcı dili, ör. tr-TR.")
    ] = None,
    allow_private_network: Annotated[
        bool,
        typer.Option(
            "--allow-private-network",
            help="localhost ve yerel ağ adreslerinin taranmasına izin ver (NFR-08).",
        ),
    ] = False,
) -> None:
    """Bir sayfayı tarar ve bulguları ortak şemayla raporlar (TAR, KUR, RAP-01)."""
    formats = {f.strip().lower() for f in fmt.split(",") if f.strip()}
    unknown = formats - SUPPORTED_FORMATS - PLANNED_FORMATS
    if unknown or not formats:
        raise _fail(ExitCode.USAGE, f"Bilinmeyen rapor biçimi: {', '.join(sorted(unknown)) or fmt}")
    planned = formats & PLANNED_FORMATS
    if planned:
        raise _fail(
            ExitCode.USAGE,
            f"{', '.join(sorted(planned))} raporu henüz desteklenmiyor (Faz 3). "
            "'--format json' kullanın.",
        )

    try:
        config = load_config(config_path) if config_path else YankiConfig()
    except (OSError, ValueError, ValidationError) as exc:
        raise _fail(ExitCode.USAGE, f"Yapılandırma okunamadı: {exc}") from exc

    overrides: dict[str, object] = {}
    if timeout is not None:
        overrides["navigation_timeout_ms"] = int(timeout * 1000)
    if viewport is not None:
        overrides["viewport_width"], overrides["viewport_height"] = _parse_viewport(viewport)
    if locale is not None:
        overrides["locale"] = locale
    if allow_private_network:
        overrides["allow_private_network"] = True
    if overrides:
        config = config.model_copy(update={"scan": config.scan.model_copy(update=overrides)})

    try:
        report = asyncio.run(run_scan(target, config))
    except TargetError as exc:
        raise _fail(ExitCode.USAGE, f"{exc.message} [{exc.code}]") from exc
    except ScanError as exc:
        raise _fail(ExitCode.SCAN_FAILED, f"{exc.message} [{exc.code}]") from exc

    data = report.model_dump_json(indent=2) + "\n"
    if output is None:
        sys.stdout.buffer.write(data.encode("utf-8"))
        sys.stdout.flush()
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(data, encoding="utf-8")
        typer.echo(
            f"{len(report.findings)} bulgu, {len(report.elements)} öğe. Rapor: {output}",
            err=True,
        )


if __name__ == "__main__":  # pragma: no cover
    app()
