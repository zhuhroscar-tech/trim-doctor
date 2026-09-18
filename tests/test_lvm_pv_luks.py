"""Unit tests for the LVM-physical-volume-LUKS check (whole-disk
encryption with LVM layered on top -- see test_luks_below_lvm_gap.py for
the original end-to-end regression this closes).
"""
from trim_doctor.core import (
    STATUS_LVM_PV_LUKS_BLOCKS,
    STATUS_LVM_PV_LUKS_UNDETERMINED,
    STATUS_OK,
    diagnose,
    get_lvm_vg_name,
    get_vg_pv_devices,
    lvm_pv_luks_discard_status,
)


def test_get_lvm_vg_name_found():
    def fake_runner(cmd, timeout=15):
        return "  /dev/vg0/root vg0\n  /dev/vg0/home vg0\n", "", 0

    assert get_lvm_vg_name("/dev/vg0/root", runner=fake_runner) == "vg0"


def test_get_lvm_vg_name_not_found():
    def fake_runner(cmd, timeout=15):
        return "  /dev/vg0/root vg0\n", "", 0

    assert get_lvm_vg_name("/dev/vg1/data", runner=fake_runner) is None


def test_get_lvm_vg_name_none_when_permission_denied():
    def fake_runner(cmd, timeout=15):
        return "", "  /dev/vg0/root: Permission denied\n", 5

    assert get_lvm_vg_name("/dev/vg0/root", runner=fake_runner) is None


def test_get_vg_pv_devices_found():
    def fake_runner(cmd, timeout=15):
        return "  /dev/sda2 vg0\n  /dev/sdb1 vg1\n", "", 0

    assert get_vg_pv_devices("vg0", runner=fake_runner) == ["/dev/sda2"]


def test_get_vg_pv_devices_empty_but_determined():
    def fake_runner(cmd, timeout=15):
        return "  /dev/sdb1 vg1\n", "", 0

    assert get_vg_pv_devices("vg0", runner=fake_runner) == []


def test_get_vg_pv_devices_none_when_permission_denied():
    def fake_runner(cmd, timeout=15):
        return "", "  Permission denied\n", 5

    assert get_vg_pv_devices("vg0", runner=fake_runner) is None


def test_lvm_pv_luks_discard_status_true_when_no_luks_pv():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "  /dev/sda2 vg0\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "status":
            return "", "Device sda2 is not active.\n", 4
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is True


def test_lvm_pv_luks_discard_status_false_when_pv_luks_blocks():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "  /dev/mapper/whole_disk_crypt vg0\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "status":
            return "  type:    LUKS2\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "luksDump":
            return "Flags:     \n", "", 0
        if cmd[0] == "cat" and cmd[-1] == "/etc/crypttab":
            return "", "", 0
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is False


def test_lvm_pv_luks_discard_status_true_when_pv_luks_allows_discards():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "  /dev/mapper/whole_disk_crypt vg0\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "status":
            return "  type:    LUKS2\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "luksDump":
            return "Flags:     allow-discards\n", "", 0
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is True


def test_lvm_pv_luks_discard_status_none_when_vg_lookup_fails():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "", "Permission denied\n", 5
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is None


def test_lvm_pv_luks_discard_status_none_when_pv_lookup_fails():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "", "Permission denied\n", 5
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is None


def test_lvm_pv_luks_discard_status_none_when_pv_luks_check_undetermined():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "  /dev/mapper/whole_disk_crypt vg0\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "status":
            return "", "Permission denied\n", 5
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is None


def test_lvm_pv_luks_discard_status_true_when_pv_list_empty():
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "  /dev/sdb1 vg1\n", "", 0
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is True


def test_lvm_pv_luks_discard_status_none_when_one_pv_luks_flag_undetermined_and_no_pv_blocks():
    """Two PVs in the VG: the first is a non-LUKS PV (nothing to check),
    the second is LUKS but whose allow-discards flag can't be read
    (luksDump/crypttab check fails for a privilege reason). No PV
    actively confirmed to block, but the result must still be
    undetermined, not a false True."""
    def fake_runner(cmd, timeout=15):
        if cmd[0] == "lvs":
            return "  /dev/vg0/root vg0\n", "", 0
        if cmd[0] == "pvs":
            return "  /dev/sda1 vg0\n  /dev/mapper/whole_disk_crypt vg0\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "status" and "sda1" in cmd[2]:
            return "", "not a valid device\n", 1
        if cmd[0] == "cryptsetup" and cmd[1] == "status":
            return "  type:    LUKS2\n", "", 0
        if cmd[0] == "cryptsetup" and cmd[1] == "luksDump":
            return "", "Permission denied\n", 1
        if cmd[0] == "cat" and cmd[-1] == "/etc/crypttab":
            return "", "", 0
        return "", "", 0

    assert lvm_pv_luks_discard_status("/dev/vg0/root", runner=fake_runner) is None


def test_diagnose_lvm_pv_luks_blocks():
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=True, lvm_ok=True,
        mount_discard=True, timer_enabled=False, lvm_pv_luks_ok=False,
    )
    assert report.status == STATUS_LVM_PV_LUKS_BLOCKS


def test_diagnose_lvm_pv_luks_undetermined():
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=True, lvm_ok=True,
        mount_discard=True, timer_enabled=False, lvm_pv_luks_ok=None,
    )
    assert report.status == STATUS_LVM_PV_LUKS_UNDETERMINED


def test_diagnose_lvm_pv_luks_ok_reaches_status_ok():
    report = diagnose(
        mountpoint="/", device="/dev/vg0/root", device_ok=True,
        is_luks=False, luks_ok=None, is_lvm=True, lvm_ok=True,
        mount_discard=True, timer_enabled=False, lvm_pv_luks_ok=True,
    )
    assert report.status == STATUS_OK
