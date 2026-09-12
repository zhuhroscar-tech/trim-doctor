"""Core logic for trim-doctor.

The problem: getting SSD TRIM/discard to actually reach the physical
device on a Linux system with LVM and/or LUKS in the storage stack
requires every layer to pass discard requests through -- and any single
missing layer silently breaks the whole chain while every individual
command still "succeeds":

  1. Device support   -- does the underlying block device report
                          nonzero DISC-GRAN/DISC-MAX (`lsblk --discard`)?
  2. LUKS passthrough  -- if the device is a LUKS volume, does it have
                          the `allow-discards` flag (persistent, LUKS2)
                          or `discard` in /etc/crypttab (LUKS1-style)?
                          Without this, TRIM never reaches the device
                          from a dm-crypt mapping at all.
  3. LVM passthrough   -- if there's an LVM layer, is `issue_discards`
                          enabled in `/etc/lvm/lvm.conf`? Without this,
                          LVM commands don't pass discards through even
                          though the filesystem thinks it can.
  4. Filesystem/mount  -- does the mount actually have `discard` (or is
                          periodic `fstrim.timer` enabled instead --
                          either is fine, but *neither* silently means
                          TRIM never happens)?

This is extensively documented (Arch Wiki, Void Linux docs, multiple
personal blogs) as a multi-step manual diagnostic that has to be redone
by hand for every new SSD/LUKS/LVM combination. This tool walks the
whole chain for a given mount point in one command and reports exactly
which layer (if any) is silently blocking TRIM.

Strictly read-only: it never edits /etc/crypttab, /etc/lvm/lvm.conf,
/etc/fstab, or runs `cryptsetup --persistent refresh`, `lvm.conf`
edits, or `fstrim` itself.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional


LAYER_DEVICE = "device"
LAYER_LUKS = "luks"
LAYER_LVM = "lvm"
LAYER_MOUNT = "mount_or_timer"

STATUS_OK = "trim_chain_healthy"
STATUS_DEVICE_UNSUPPORTED = "device_does_not_support_discard"
STATUS_LUKS_BLOCKS = "luks_blocks_discard"
STATUS_LVM_BLOCKS = "lvm_blocks_discard"
STATUS_NEITHER_DISCARD_NOR_TIMER = "no_discard_mount_option_and_no_fstrim_timer"
STATUS_LUKS_UNDETERMINED = "luks_status_could_not_be_determined"
STATUS_DEVICE_UNDETERMINED = "device_discard_support_could_not_be_determined"

STATUS_EXPLANATIONS = {
    STATUS_OK: (
        "The full TRIM/discard chain appears intact for this mount: the "
        "underlying device supports discard, any LUKS/LVM layers pass it "
        "through, and either the 'discard' mount option is set or "
        "fstrim.timer is enabled for periodic trimming."
    ),
    STATUS_DEVICE_UNSUPPORTED: (
        "The underlying block device reports no discard support (DISC-GRAN "
        "and DISC-MAX are both 0 in `lsblk --discard`). No amount of LUKS/LVM/"
        "mount configuration will make TRIM work if the device itself doesn't "
        "support it -- confirm this is expected (e.g. a spinning HDD, or an "
        "external enclosure that doesn't pass through UNMAP/TRIM)."
    ),
    STATUS_LUKS_BLOCKS: (
        "This mount sits on a LUKS-encrypted volume that does not have "
        "discard passthrough enabled. By default dm-crypt blocks discards "
        "for security reasons (they can leak filesystem metadata about used "
        "space). Without `allow-discards` (LUKS2 persistent flag) or "
        "`discard` in /etc/crypttab, TRIM requests never reach the "
        "underlying device at all, regardless of mount options or LVM "
        "configuration."
    ),
    STATUS_LVM_BLOCKS: (
        "This mount sits on an LVM logical volume, but `issue_discards` is "
        "not enabled in /etc/lvm/lvm.conf. Without it, LVM does not pass "
        "discard requests through to the underlying physical volume even "
        "though the filesystem and mount options may be configured "
        "correctly."
    ),
    STATUS_NEITHER_DISCARD_NOR_TIMER: (
        "Neither the 'discard' mount option is set for this filesystem nor "
        "is fstrim.timer enabled. TRIM will never actually run for this "
        "mount -- data is never actively discarded, even though every "
        "layer below (device/LUKS/LVM) may support it fine."
    ),
    STATUS_LUKS_UNDETERMINED: (
        "This mount sits on a LUKS-encrypted volume, but whether discard "
        "passthrough is enabled could not be determined -- `cryptsetup "
        "luksDump` or `status` failed, most likely because this check "
        "requires root/CAP_SYS_ADMIN and this process isn't running as "
        "root. Re-run with sudo to get a real answer instead of an "
        "assumed pass or fail on this critical layer."
    ),
    STATUS_DEVICE_UNDETERMINED: (
        "Whether the underlying block device supports discard could not "
        "be determined -- the device did not appear by name in `lsblk "
        "--discard` output (e.g. an unusual mapper name, a device lsblk "
        "doesn't recognize, or a filesystem not backed by a single named "
        "block device). This is the most fundamental layer in the TRIM "
        "chain; without a real answer here the rest of the chain cannot "
        "be trusted even if it reports healthy."
    ),
}

def run(cmd: list, timeout: int = 15) -> str:
    """Run a read-only subprocess command, returning stdout (empty on error)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


_PERMISSION_DENIED_RE = re.compile(
    r"permission denied|must be superuser|must be root|requires? (?:root|superuser)|not permitted",
    re.IGNORECASE,
)


def run_capture(cmd: list, timeout: int = 15):
    """Run a read-only subprocess command, returning (stdout, stderr, returncode).

    Unlike run(), this preserves stderr/exit status so callers can tell a
    genuinely empty/negative result apart from a command that failed because
    it needs elevated privileges (e.g. `cryptsetup status` as non-root)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout or "", result.stderr or "", result.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc), -1


def _is_permission_denied(stderr: str, returncode: int) -> bool:
    """True if a command's failure looks like a privilege/permission problem
    rather than a genuine 'no such thing exists' answer."""
    if returncode == 0:
        return False
    return bool(_PERMISSION_DENIED_RE.search(stderr))


def get_source_device(mountpoint: str, runner=run) -> Optional[str]:
    """Return the underlying device/mapper path for a mountpoint via findmnt."""
    out = runner(["findmnt", "-n", "-o", "SOURCE", mountpoint])
    source = out.strip()
    return source or None


_LSBLK_DISCARD_RE = re.compile(r"^(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$")


def device_supports_discard(device: str, runner=run) -> Optional[bool]:
    """Parse `lsblk --discard` for a device/mapper name, returning True if
    DISC-GRAN or DISC-MAX is nonzero, False if both are zero, None if the
    device couldn't be found in the output."""
    name = device.split("/")[-1]
    out = runner(["lsblk", "-no", "NAME,DISC-ALN,DISC-GRAN,DISC-MAX", "-r"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        line_name = parts[0].lstrip("├└│─ ")
        if line_name != name:
            continue
        disc_gran, disc_max = parts[2], parts[3]
        gran_val = _size_to_bytes(disc_gran)
        max_val = _size_to_bytes(disc_max)
        return bool((gran_val or 0) > 0 or (max_val or 0) > 0)
    return None


def _size_to_bytes(value: str) -> Optional[int]:
    value = value.strip()
    if not value or value == "0":
        return 0
    try:
        return int(value)
    except ValueError:
        return None  # lsblk -r sometimes prints raw bytes; unparseable = unknown


def is_luks_device(device: str, runner=run_capture) -> Optional[bool]:
    """Return True if `cryptsetup status` reports this device as LUKS,
    False if it clearly is not, or None if the check itself could not be
    performed (e.g. `cryptsetup status` requires root and this process
    isn't -- it exits non-zero with a permission-denied message on stderr
    rather than printing a real answer)."""
    out, err, rc = runner(["cryptsetup", "status", device.split("/")[-1]])
    if _is_permission_denied(err, rc):
        return None
    # cryptsetup pads the "type:" field to align with longer labels like
    # "cipher:"/"keysize:" below it; the padding width varies by version,
    # locale, and terminal width (1, 2, 3, 4+ spaces have all been observed
    # in the wild). A fixed-width substring check or a single left-to-right
    # "  " -> " " collapse (which does not fully normalize odd counts, e.g.
    # 3 spaces collapses to 2, not 1) can silently miss a real LUKS device
    # and report it as non-LUKS, skipping the LUKS passthrough check below.
    return bool(re.search(r"^\s*type:\s*LUKS", out, re.MULTILINE | re.IGNORECASE))


def luks_allows_discards(device: str, runner=run_capture) -> Optional[bool]:
    """Check for the LUKS2 persistent 'allow-discards' flag via luksDump,
    or a 'discard' option for this mapping in /etc/crypttab. Returns None
    if luksDump itself could not be run (typically a permission problem --
    luksDump requires root) and crypttab has no matching, explicit entry
    either, since in that case we genuinely don't know the answer."""
    dump_out, dump_err, dump_rc = runner(["cryptsetup", "luksDump", device])
    dump_failed_permission = _is_permission_denied(dump_err, dump_rc)
    if not dump_failed_permission and re.search(r"^\s*Flags:\s*.*allow-discards", dump_out, re.MULTILINE):
        return True
    crypttab_out, _, _ = runner(["cat", "/etc/crypttab"])
    mapper_name = device.split("/")[-1]
    for line in crypttab_out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if fields and fields[0] == mapper_name:
            options = fields[3] if len(fields) > 3 else ""
            if "discard" in options.split(","):
                return True
    if dump_failed_permission:
        return None
    return False


def is_lvm_device(device: str, runner=run) -> bool:
    out = runner(["lvs", "--noheadings", "-o", "lv_path"])
    return device.strip() in out


def lvm_issue_discards_enabled(runner=run) -> bool:
    conf = runner(["cat", "/etc/lvm/lvm.conf"])
    for line in conf.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.match(r"issue_discards\s*=\s*(\d+)", stripped)
        if m:
            return m.group(1) == "1"
    return False


def mount_has_discard_option(mountpoint: str, runner=run) -> bool:
    out = runner(["findmnt", "-n", "-o", "OPTIONS", mountpoint])
    options = out.strip().split(",")
    return "discard" in options


def fstrim_timer_enabled(runner=run) -> bool:
    out = runner(["systemctl", "is-enabled", "fstrim.timer"])
    return out.strip() == "enabled"


@dataclass
class TrimChainReport:
    mountpoint: str
    status: str
    explanation: str
    device: Optional[str] = None
    device_supports_discard: Optional[bool] = None
    is_luks: Optional[bool] = False
    luks_allows_discards: Optional[bool] = None
    is_lvm: bool = False
    lvm_issue_discards: Optional[bool] = None
    mount_has_discard: Optional[bool] = None
    fstrim_timer_enabled: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "mountpoint": self.mountpoint,
            "status": self.status,
            "explanation": self.explanation,
            "device": self.device,
            "device_supports_discard": self.device_supports_discard,
            "is_luks": self.is_luks,
            "luks_allows_discards": self.luks_allows_discards,
            "is_lvm": self.is_lvm,
            "lvm_issue_discards": self.lvm_issue_discards,
            "mount_has_discard": self.mount_has_discard,
            "fstrim_timer_enabled": self.fstrim_timer_enabled,
        }


def diagnose(
    mountpoint: str,
    device: Optional[str],
    device_ok: Optional[bool],
    is_luks: Optional[bool],
    luks_ok: Optional[bool],
    is_lvm: bool,
    lvm_ok: Optional[bool],
    mount_discard: Optional[bool],
    timer_enabled: Optional[bool],
) -> TrimChainReport:
    """Walk the chain and return the first blocking layer, in physical
    order (device, then LUKS, then LVM, then mount/timer) since a lower
    layer blocking makes everything above it moot."""
    if device_ok is False:
        status = STATUS_DEVICE_UNSUPPORTED
    elif device_ok is None:
        status = STATUS_DEVICE_UNDETERMINED
    elif is_luks is None:
        status = STATUS_LUKS_UNDETERMINED
    elif is_luks and luks_ok is False:
        status = STATUS_LUKS_BLOCKS
    elif is_luks and luks_ok is None:
        status = STATUS_LUKS_UNDETERMINED
    elif is_lvm and lvm_ok is False:
        status = STATUS_LVM_BLOCKS
    elif not mount_discard and not timer_enabled:
        status = STATUS_NEITHER_DISCARD_NOR_TIMER
    else:
        status = STATUS_OK

    return TrimChainReport(
        mountpoint=mountpoint,
        status=status,
        explanation=STATUS_EXPLANATIONS[status],
        device=device,
        device_supports_discard=device_ok,
        is_luks=is_luks,
        luks_allows_discards=luks_ok,
        is_lvm=is_lvm,
        lvm_issue_discards=lvm_ok,
        mount_has_discard=mount_discard,
        fstrim_timer_enabled=timer_enabled,
    )


def diagnose_mountpoint(mountpoint: str, runner=run, capture_runner=run_capture) -> TrimChainReport:
    device = get_source_device(mountpoint, runner=runner)

    is_luks = is_luks_device(device, runner=capture_runner) if device else False
    luks_ok = luks_allows_discards(device, runner=capture_runner) if (is_luks and device) else None

    is_lvm = bool(device) and is_lvm_device(device, runner=runner) if device else False
    lvm_ok = lvm_issue_discards_enabled(runner=runner) if is_lvm else None

    device_ok = device_supports_discard(device, runner=runner) if device else None
    mount_discard = mount_has_discard_option(mountpoint, runner=runner)
    timer_enabled = fstrim_timer_enabled(runner=runner)

    return diagnose(
        mountpoint=mountpoint,
        device=device,
        device_ok=device_ok,
        is_luks=is_luks,
        luks_ok=luks_ok,
        is_lvm=is_lvm,
        lvm_ok=lvm_ok,
        mount_discard=mount_discard,
        timer_enabled=timer_enabled,
    )
