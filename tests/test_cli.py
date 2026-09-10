import json

import pytest

from trim_doctor.cli import main
from trim_doctor.core import TrimChainReport, STATUS_LUKS_BLOCKS, STATUS_OK


def _fake_report(status=STATUS_LUKS_BLOCKS):
    return TrimChainReport(
        mountpoint="/", status=status, explanation="example explanation",
        device="/dev/mapper/root_crypt", device_supports_discard=True,
        is_luks=True, luks_allows_discards=False,
        is_lvm=False, lvm_issue_discards=None,
        mount_has_discard=True, fstrim_timer_enabled=False,
    )


def test_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "trim-doctor" in capsys.readouterr().out


def test_text_output(monkeypatch, capsys):
    monkeypatch.setattr("trim_doctor.cli.diagnose_mountpoint", lambda mountpoint: _fake_report())
    rc = main(["/"])
    out = capsys.readouterr().out
    assert "example explanation" in out
    assert "LUKS passthrough" in out
    assert "no" in out
    assert rc == 2


def test_json_output(monkeypatch, capsys):
    monkeypatch.setattr("trim_doctor.cli.diagnose_mountpoint", lambda mountpoint: _fake_report())
    rc = main(["/", "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == STATUS_LUKS_BLOCKS
    assert rc == 2


def test_ok_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        "trim_doctor.cli.diagnose_mountpoint",
        lambda mountpoint: TrimChainReport(mountpoint="/", status=STATUS_OK, explanation="fine"),
    )
    rc = main(["/"])
    assert rc == 0


def test_mountpoint_passed_through(monkeypatch):
    captured = {}

    def fake_diagnose(mountpoint):
        captured["mountpoint"] = mountpoint
        return TrimChainReport(mountpoint=mountpoint, status=STATUS_OK, explanation="fine")

    monkeypatch.setattr("trim_doctor.cli.diagnose_mountpoint", fake_diagnose)
    main(["/home"])
    assert captured["mountpoint"] == "/home"


def test_no_color_flag_disables_ansi(monkeypatch, capsys):
    monkeypatch.setattr("trim_doctor.cli.diagnose_mountpoint", lambda mountpoint: _fake_report())
    main(["/", "--no-color"])
    out = capsys.readouterr().out
    assert "\033[" not in out
