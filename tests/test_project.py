"""Proje düzeyinde değişmezler: mimari kural, sürüm sabitleme ve gömülü axe-core
(NFR-15, KUR-03, CLAUDE.md "altın kural")."""

from __future__ import annotations

import ast
import hashlib
import re
from importlib import resources
from importlib.metadata import version
from pathlib import Path

import pytest

from yanki.core.rules import AXE_SHA256, AXE_VERSION

ROOT = Path(__file__).resolve().parents[1]
INTERFACE_LIBS = {"typer", "click", "fastapi", "starlette", "streamlit", "uvicorn"}


def test_core_does_not_import_interfaces() -> None:
    offenders: list[str] = []
    for file in (ROOT / "yanki" / "core").rglob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in INTERFACE_LIBS or name.startswith(("yanki.cli", "yanki.api", "yanki.ui")):
                    offenders.append(f"{file.relative_to(ROOT)}: {name}")
    assert offenders == []


def test_vendored_axe_matches_pinned_hash_and_version() -> None:
    data = resources.files("yanki.core.vendor").joinpath("axe.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == AXE_SHA256
    assert data.startswith(f"/*! axe v{AXE_VERSION}".encode())


def test_playwright_package_matches_docker_base_image() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"mcr\.microsoft\.com/playwright/python:v([\d.]+)-", dockerfile)
    assert m, "Dockerfile'da Playwright taban imajı bulunamadı"
    assert version("playwright") == m.group(1)


def test_lock_file_pins_playwright_and_has_hashes() -> None:
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert re.search(r"^playwright==1\.65\.2 ", lock, re.MULTILINE)
    assert "--hash=sha256:" in lock


@pytest.mark.parametrize("lock_name", ["requirements.lock", "requirements-ml.lock"])
def test_every_locked_package_has_a_license_entry(lock_name: str) -> None:
    lock = (ROOT / lock_name).read_text(encoding="utf-8")
    locked = {m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", lock, re.MULTILINE)}
    licenses = (ROOT / "docs" / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8").lower()
    missing = sorted(p for p in locked if f"| {p} |" not in licenses)
    assert missing == [], f"Lisans listesinde eksik paketler: {missing}"
    assert "| axe-core |" in licenses


def test_base_lock_has_no_ml_packages() -> None:
    """Native CI işleri (Windows, macOS, Linux) torch kurmaz; ML yalnızca ml kilidinde."""
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert not re.search(r"^(torch|transformers)==", lock, re.MULTILINE)


def test_ml_lock_is_linux_cpu_only_and_hashed() -> None:
    """ML kilidi: torch CPU indeksinden, CUDA paketi yok, yalnızca Linux (NFR-12, NFR-15)."""
    lock = (ROOT / "requirements-ml.lock").read_text(encoding="utf-8")
    assert lock.startswith("--extra-index-url https://download.pytorch.org/whl/cpu\n")
    assert re.search(r"^torch==[\d.]+\+cpu ; sys_platform == 'linux'", lock, re.MULTILINE)
    assert re.search(r"^playwright==1\.65\.2 ", lock, re.MULTILINE)
    assert not re.search(r"^(nvidia-|triton)", lock, re.MULTILINE)
    assert "sys_platform == 'darwin'" not in lock and "sys_platform == 'win32'" not in lock
    assert "--hash=sha256:" in lock


def test_docker_image_installs_ml_lock() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install -r requirements-ml.lock" in dockerfile
