"""Install / inspect the macOS launchd agents for the daily pipeline.

    python ops/launchd.py install [--hour 9 --minute 0]          # `make daily`
    python ops/launchd.py install --job closing [--times 13:00,18:30]
    python ops/launchd.py status [--job closing]
    python ops/launchd.py uninstall [--job closing]  # unloads; prints the plist path to delete

Jobs:
  * daily   -- `make daily`: ratings, odds + scores, venues, picks, rankings, publish.
  * closing -- `make closing`: odds-only pulls before the afternoon and evening
               tips (3 credits each), so the ledger has real closing lines and CLV.

launchd (unlike cron) runs a missed job once the Mac wakes; a late closing pull
is harmless (lines for games already in progress are ignored). Output goes to
logs/<job>.log in the v2 directory.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOBS = {
    "daily": ("com.terrarium.cbb-daily", "daily", "9:00"),
    "closing": ("com.terrarium.cbb-closing", "closing", "13:00,18:30"),
}
# Back-compat names for the daily job.
LABEL = JOBS["daily"][0]
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = PROJECT_ROOT / "logs" / "daily.log"


def _paths(job: str) -> tuple[str, Path, Path]:
    label = JOBS[job][0]
    return (label, Path.home() / "Library" / "LaunchAgents" / f"{label}.plist",
            PROJECT_ROOT / "logs" / f"{job}.log")


def parse_times(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split(","):
        hour, minute = part.strip().split(":")
        h, m = int(hour), int(minute)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"bad time {part!r}")
        out.append((h, m))
    return out


def build_plist(hour: int | None = None, minute: int | None = None, job: str = "daily",
                times: list[tuple[int, int]] | None = None) -> dict:
    label, _, log_path = _paths(job)
    target = JOBS[job][1]
    times = times or ([(hour, minute)] if hour is not None else parse_times(JOBS[job][2]))
    command = f'cd "{PROJECT_ROOT}" && echo "=== $(date)" && /usr/bin/make {target}'
    intervals = [{"Hour": h, "Minute": m} for h, m in times]
    return {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-c", command],
        "StartCalendarInterval": intervals[0] if len(intervals) == 1 else intervals,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(times: list[tuple[int, int]], job: str = "daily") -> None:
    label, plist_path, log_path = _paths(job)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as fh:
        plistlib.dump(build_plist(job=job, times=times), fh)
    subprocess.run(["plutil", "-lint", str(plist_path)], check=True)
    # Reload if already loaded (bootout fails harmlessly when it isn't).
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(plist_path)], check=True)
    when = ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    print(f"Installed {label}: `make {JOBS[job][1]}` at {when} local; log {log_path}")


def status(job: str = "daily") -> int:
    label = JOBS[job][0]
    res = subprocess.run(["launchctl", "print", f"{_domain()}/{label}"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"{label} is not loaded")
        return 1
    keep = ("state =", "last exit code", "runs =", "path =")
    print("\n".join(line.strip() for line in res.stdout.splitlines() if line.strip().startswith(keep)))
    return 0


def uninstall(job: str = "daily") -> None:
    label, plist_path, _ = _paths(job)
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=False)
    print(f"Unloaded {label}. Delete {plist_path} to remove it permanently.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "status", "uninstall"):
        p = sub.add_parser(name)
        p.add_argument("--job", choices=sorted(JOBS), default="daily")
        if name == "install":
            p.add_argument("--hour", type=int, default=None, help="Single run time (daily job)")
            p.add_argument("--minute", type=int, default=0)
            p.add_argument("--times", default=None, help="Comma-separated HH:MM list, e.g. 13:00,18:30")
    args = parser.parse_args()
    if args.command == "install":
        if args.times:
            times = parse_times(args.times)
        elif args.hour is not None:
            times = [(args.hour, args.minute)]
        else:
            times = parse_times(JOBS[args.job][2])
        install(times, args.job)
    elif args.command == "status":
        return status(args.job)
    else:
        uninstall(args.job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
