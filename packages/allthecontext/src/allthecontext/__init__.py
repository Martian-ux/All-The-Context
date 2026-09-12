"""All The Context: a user-owned portable context system."""

from .build_identity import PRODUCT_VERSION

__version__ = "0.1.0-beta.7"
if __version__ != PRODUCT_VERSION:
    raise RuntimeError("runtime version and canonical build identity disagree")
