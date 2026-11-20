"""Install / inspect the macOS launchd agent that runs `make daily`.

    python ops/launchd.py install [--hour 9 --minute 0]
    python ops/launchd.py status
    python ops/launchd.py uninstall      # unloads; prints the plist path to delete

launchd (unlike cron) runs a missed job once the Mac wakes. Output goes to
logs/daily.log in the v2 directory.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.terrarium.cbb-daily"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = PROJECT_ROOT / "logs" / "daily.log"


def build_plist(hour: int, minute: int) -> dict:
    command = f'cd "{PROJECT_ROOT}" && echo "=== $(date)" && /usr/bin/make daily'
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/zsh", "-c", command],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(hour: int, minute: int) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as fh:
        plistlib.dump(build_plist(hour, minute), fh)
    subprocess.run(["plutil", "-lint", str(PLIST_PATH)], check=True)
    # Reload if already loaded (bootout fails harmlessly when it isn't).
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(PLIST_PATH)], check=True)
    print(f"Installed {LABEL}: `make daily` at {hour:02d}:{minute:02d} local; log {LOG_PATH}")


def status() -> int:
    res = subprocess.run(["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"{LABEL} is not loaded")
        return 1
    keep = ("state =", "last exit code", "runs =", "path =")
    print("\n".join(line.strip() for line in res.stdout.splitlines() if line.strip().startswith(keep)))
    return 0


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=False)
    print(f"Unloaded {LABEL}. Delete {PLIST_PATH} to remove it permanently.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install")
    p.add_argument("--hour", type=int, default=9)
    p.add_argument("--minute", type=int, default=0)
    sub.add_parser("status")
    sub.add_parser("uninstall")
    args = parser.parse_args()
    if args.command == "install":
        install(args.hour, args.minute)
    elif args.command == "status":
        return status()
    else:
        uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
