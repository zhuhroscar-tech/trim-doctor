"""trim-doctor CLI."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import diagnose_mountpoint, STATUS_OK, STATUS_LUKS_UNDETERMINED, STATUS_DEVICE_UNDETERMINED
from .style import bool_badge, print_fields, resolve_style, status_headline

_LEVEL_BY_STATUS = {
    STATUS_OK: "ok",
    STATUS_LUKS_UNDETERMINED: "warn",
    STATUS_DEVICE_UNDETERMINED: "warn",
}


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
    p.add_argument("--no-color", action="store_true", help="Disable colored output.")
    return p


def _print_text(report, style) -> None:
    level = _LEVEL_BY_STATUS.get(report.status, "fail")
    print(status_headline(style, level, report.mountpoint))
    print(style.dim(report.explanation))

    if report.device:
        rows = [("Device", report.device)]
        rows.append(("Discard support", bool_badge(style, report.device_supports_discard)))
        if report.is_luks or report.is_luks is None:
            rows.append(("LUKS passthrough", bool_badge(style, report.luks_allows_discards)))
        if report.is_lvm or report.is_lvm is None:
            rows.append(("LVM passthrough", bool_badge(style, report.lvm_issue_discards)))
        rows.append(("Mount 'discard' option", bool_badge(style, report.mount_has_discard)))
        rows.append(("fstrim.timer enabled", bool_badge(style, report.fstrim_timer_enabled)))
        print()
        print_fields(rows)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    report = diagnose_mountpoint(args.mountpoint)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        style = resolve_style(no_color_flag=args.no_color)
        _print_text(report, style)

    if report.status == STATUS_OK:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
