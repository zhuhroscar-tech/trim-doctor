from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_required_project_files_exist():
    for relative in [
        "README.md",
        "README.zh-CN.md",
        "CHANGELOG.md",
        "LICENSE",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
    ]:
        assert (ROOT / relative).is_file(), f"Missing required project file: {relative}"


def test_readmes_link_release_history_and_license():
    for relative in ["README.md", "README.zh-CN.md"]:
        text = _read(relative)
        assert "CHANGELOG.md" in text, f"{relative} must link the changelog"
        assert "LICENSE" in text, f"{relative} must link the license"
        assert "https://github.com/zhuhroscar-tech/trim-doctor/releases" in text


def test_changelog_documents_current_release():
    pyproject = _read("pyproject.toml")
    version_line = next(line for line in pyproject.splitlines() if line.startswith("version = "))
    version = version_line.split('"')[1]
    changelog = _read("CHANGELOG.md")
    assert f"## v{version}" in changelog
    assert "lsblk" in changelog
    assert "discard" in changelog


def test_ci_keeps_tests_and_release_artifacts_covered():
    ci = _read(".github/workflows/ci.yml")
    assert "python -m pytest" in ci
    assert "python -m build" in ci
    assert "zipapp" in ci
    assert "SHA256SUMS.txt" in ci
    codeql = _read(".github/workflows/codeql.yml")
    assert "github/codeql-action/init" in codeql
    assert "languages: python" in codeql
