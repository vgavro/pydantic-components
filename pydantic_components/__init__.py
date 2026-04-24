from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydantic-components")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for local dev
