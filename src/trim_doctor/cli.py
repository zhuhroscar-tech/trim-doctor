"""trim-doctor CLI."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import diagnose_mountpoint, STATUS_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trim-doctor",
        description=(
            "Verify the full SSD TRIM/discard passthrough chain for a mount "
            "point: device support, LUKS passthrough, LVM passthrough, and "
            "the mount 'discard' option / fstrim.timer. Strictly read-only: "
            "never modifies crypttab, lvm.conf, fstab, or runs fstrim."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("mountpoint", help="Mount point to check, e.g. / or /home")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return p


def _print_text(report) -> None:
    print(f"Mountpoint: {report.mountpoint}")
    print(f"Status: {report.status}")
    print(report.explanation)
    if report.device:
        print(f"\nDevice: {report.device}")
        print(f"  Device supports discard: {report.device_supports_discard}")
        if report.is_luks:
            print(f"  LUKS: yes, allows discards: {report.luks_allows_discards}")
        if report.is_lvm:
            print(f"  LVM: yes, issue_discards enabled: {report.lvm_issue_discards}")
        print(f"  Mount has 'discard' option: {report.mount_has_discard}")
        print(f"  fstrim.timer enabled: {report.fstrim_timer_enabled}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    report = diagnose_mountpoint(args.mountpoint)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_text(report)

    if report.status == STATUS_OK:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
