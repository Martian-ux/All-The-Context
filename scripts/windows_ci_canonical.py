"""PR witness archive encoding; the normal release packager never uses this."""

from __future__ import annotations

import marshal
from types import SimpleNamespace


def install() -> None:
    from PyInstaller.archive import writers

    if writers.marshal is not marshal:
        raise ValueError("unexpected PyInstaller archive writer")
    writers.marshal = SimpleNamespace(
        dumps=lambda value: marshal.dumps(value, 2), loads=marshal.loads
    )


if __name__ == "__main__":
    import PyInstaller.__main__

    install()
    PyInstaller.__main__.run()
