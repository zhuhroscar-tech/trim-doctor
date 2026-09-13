from trim_doctor.core import (
    STATUS_DEVICE_UNDETERMINED,
    STATUS_DEVICE_UNSUPPORTED,
    STATUS_LUKS_BLOCKS,
    STATUS_LUKS_UNDETERMINED,
    STATUS_LVM_BLOCKS,
    STATUS_LVM_UNDETERMINED,
    STATUS_NEITHER_DISCARD_NOR_TIMER,
    STATUS_OK,
    _size_to_bytes,
    device_supports_discard,
    diagnose,
    diagnose_mountpoint,
    fstrim_timer_enabled,
    get_source_device,
    is_luks_device,
    is_lvm_device,
    luks_allows_discards,
    lvm_issue_discards_enabled,
    mount_has_discard_option,
    run,
    run_capture,
)


def test_get_source_device():
    def fake_runner(cmd, timeout=15):
        return "/dev/mapper/root_crypt\n"

    assert get_source_device("/", runner=fake_runner) == "/dev/mapper/root_crypt"


def test_get_source_device_none_when_empty():
    def fake_runner(cmd, timeout=15):
        return ""

    assert get_source_device("/", runner=fake_runner) is None


LSBLK_DISCARD_SAMPLE = (
    "nvme0n1 0 0 0\n"
    "nvme0n1p1 0 0 0\n"
    "root_crypt 0 512 2147450880\n"
    "sdb 0 0 0\n"
)


def test_device_supports_discard_true():
    def fake_runner(cmd, timeout=15):
        return LSBLK_DISCARD_SAMPLE

    assert device_supports_discard("/dev/mapper/root_crypt", runner=fake_runner) is True


def test_device_supports_discard_false():
    def fake_runner(cmd, timeout=15):
        return LSBLK_DISCARD_SAMPLE

    assert device_supports_discard("/dev/sdb", runner=fake_runner) is False


def test_device_supports_discard_none_when_not_found():
    def fake_runner(cmd, timeout=15):
        return LSBLK_DISCARD_SAMPLE

    assert device_supports_discard("/dev/nonexistent", runner=fake_runner) is None


def test_is_luks_device_true():
    def fake_capture_runner(cmd, timeout=15):
        return "/dev/mapper/root_crypt is active.\n  type:    LUKS2\n  cipher:  aes-xts-plain64\n", "", 0

    assert is_luks_device("/dev/mapper/root_crypt", runner=fake_capture_runner) is True


def test_is_luks_device_false():
    def fake_capture_runner(cmd, timeout=15):
        return "", "Device /dev/sda1 is not active.\n", 4

    assert is_luks_device("/dev/sda1", runner=fake_capture_runner) is False


def test_is_luks_device_true_with_odd_padding_widths():
    """Regression: real `cryptsetup status` output pads the 'type:' field to
    align with longer labels like 'cipher:'/'keysize:' below it, and the
    padding width varies (1, 2, 3, 4+ spaces observed across cryptsetup
    versions/locales). The old check (`"type:    LUKS" in out` or a single
    "  " -> " " collapse) missed 3-space and 5-space padding, silently
    misreporting a real LUKS device as non-LUKS and skipping the LUKS
    passthrough discard check entirely -- a correctness bug on a
    security-relevant code path."""
    for padding in (1, 2, 3, 4, 5):
        out = f"/dev/mapper/root_crypt is active.\n  type:{' ' * padding}LUKS1\n  cipher:  aes-xts-plain64\n"

        def fake_capture_runner(cmd, timeout=15, _out=out):
            return _out, "", 0

        assert is_luks_device("/dev/mapper/root_crypt", runner=fake_capture_runner) is True, (
            f"failed to detect LUKS with {padding}-space padding"
        )


def test_is_luks_device_false_when_type_is_not_luks():
    def fake_capture_runner(cmd, timeout=15):
        return "/dev/mapper/plain_crypt is active.\n  type:    PLAIN\n", "", 0

    assert is_luks_device("/dev/mapper/plain_crypt", runner=fake_capture_runner) is False


def test_is_luks_device_none_when_permission_denied():
    """Regression: `cryptsetup status` requires root. Running trim-doctor as
    a non-root user must surface an honest 'unknown' instead of silently
    treating a permission error identically to 'definitely not LUKS', which
    would skip the LUKS-blocks-discard check entirely on encrypted disks."""
    def fake_capture_runner(cmd, timeout=15):
        return "", "cryptsetup: Permission denied.\n", 1

    assert is_luks_device("/dev/mapper/root_crypt", runner=fake_capture_runner) is None


def test_luks_allows_discards_via_dump_flag():
    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup" and cmd[1] == "luksDump":
            return "Version:        2\nFlags:           allow-discards\n", "", 0
        return "", "", 0

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_capture_runner) is True


def test_luks_allows_discards_via_crypttab():
    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "Version: 1\n", "", 0
        if cmd[0] == "cat":
            return "root_crypt UUID=xyz none luks,discard\n", "", 0
        return "", "", 0

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_capture_runner) is True


def test_luks_allows_discards_false_when_neither():
    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "Version: 2\nFlags:\n", "", 0
        if cmd[0] == "cat":
            return "root_crypt UUID=xyz none luks\n", "", 0
        return "", "", 0

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_capture_runner) is False


def test_luks_allows_discards_none_when_permission_denied_and_no_crypttab_entry():
    """Same permission-denied class as is_luks_device: luksDump also
    requires root. If it fails on permission and crypttab has no explicit
    entry either, the honest answer is 'unknown', not 'False'."""
    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "", "Cannot access keyslot area of device: Permission denied\n", 1
        if cmd[0] == "cat":
            return "", "", 0
        return "", "", 0

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_capture_runner) is None


def test_is_lvm_device_true():
    def fake_runner(cmd, timeout=15):
        return "  /dev/vg0/root\n  /dev/vg0/home\n", "", 0

    assert is_lvm_device("/dev/vg0/root", runner=fake_runner) is True


def test_is_lvm_device_false():
    def fake_runner(cmd, timeout=15):
        return "  /dev/vg0/root\n", "", 0

    assert is_lvm_device("/dev/mapper/root_crypt", runner=fake_runner) is False


def test_is_lvm_device_none_when_permission_denied():
    """Regression: a permission-denied `lvs` failure previously collapsed
    to an empty string (since is_lvm_device used the stdout-only `run`),
    which made `device.strip() in out` evaluate to False -- silently
    reporting 'this is not LVM' and skipping the LVM discard-passthrough
    check entirely, even though whether LVM is involved was never
    actually determined. Same false-negative bug class as the LUKS
    permission-denied fix above."""
    def fake_runner(cmd, timeout=15):
        return "", "  /dev/vg0/root: Permission denied\n", 5

    assert is_lvm_device("/dev/vg0/root", runner=fake_runner) is None


def test_lvm_issue_discards_enabled_true():
    def fake_runner(cmd, timeout=15):
        return "devices {\n    issue_discards = 1\n}\n", "", 0

    assert lvm_issue_discards_enabled(runner=fake_runner) is True


def test_lvm_issue_discards_enabled_false():
    def fake_runner(cmd, timeout=15):
        return "devices {\n    issue_discards = 0\n}\n", "", 0

    assert lvm_issue_discards_enabled(runner=fake_runner) is False


def test_lvm_issue_discards_default_false_when_absent():
    def fake_runner(cmd, timeout=15):
        return "devices {\n}\n", "", 0

    assert lvm_issue_discards_enabled(runner=fake_runner) is False


def test_lvm_issue_discards_none_when_permission_denied():
    """Regression: reading /etc/lvm/lvm.conf can fail with permission
    denied (e.g. mode 0600 root-only on some distros); the prior
    implementation used the stdout-only `run` helper, which silently
    returned "" for that case, identical to a genuinely absent setting --
    reporting a confirmed 'issue_discards disabled, LVM blocks discard'
    verdict when the truth is 'we could not check'."""
    def fake_runner(cmd, timeout=15):
        return "", "cat: /etc/lvm/lvm.conf: Permission denied\n", 1

    assert lvm_issue_discards_enabled(runner=fake_runner) is None


def test_mount_has_discard_option_true():
    def fake_runner(cmd, timeout=15):
        return "rw,relatime,discard,errors=remount-ro\n"

    assert mount_has_discard_option("/", runner=fake_runner) is True


def test_mount_has_discard_option_false():
    def fake_runner(cmd, timeout=15):
        return "rw,relatime,errors=remount-ro\n"

    assert mount_has_discard_option("/", runner=fake_runner) is False


def test_fstrim_timer_enabled_true():
    def fake_runner(cmd, timeout=15):
        return "enabled\n"

    assert fstrim_timer_enabled(runner=fake_runner) is True


def test_fstrim_timer_enabled_false():
    def fake_runner(cmd, timeout=15):
        return "disabled\n"

    assert fstrim_timer_enabled(runner=fake_runner) is False


def test_diagnose_device_unsupported_takes_priority():
    report = diagnose(
        mountpoint="/", device="/dev/sdb1", device_ok=False,
        is_luks=True, luks_ok=False, is_lvm=True, lvm_ok=False,
        mount_discard=False, timer_enabled=False,
    )
    assert report.status == STATUS_DEVICE_UNSUPPORTED


def test_diagnose_luks_blocks():
    report = diagnose(
        mountpoint="/", device="/dev/mapper/root_crypt", device_ok=True,
        is_luks=True, luks_ok=False, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LUKS_BLOCKS


def test_diagnose_luks_undetermined_when_is_luks_none():
    """Regression: previously is_luks was a plain bool, so a permission
    failure on `cryptsetup status` collapsed to False and the whole LUKS
    layer was skipped -- diagnose() would report the mount OK/healthy even
    though nobody actually checked LUKS discard passthrough."""
    report = diagnose(
        mountpoint="/", device="/dev/mapper/root_crypt", device_ok=True,
        is_luks=None, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LUKS_UNDETERMINED


def test_diagnose_luks_undetermined_when_luks_ok_none_but_is_luks_true():
    """Same bug class, one layer down: is_luks confirmed True (device really
    is LUKS) but luksDump/crypttab could not determine the discard flag."""
    report = diagnose(
        mountpoint="/", device="/dev/mapper/root_crypt", device_ok=True,
        is_luks=True, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LUKS_UNDETERMINED


def test_diagnose_lvm_blocks():
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=True, lvm_ok=False,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LVM_BLOCKS


def test_diagnose_lvm_undetermined_when_is_lvm_none():
    """Regression: previously is_lvm_device/lvm_issue_discards_enabled used
    the stdout-only `run` helper, so a permission-denied `lvs`/lvm.conf
    read collapsed to False -- diagnose() would report the mount OK even
    though whether LVM is even involved, let alone whether it passes
    discards through, was never actually verified."""
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=None, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LVM_UNDETERMINED


def test_diagnose_lvm_undetermined_when_lvm_ok_none_but_is_lvm_true():
    """Same bug class, one layer down: is_lvm confirmed True (device really
    is LVM) but lvm.conf could not be read to determine issue_discards."""
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=True, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LVM_UNDETERMINED


def test_diagnose_neither_discard_nor_timer():
    report = diagnose(
        mountpoint="/", device="/dev/sda1", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=False, timer_enabled=False,
    )
    assert report.status == STATUS_NEITHER_DISCARD_NOR_TIMER


def test_diagnose_ok_via_discard_option():
    report = diagnose(
        mountpoint="/", device="/dev/sda1", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_OK


def test_diagnose_ok_via_timer():
    report = diagnose(
        mountpoint="/", device="/dev/sda1", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=False, timer_enabled=True,
    )
    assert report.status == STATUS_OK


def test_diagnose_device_ok_none_is_undetermined_not_ok():
    """device_supports_discard() returns None when the device couldn't be
    found by name in `lsblk --discard` output (e.g. an odd mapper name or
    an lsblk-unrecognized backing device) -- that must not silently fall
    through to STATUS_OK. Before this fix, diagnose() had no branch for
    device_ok is None, so it fell all the way to the STATUS_OK else-branch
    whenever mount_discard or timer_enabled was true, incorrectly reporting
    the TRIM chain healthy despite never actually confirming the most
    fundamental layer (device-level discard support)."""
    report = diagnose(
        mountpoint="/mnt/data", device="/dev/mapper/oddname", device_ok=None,
        is_luks=False, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_DEVICE_UNDETERMINED
    assert report.status != STATUS_OK


def test_diagnose_device_ok_none_takes_priority_over_luks_undetermined():
    """Device layer is checked first physically -- if device support is
    unknown, that should be reported even if a later layer is also
    undetermined, since the device is the most fundamental blocker."""
    report = diagnose(
        mountpoint="/mnt/data", device="/dev/mapper/oddname", device_ok=None,
        is_luks=None, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_DEVICE_UNDETERMINED


def test_diagnose_mountpoint_device_not_found_in_lsblk_is_undetermined():
    """Integration-level regression: when lsblk's output never mentions the
    resolved device name at all (e.g. it uses a different display name than
    the crypttab/mapper name findmnt reports), device_supports_discard()
    returns None and diagnose_mountpoint() must surface that as genuinely
    undetermined rather than reporting a false-positive healthy chain."""
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "findmnt" and "SOURCE" in cmd:
            return "/dev/mapper/data_crypt\n"
        if cmd[0] == "findmnt" and "OPTIONS" in cmd:
            return "rw,relatime,discard\n"
        if cmd[0] == "lsblk":
            # Note: no line matches "data_crypt" at all.
            return "sda1 0 512 2147450880\n"
        if cmd[0] == "lvs":
            return ""
        if cmd[0] == "systemctl":
            return "disabled\n"
        return ""

    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "", "Device data_crypt is not active.\n", 4
        return "", "", 0

    report = diagnose_mountpoint("/mnt/data", runner=fake_runner, capture_runner=fake_capture_runner)
    assert report.status == STATUS_DEVICE_UNDETERMINED
    assert report.status != STATUS_OK


def test_report_to_dict_roundtrip():
    report = diagnose(
        mountpoint="/", device="/dev/sda1", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=False, lvm_ok=None,
        mount_discard=True, timer_enabled=False,
    )
    d = report.to_dict()
    assert d["status"] == STATUS_OK
    assert d["device"] == "/dev/sda1"


def test_diagnose_mountpoint_integration():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "findmnt" and "SOURCE" in cmd:
            return "/dev/sda1\n"
        if cmd[0] == "findmnt" and "OPTIONS" in cmd:
            return "rw,relatime,discard\n"
        if cmd[0] == "lsblk":
            return "sda1 0 512 2147450880\n"
        if cmd[0] == "lvs":
            return ""
        if cmd[0] == "systemctl":
            return "disabled\n"
        return ""

    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "", "Device /dev/sda1 is not active.\n", 4
        return "", "", 0

    report = diagnose_mountpoint("/", runner=fake_runner, capture_runner=fake_capture_runner)
    assert report.status == STATUS_OK
    assert report.is_luks is False
    assert report.is_lvm is False


def test_diagnose_mountpoint_integration_luks_permission_denied_is_honest():
    """End-to-end regression for the original bug: a LUKS-encrypted mount
    checked as a non-root user must report STATUS_LUKS_UNDETERMINED, not a
    false STATUS_OK. Before the fix, is_luks_device()/luks_allows_discards()
    swallowed the permission-denied cryptsetup failure into a plain False,
    so this exact scenario (mount option is fine, but LUKS discard
    passthrough was never actually checked) silently reported healthy."""
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "findmnt" and "SOURCE" in cmd:
            return "/dev/mapper/root_crypt\n"
        if cmd[0] == "findmnt" and "OPTIONS" in cmd:
            return "rw,relatime,discard\n"
        if cmd[0] == "lsblk":
            return "root_crypt 0 512 2147450880\n"
        if cmd[0] == "lvs":
            return ""
        if cmd[0] == "systemctl":
            return "disabled\n"
        return ""

    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup" and cmd[1] == "status":
            return "", "cryptsetup: Permission denied.\n", 1
        if cmd[0] == "cryptsetup" and cmd[1] == "luksDump":
            return "", "Cannot access keyslot area of device: Permission denied\n", 1
        if cmd[0] == "cat":
            return "", "", 0
        return "", "", 0

    report = diagnose_mountpoint(
        "/home", runner=fake_runner, capture_runner=fake_capture_runner
    )
    assert report.status == STATUS_LUKS_UNDETERMINED
    assert report.is_luks is None


def test_diagnose_mountpoint_integration_lvm_permission_denied_is_honest():
    """End-to-end regression, LVM layer: a mount on an LVM logical volume
    checked as a non-root user (or under a locked-down device-mapper
    policy) must report STATUS_LVM_UNDETERMINED, not a false STATUS_OK.
    Before the fix, is_lvm_device()/lvm_issue_discards_enabled() used the
    stdout-only `run` helper, so a permission-denied `lvs` failure
    silently collapsed to is_lvm=False and the whole LVM discard-
    passthrough check was skipped, reporting healthy without ever having
    checked it."""
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "findmnt" and "SOURCE" in cmd:
            return "/dev/vg0/root\n"
        if cmd[0] == "findmnt" and "OPTIONS" in cmd:
            return "rw,relatime,discard\n"
        if cmd[0] == "lsblk":
            return "root 0 512 2147450880\n"
        if cmd[0] == "systemctl":
            return "disabled\n"
        return ""

    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "", "Device root is not active.\n", 4
        if cmd[0] == "lvs":
            return "", "  /dev/vg0/root: Permission denied\n", 5
        return "", "", 0

    report = diagnose_mountpoint(
        "/mnt/data", runner=fake_runner, capture_runner=fake_capture_runner
    )
    assert report.status == STATUS_LVM_UNDETERMINED
    assert report.is_lvm is None


def test_run_swallows_missing_binary_oserror():
    # A nonexistent command raises OSError (FileNotFoundError) inside
    # subprocess.run; run() must degrade to "" rather than propagate.
    # This exercises the real subprocess.run() call (not a fake runner),
    # closing the coverage gap on run()'s try/except body itself.
    assert run(["/no/such/trim-doctor-binary-xyz"]) == ""


def test_run_swallows_timeout(monkeypatch):
    import subprocess as sp

    def fake_run(cmd, capture_output, text, timeout, check):
        raise sp.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(sp, "run", fake_run)
    assert run(["findmnt"], timeout=1) == ""


def test_run_returns_real_stdout():
    # A real, always-present command exercises the success path of run(),
    # not just the exception branches covered elsewhere.
    assert run(["echo", "hello"]).strip() == "hello"


def test_run_capture_swallows_missing_binary_oserror():
    out, err, rc = run_capture(["/no/such/trim-doctor-binary-xyz"])
    assert out == ""
    assert rc == -1
    assert err  # the OSError string itself, non-empty


def test_run_capture_returns_real_stdout_stderr_rc():
    out, err, rc = run_capture(["echo", "hello"])
    assert out.strip() == "hello"
    assert rc == 0


def test_device_supports_discard_skips_non_matching_lines_before_match():
    # Regression: the loop must `continue` past lines whose NAME doesn't
    # match rather than stopping at the first non-matching line -- this
    # exercises the mismatch branch (line_name != name) explicitly with
    # multiple non-matching entries preceding the real match.
    sample = "sda 0 0 0\nsdb 0 0 0\nnvme0n1 0 512 2147450880\n"

    def fake_runner(cmd, timeout=15):
        return sample

    assert device_supports_discard("/dev/nvme0n1", runner=fake_runner) is True


def test_device_supports_discard_skips_short_malformed_lines():
    # Regression: lsblk -r output can include a short/malformed line
    # (fewer than 4 whitespace-separated fields -- e.g. a truncated or
    # corrupted row) before the real match. The parser must `continue`
    # past it rather than crashing on an index error or matching wrong.
    sample = "weird\nnvme0n1 0 512 2147450880\n"

    def fake_runner(cmd, timeout=15):
        return sample

    assert device_supports_discard("/dev/nvme0n1", runner=fake_runner) is True


def test_size_to_bytes_unparseable_value_returns_none():
    # lsblk -r can print a non-numeric, non-empty token in edge cases
    # (locale or version quirks); _size_to_bytes must report "unknown"
    # (None) rather than raising or silently coercing to zero.
    assert _size_to_bytes("not-a-number") is None


def test_luks_allows_discards_skips_crypttab_comments_and_blank_lines():
    # Regression: comment and blank lines in /etc/crypttab must be
    # skipped (the `continue` branch) rather than being mis-split and
    # potentially matching by accident.
    def fake_capture_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "Version: 1\n", "", 0
        if cmd[0] == "cat":
            return (
                "# this is a comment\n"
                "\n"
                "root_crypt UUID=xyz none luks,discard\n"
            ), "", 0
        return "", "", 0

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_capture_runner) is True


def test_lvm_issue_discards_enabled_skips_comment_lines():
    # Regression: a commented-out issue_discards line (e.g. a disabled
    # example in the default lvm.conf) must not be matched -- only the
    # real, uncommented setting further down should count.
    def fake_runner(cmd, timeout=15):
        return "devices {\n    # issue_discards = 0\n    issue_discards = 1\n}\n", "", 0

    assert lvm_issue_discards_enabled(runner=fake_runner) is True
