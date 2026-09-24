# Changelog

All notable source-level changes to `trim-doctor` are recorded here.

## v0.1.10 — 2026-09-24

- Added an explicit release-history contract so maintenance releases stay documented.
- Linked the changelog from the English and Chinese READMEs.

## v0.1.9 — 2026-09-23

- Fixed parsing for human-readable `lsblk` discard units such as `4K` and `2G`, so devices with nonzero discard support are not reported as unknown.
- Modernized package license metadata to SPDX-style `MIT` with explicit `LICENSE` inclusion.

## Earlier releases

- Established the read-only TRIM/discard diagnostic CLI, Linux CI, CodeQL scanning, standalone `.pyz` artifact, wheel/sdist builds, bilingual documentation, and regression coverage for LUKS/LVM/mount interpretation.
