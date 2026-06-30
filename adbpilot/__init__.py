"""AdbPilot core package."""

__all__ = ["__version__"]

try:
    from ._packaged_version import __version__
except ImportError:
    __version__ = "0.0.3"
