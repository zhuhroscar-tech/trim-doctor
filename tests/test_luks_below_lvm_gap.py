"""Regression test proving trim-doctor's LUKS-below-LVM false-positive.

Real, common topology: Ubuntu/Debian's "Use LVM with encryption" installer
option layers LVM ON TOP of a single LUKS-encrypted physical volume (the
whole disk is one LUKS container; the VG/LVs live inside it). This is
extensively documented (Ubuntu server-guide, Debian installer docs, Arch
Wiki "Full disk encryption" article) as the standard/default full-disk
encryption + LVM topology.

is_luks_device() and luks_allows_discards() in the current code only ever
inspect the LOGICAL VOLUME device name returned by findmnt SOURCE (e.g.
/dev/mapper/vg0-root) via `cryptsetup status <lv-name>`. An LV is never
itself a LUKS device -- `cryptsetup status` correctly reports "not active"
for it -- so is_luks_device() returns False, and diagnose_mountpoint()
never checks LUKS discard passthrough at all, even when the LV's own
volume group sits on a LUKS-encrypted PV that does NOT have
`allow-discards` set. The tool then reports STATUS_OK ("trim chain
healthy") while TRIM requests are actually silently dropped by dm-crypt
at the PV layer -- exactly the false-reassurance failure mode this tool
exists to prevent, just one layer further down the stack than the
already-handled "device is directly LUKS" case.
"""
from trim_doctor.core import STATUS_OK, diagnose_mountpoint


def _fake_runner(cmd, timeout=15):
    if cmd[0] == "findmnt" and "SOURCE" in cmd:
        return "/dev/vg0/root\n"
    if cmd[0] == "findmnt" and "OPTIONS" in cmd:
        return "rw,relatime,discard\n"
    if cmd[0] == "lsblk":
        # The kernel correctly reports discard support straight through
        # the whole dm stack (LUKS+LVM), so this layer looks fine on its
        # own -- the bug is specifically about the LUKS check being
        # skipped, not about discard support being misreported. lsblk's
        # NAME column here must match device.split("/")[-1] (i.e. "root",
        # the last path segment of /dev/vg0/root) or device_supports_discard
        # returns None and masks the LUKS gap behind an unrelated
        # "undetermined" verdict instead of exercising the LUKS check.
        return "root 0 512 2147450880\n"
    if cmd[0] == "systemctl":
        return "disabled\n"
    return ""


def _fake_capture_runner(cmd, timeout=15):
    if cmd[0] == "cryptsetup" and cmd[1] == "status":
        # cryptsetup status on the LV name itself: correctly "not a LUKS
        # device", since the LV is not directly a dm-crypt mapping.
        return "", "Device root is not active.\n", 4
    if cmd[0] == "lvs" and "lv_path" in cmd:
        return "  /dev/vg0/root\n", "", 0
    if cmd[0] == "cat" and cmd[-1] == "/etc/lvm/lvm.conf":
        return "devices {\n    issue_discards = 1\n}\n", "", 0
    return "", "", 0


def test_luks_below_lvm_is_not_silently_reported_healthy():
    """The real bug: whole-disk LUKS + LVM on top, PV has no
    allow-discards flag. Current (pre-fix) code has no way to see this
    and reports a false STATUS_OK. This test currently FAILS against
    unmodified core.py (documenting the gap); after the fix it must
    NOT report STATUS_OK for this exact scenario.
    """
    report = diagnose_mountpoint(
        "/", runner=_fake_runner, capture_runner=_fake_capture_runner
    )
    assert report.status != STATUS_OK, (
        "trim-doctor reported a healthy TRIM chain for a mount whose "
        "LVM volume group sits on a LUKS PV with no allow-discards flag "
        "and no crypttab discard option -- the LUKS layer below LVM was "
        "never actually checked."
    )
