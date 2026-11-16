"""Yankı: yapay zeka destekli web erişilebilirlik denetim ve puanlama aracı."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yanki")
except PackageNotFoundError:  # pragma: no cover - kurulmamış kaynak ağacı
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
