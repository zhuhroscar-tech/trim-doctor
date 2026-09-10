# trim-doctor

Verify the full SSD **TRIM/discard passthrough chain** on Linux in one
command, instead of manually re-deriving it every time you set up a new
LUKS/LVM/SSD combination.

## The problem

Getting `TRIM`/`discard` to actually reach the physical SSD on a Linux
system that uses LVM and/or LUKS requires **every layer in the storage
stack to pass discard requests through** — and any single missing layer
silently breaks the whole chain while every individual command still
"succeeds":

1. **Device support** — does the underlying block device report a nonzero
   `DISC-GRAN`/`DISC-MAX` (`lsblk --discard`)?
2. **LUKS passthrough** — if the device is a LUKS volume, does it have the
   `allow-discards` flag (LUKS2 persistent flag) or `discard` in
   `/etc/crypttab` (LUKS1-style)? Without this, TRIM never reaches the
   device from a dm-crypt mapping at all — by default dm-crypt blocks
   discards for security reasons (they can leak filesystem metadata about
   used space).
3. **LVM passthrough** — if there's an LVM layer, is `issue_discards`
   enabled in `/etc/lvm/lvm.conf`? Without it, LVM does not pass discard
   requests through to the underlying physical volume even though the
   filesystem thinks it can.
4. **Filesystem/mount** — does the mount actually have the `discard` option,
   or is periodic `fstrim.timer` enabled instead? Either is fine, but
   *neither* silently means TRIM never happens.

This four-layer interaction is extensively documented as a manual
diagnostic (Arch Wiki "dm-crypt/Specialties#Discard/TRIM support for solid
state drives", Void Linux docs, and multiple independent blog write-ups
walking through `lsblk --discard` + `cryptsetup luksDump` + `lvm.conf` +
`findmnt` by hand) that has to be redone step-by-step for every new
SSD/LUKS/LVM combination, and it is easy to stop checking after the first
layer that looks fine. `trim-doctor` walks the whole chain for a given
mount point in one command and reports exactly which layer (if any) is
silently blocking TRIM.

**Strictly read-only.** It never edits `/etc/crypttab`, `/etc/lvm/lvm.conf`,
`/etc/fstab`, and never runs `cryptsetup --persistent refresh`, edits
`lvm.conf`, or runs `fstrim` itself. It only calls read-only inspection
commands: `findmnt`, `lsblk`, `cryptsetup status`/`luksDump`, `lvs`,
`systemctl is-enabled`, and reads `/etc/crypttab` / `/etc/lvm/lvm.conf`.

## Install

Requires Python 3.9+ on Linux. No compiled dependencies.

```bash
pip install --user trim-doctor
```

Or run without installing, from a downloaded release asset (no pip/venv
needed — a single self-contained zipapp):

```bash
python3 trim-doctor.pyz / 
```

Or from source:

```bash
git clone https://github.com/zhuhroscar-tech/trim-doctor
cd trim-doctor
pip install --user .
```

## Uninstall

```bash
pip uninstall trim-doctor
```

(or simply delete the downloaded `trim-doctor.pyz` file if you used the
zipapp — it makes no other changes to your system.)

## Usage

```bash
trim-doctor /                # human-readable report for the root mount
trim-doctor /home --json     # machine-readable JSON report
trim-doctor --version
```

Exit code is `0` if the chain is healthy, `2` if a blocking layer was
found, so it's safe to use in scripts/checks.

Example output:

```
Mountpoint: /
Status: luks_blocks_discard
This mount sits on a LUKS-encrypted volume that does not have discard
passthrough enabled. By default dm-crypt blocks discards for security
reasons (they can leak filesystem metadata about used space). Without
`allow-discards` (LUKS2 persistent flag) or `discard` in /etc/crypttab,
TRIM requests never reach the underlying device at all, regardless of
mount options or LVM configuration.

Device: /dev/mapper/root_crypt
  Device supports discard: True
  LUKS: yes, allows discards: False
  Mount has 'discard' option: True
  fstrim.timer enabled: False
```

Reading `cryptsetup luksDump` on a LUKS volume you don't own the passphrase
for may prompt for confirmation on some setups; `trim-doctor` never asks
for or handles passphrases itself, it only reads the on-disk LUKS header
metadata (flags), which does not require unlocking the volume.

## Privacy / permissions

- No network access, no telemetry, no data ever leaves your machine.
- Read-only: inspects `lsblk`, `findmnt`, `cryptsetup status`/`luksDump`,
  `lvs`, `systemctl is-enabled fstrim.timer`, `/etc/crypttab`, and
  `/etc/lvm/lvm.conf`. Never writes to any of these.
- Some commands (`lsblk`, `cryptsetup`, `lvs`) may need to run as root to
  see full device/LVM details depending on your distro's permission setup;
  `trim-doctor` does not escalate privileges itself — run it with `sudo`
  if your system requires that for these commands, or without `sudo` if it
  doesn't (in which case unreachable layers are reported as unknown, not
  guessed).

## Supported distros / architectures

Pure Python, no compiled extensions — works on any Linux distribution and
CPU architecture with Python 3.9+ and the standard `util-linux`
(`lsblk`, `findmnt`), `cryptsetup`, and (optionally) `lvm2` / `systemd`
tools available on the `PATH`. Verified in CI on `ubuntu-latest`
(x86_64) with Python 3.9 and 3.12. Not applicable to non-Linux systems —
`fstrim`/discard-passthrough concepts here are Linux-storage-stack
specific (this tool does nothing useful on macOS/BSD/Windows).

## Development / reproducible build

```bash
git clone https://github.com/zhuhroscar-tech/trim-doctor
cd trim-doctor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pytest -v                       # 32 unit tests, all environment calls mocked
```

Build the distributable artifacts exactly as CI does:

```bash
python -m pip install build
python -m build                 # produces dist/*.whl and dist/*.tar.gz

# standalone zipapp (single file, no pip install needed to run it):
python -m pip install . --target build/pyz_root
python -m zipapp build/pyz_root \
  --main "trim_doctor.cli:main" \
  --output dist/trim-doctor.pyz \
  --python "/usr/bin/env python3"

cd dist && sha256sum * > SHA256SUMS.txt
```

CI (`.github/workflows/ci.yml`) runs the full test suite plus a real smoke
test of the installed console script and the `.pyz` on an `ubuntu-latest`
GitHub Actions runner (actual Linux, not emulated), including running
`trim-doctor / --json` against the runner's real root filesystem.

## Evidence this is a real, recurring pain point (not a gimmick)

- Arch Wiki dm-crypt "Discard/TRIM support for solid state drives" section
  documents the LUKS+LVM+mount three-layer interaction as something users
  must manually verify.
- Multiple independent Linux blog write-ups walk through the exact same
  `lsblk --discard` → `cryptsetup luksDump` → `/etc/lvm/lvm.conf` →
  `findmnt` sequence by hand for a single mount, each time from scratch.
- No existing maintained tool automates this specific cross-layer check;
  `fstrim -v` and `systemctl status fstrim.timer` only tell you whether
  periodic trim *ran*, not whether any layer below is silently discarding
  the discard requests before they reach the device.

## License

MIT — see [LICENSE](LICENSE).
