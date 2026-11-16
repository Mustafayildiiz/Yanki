"""Hedef ayrıştırma (TAR-01, NFR-08) ve yapılandırma (PUA-02, NFR-13) testleri."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from yanki.config import YankiConfig, load_config
from yanki.core.errors import TargetError
from yanki.core.target import parse_target


@pytest.mark.parametrize(
    ("raw", "host", "port"),
    [
        ("https://ornek.gov.tr/", "ornek.gov.tr", 443),
        ("http://ornek.gov.tr:8080/a?b=1", "ornek.gov.tr", 8080),
        ("HTTPS://Ornek.gov.tr", "ornek.gov.tr", 443),
        ("http://[2001:db8::1]/", "2001:db8::1", 80),
    ],
)
def test_http_targets(raw: str, host: str, port: int) -> None:
    t = parse_target(raw)
    assert t.kind == "url"
    assert t.host == host
    assert t.port == port


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://ornek.gov.tr",
        "javascript:alert(1)",
        "data:text/html,<p>x</p>",
        "chrome://settings",
        "ws://ornek.gov.tr",
    ],
)
def test_other_schemes_rejected(raw: str) -> None:
    with pytest.raises(TargetError) as exc:
        parse_target(raw)
    assert exc.value.code == "unsupported_scheme"


@pytest.mark.parametrize("raw", ["", "   ", "http://", "http://:80/"])
def test_invalid_targets(raw: str) -> None:
    with pytest.raises(TargetError):
        parse_target(raw)


def test_local_file_path_and_file_uri(tmp_path: Path) -> None:
    page = tmp_path / "sayfa ğüş.html"
    page.write_text("<html lang='tr'></html>", encoding="utf-8")
    by_path = parse_target(str(page))
    by_uri = parse_target(page.as_uri())
    assert by_path.kind == by_uri.kind == "file"
    assert by_path.url == by_uri.url == page.resolve().as_uri()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TargetError) as exc:
        parse_target(str(tmp_path / "yok.html"))
    assert exc.value.code == "file_not_found"


def test_remote_file_uri_rejected() -> None:
    with pytest.raises(TargetError):
        parse_target("file://sunucu/paylasim/a.html")


def test_default_config_matches_srs_initial_values() -> None:
    cfg = YankiConfig()
    assert cfg.scoring.severity_weights == {
        "critical": 1.0,
        "important": 0.6,
        "moderate": 0.3,
        "low": 0.1,
    }
    assert cfg.scoring.module_weights == {"rule": 0.30, "alt_text": 0.30, "keyboard": 0.40}
    assert cfg.scoring.critical_cap == 60
    assert cfg.scan.allow_private_network is False
    assert cfg.device == "cpu"


def test_load_config_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "yanki.toml"
    path.write_text(
        "seed = 7\n[scan]\nnavigation_timeout_ms = 5000\nlocale = 'en-US'\n"
        "[rules]\naxe_tags = ['wcag2a']\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.seed == 7
    assert cfg.scan.navigation_timeout_ms == 5000
    assert cfg.scan.locale == "en-US"
    assert cfg.rules.axe_tags == ["wcag2a"]
    assert cfg.scan.viewport_width == 1280  # verilmeyen değer varsayılan kalır


def test_config_typos_rejected(tmp_path: Path) -> None:
    path = tmp_path / "yanki.toml"
    path.write_text("[scan]\nnavigation_timeot_ms = 5\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_severity_map_must_cover_all_impacts() -> None:
    with pytest.raises(ValidationError):
        YankiConfig.model_validate({"rules": {"severity_map": {"critical": "critical"}}})
