from trim_doctor.core import (
    STATUS_DEVICE_UNSUPPORTED,
    STATUS_LUKS_BLOCKS,
    STATUS_LVM_BLOCKS,
    STATUS_NEITHER_DISCARD_NOR_TIMER,
    STATUS_OK,
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
    def fake_runner(cmd, timeout=15):
        return "/dev/mapper/root_crypt is active.\n  type:    LUKS2\n  cipher:  aes-xts-plain64\n"

    assert is_luks_device("/dev/mapper/root_crypt", runner=fake_runner) is True


def test_is_luks_device_false():
    def fake_runner(cmd, timeout=15):
        return "Command failed"

    assert is_luks_device("/dev/sda1", runner=fake_runner) is False


def test_luks_allows_discards_via_dump_flag():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup" and cmd[1] == "luksDump":
            return "Version:        2\nFlags:           allow-discards\n"
        return ""

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_runner) is True


def test_luks_allows_discards_via_crypttab():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "Version: 1\n"
        if cmd[0] == "cat":
            return "root_crypt UUID=xyz none luks,discard\n"
        return ""

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_runner) is True


def test_luks_allows_discards_false_when_neither():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "cryptsetup":
            return "Version: 2\nFlags:\n"
        if cmd[0] == "cat":
            return "root_crypt UUID=xyz none luks\n"
        return ""

    assert luks_allows_discards("/dev/mapper/root_crypt", runner=fake_runner) is False


def test_is_lvm_device_true():
    def fake_runner(cmd, timeout=15):
        return "  /dev/vg0/root\n  /dev/vg0/home\n"

    assert is_lvm_device("/dev/vg0/root", runner=fake_runner) is True


def test_is_lvm_device_false():
    def fake_runner(cmd, timeout=15):
        return "  /dev/vg0/root\n"

    assert is_lvm_device("/dev/mapper/root_crypt", runner=fake_runner) is False


def test_lvm_issue_discards_enabled_true():
    def fake_runner(cmd, timeout=15):
        return "devices {\n    issue_discards = 1\n}\n"

    assert lvm_issue_discards_enabled(runner=fake_runner) is True


def test_lvm_issue_discards_enabled_false():
    def fake_runner(cmd, timeout=15):
        return "devices {\n    issue_discards = 0\n}\n"

    assert lvm_issue_discards_enabled(runner=fake_runner) is False


def test_lvm_issue_discards_default_false_when_absent():
    def fake_runner(cmd, timeout=15):
        return "devices {\n}\n"

    assert lvm_issue_discards_enabled(runner=fake_runner) is False


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


def test_diagnose_lvm_blocks():
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=True, lvm_ok=False,
        mount_discard=True, timer_enabled=False,
    )
    assert report.status == STATUS_LVM_BLOCKS


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
        if cmd[0] == "cryptsetup":
            return ""
        if cmd[0] == "lvs":
            return ""
        if cmd[0] == "systemctl":
            return "disabled\n"
        return ""

    report = diagnose_mountpoint("/", runner=fake_runner)
    assert report.status == STATUS_OK
    assert report.is_luks is False
    assert report.is_lvm is False
