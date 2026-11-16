"""Komut satırı (``yanki scan``) testleri: JSON çıktısı (RAP-01) ve çıkış kodları
(TAR-05, CLI-03'ün Faz 0 kısmı: 0 / 2 / 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yanki.cli import ExitCode, app
from yanki.core.schema import ScanReport

from conftest import PAGES, LocalServer

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("yanki ")


@pytest.mark.browser
def test_scan_writes_valid_json_report(tmp_path: Path) -> None:
    out = tmp_path / "rapor" / "sonuc.json"
    result = runner.invoke(app, ["scan", str(PAGES / "rules_violations.html"), "-o", str(out)])
    assert result.exit_code == ExitCode.OK, result.output
    report = ScanReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert len([f for f in report.findings if f.type == "rule"]) == 5
    assert report.findings[0].id == "f-0001"
    # Sayfa kaynaklı Türkçe metin bozulmadan yazılır.
    assert "Öne çıkanlar" in out.read_text(encoding="utf-8")


@pytest.mark.browser
def test_scan_to_stdout_is_json() -> None:
    result = runner.invoke(app, ["scan", str(PAGES / "clean.html")])
    assert result.exit_code == ExitCode.OK
    data = json.loads(result.stdout)
    assert data["findings"] == []
    assert data["schema_version"]


@pytest.mark.parametrize(
    "args",
    [
        ["scan", "ftp://ornek.gov.tr"],
        ["scan", "yok-boyle-bir-dosya.html"],
        ["scan", "http://localhost/"],
        ["scan", "http://10.0.0.1/"],
        ["scan", "x.html", "--format", "docx"],
        ["scan", "x.html", "--viewport", "genis"],
    ],
)
def test_usage_errors_exit_2(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == ExitCode.USAGE, result.output


def test_planned_formats_explain_phase() -> None:
    result = runner.invoke(app, ["scan", str(PAGES / "clean.html"), "--format", "json,pdf"])
    assert result.exit_code == ExitCode.USAGE
    assert "Faz 3" in result.output


def test_bad_config_file_exit_2(tmp_path: Path) -> None:
    cfg = tmp_path / "yanki.toml"
    cfg.write_text("[scan]\nbilinmeyen = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(PAGES / "clean.html"), "--config", str(cfg)])
    assert result.exit_code == ExitCode.USAGE


@pytest.mark.browser
def test_unreachable_page_exit_3(http_server: LocalServer) -> None:
    result = runner.invoke(
        app,
        ["scan", http_server.url("/status/404"), "--allow-private-network"],
    )
    assert result.exit_code == ExitCode.SCAN_FAILED
    assert "http_error" in result.output


@pytest.mark.browser
def test_timeout_option(http_server: LocalServer) -> None:
    result = runner.invoke(
        app,
        ["scan", http_server.url("/slow"), "--allow-private-network", "--timeout", "1"],
    )
    assert result.exit_code == ExitCode.SCAN_FAILED
    assert "navigation_timeout" in result.output
