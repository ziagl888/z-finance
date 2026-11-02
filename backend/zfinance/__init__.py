"""z-finance: local personal finance and liquidity tool (FastAPI backend + bundled React SPA)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("z-finance")
except PackageNotFoundError:  # running from a source tree without an installed distribution
    __version__ = "0.0.0+unknown"
