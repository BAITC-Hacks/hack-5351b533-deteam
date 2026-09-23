"""Mark a MixMonitor WAV ready only after Asterisk closes it."""

import os
import re
import sys
from pathlib import Path


MONITOR_DIR = Path("/var/spool/asterisk/monitor")


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", sys.argv[1]):
        print("Invalid recording ID", file=sys.stderr)
        return 2

    call_id = sys.argv[1]
    recording = MONITOR_DIR / f"{call_id}.wav"
    if not recording.is_file():
        print(f"Recording file missing for {call_id}", file=sys.stderr)
        return 1

    temporary = MONITOR_DIR / f".{call_id}.ready.tmp"
    ready = MONITOR_DIR / f"{call_id}.ready"
    temporary.write_text("", encoding="ascii")
    os.replace(temporary, ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
