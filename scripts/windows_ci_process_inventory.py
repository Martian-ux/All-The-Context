"""Query the local Windows process inventory for the packaged CI smoke."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path


def creation_ticks(stamp: str) -> str:
    """Convert WMI's offset-aware timestamp to UTC .NET ticks."""
    offset = timedelta(minutes=int(stamp[21:25]))
    observed = datetime.strptime(stamp[:21], "%Y%m%d%H%M%S.%f").replace(tzinfo=timezone(offset))
    elapsed = observed - datetime(1, 1, 1, tzinfo=UTC)
    return str((elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + elapsed.microseconds * 10)


def main() -> int:
    from win32com.client import Dispatch

    target = Path(os.environ["ATC_SMOKE_PROCESS_PATH"]).resolve()
    if target.name.casefold() != "allthecontext.exe":
        return 2
    normalized = os.path.normcase(str(target))
    server = Dispatch("WbemScripting.SWbemLocator").ConnectServer(".", r"root\cimv2")
    rows = []
    for process in server.ExecQuery(
        "SELECT ProcessId,CreationDate,ExecutablePath FROM Win32_Process "
        "WHERE Name = 'AllTheContext.exe'",
        "WQL",
        48,
    ):
        executable = process.ExecutablePath
        if isinstance(executable, str) and os.path.normcase(executable) == normalized:
            rows.append(
                {
                    "pid": int(process.ProcessId),
                    "creation_identity": creation_ticks(process.CreationDate),
                    "exe": executable,
                }
            )
            if len(rows) > 64:
                return 2
    print(json.dumps(rows, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
