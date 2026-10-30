from __future__ import annotations

import subprocess
from argparse import Namespace
from pathlib import Path

import pytest
from ai_review import ReviewError, run_claude, run_review

pytestmark = pytest.mark.governance
ROOT = Path(__file__).resolve().parents[2]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("kind", ["modified", "untracked", "ignored"])
def test_dirty_checkout_is_rejected_before_reviewer(tmp_path: Path, kind: str) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("ignored\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    head = git(tmp_path, "rev-parse", "HEAD")
    filename = {"modified": "a.py", "untracked": "extra", "ignored": "ignored"}[kind]
    (tmp_path / filename).write_text("unexpected content\n")
    args = Namespace(
        trusted_root=str(ROOT),
        role="A",
        provider=None,
        repo_dir=str(tmp_path),
        execution="local",
        repository="o/r",
        pr=33,
        base=head,
        head=head,
    )
    report = run_review(args)
    assert report["effective_verdict"] == "FAIL"
    assert "must be clean" in report["error"]


def test_claude_oauth_only_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token")
    with pytest.raises(ReviewError, match="requires ANTHROPIC_API_KEY"):
        run_claude("review", repo=tmp_path, schema_path=tmp_path / "schema", model=None, timeout=1)
