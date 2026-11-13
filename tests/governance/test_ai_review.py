from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
from ai_review import build_prompt, render_markdown, run_review
from review_common import REDACTED, glob_to_regex, redact, validate_model_output

pytestmark = pytest.mark.governance
ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "base")
    base = _git(r, "rev-parse", "HEAD")
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-qam", "head")
    return r, base, _git(r, "rev-parse", "HEAD")


def _args(repo: Path, base: str, head: str, **kw: Any) -> Namespace:
    ns = Namespace(
        role="A",
        repo_dir=str(repo),
        base=base,
        head=head,
        pr=7,
        repository="o/r",
        out="unused",
        provider=None,
        model=None,
        execution="local",
        codex_sandbox="read-only",
        timeout=30,
        trusted_root=str(ROOT),
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_head_mismatch_is_an_error_and_fails(repo: tuple[Path, str, str]) -> None:
    r, base, _head = repo
    rep = run_review(_args(r, base, base))
    assert rep["effective_verdict"] == "FAIL"
    assert "!=" in rep["error"]


def test_non_sha_inputs_rejected(repo: tuple[Path, str, str]) -> None:
    r, _base, head = repo
    rep = run_review(_args(r, "main", head))
    assert rep["effective_verdict"] == "FAIL"
    assert "40-character" in rep["error"]


def _passing_output() -> dict[str, Any]:
    return {
        "verdict": "PASS",
        "summary": "ok",
        "files_inspected": ["a.py"],
        "tests_inspected": [],
        "findings": [],
        "required_changes": [],
    }


REVIEWER_CONFIG = [
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".codex/config.toml",
    ".mcp.json",
    "nested/.claude/settings.json",
]


@pytest.mark.parametrize("modify", [False, True])
def test_pr_with_reviewer_config_is_reviewed_without_exposing_it(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch, modify: bool
) -> None:
    """main contains .claude/settings.json, so every PR head does. The PR must still be
    reviewed, its config changes must be in the prompt, and the reviewer must never see it."""
    import ai_review

    r, base, _ = repo
    for cfg in REVIEWER_CONFIG:
        (r / cfg).parent.mkdir(parents=True, exist_ok=True)
        (r / cfg).write_text('{"permissions": {"allow": ["Bash"]}}\n')
    _git(r, "add", ".")
    if modify:  # config already on the base; the PR modifies it
        _git(r, "commit", "-qm", "config on base")
        base = _git(r, "rev-parse", "HEAD")
        (r / ".claude/settings.json").write_text('{"permissions": {"allow": ["Write"]}}\n')
        _git(r, "commit", "-qam", "modify config")
    else:
        _git(r, "commit", "-qm", "add config")
    head = _git(r, "rev-parse", "HEAD")
    seen: dict[str, Any] = {}

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        tree: Path = kw["repo"]
        seen["tree"] = tree
        seen["prompt"] = prompt
        seen["files"] = sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*"))
        return _passing_output(), {"cli": "x", "model": "m"}

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "PASS", rep
    tree = seen["tree"]
    assert tree.resolve() != r.resolve()
    assert "a.py" in seen["files"]
    assert not any(
        part.lower() in {".git", ".claude", ".codex", ".mcp.json"}
        for f in seen["files"]
        for part in f.split("/")
    )
    assert "+++ b/.claude/settings.json" in seen["prompt"]
    assert "- .claude/settings.json" in seen["prompt"].split("### Changed files", 1)[1]
    assert not tree.exists()  # the copy is removed after the review
    assert (r / ".claude/settings.json").is_file()  # the checkout itself is untouched


def test_symlinks_are_not_copied_into_review_tree(
    repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_review

    r, base, _ = repo
    host_file = tmp_path / "host-secret.txt"
    host_file.write_text("HOST FILE\n")
    host_dir = tmp_path / "host-dir"
    host_dir.mkdir()
    (host_dir / "inner.txt").write_text("HOST DIR\n")
    try:
        (r / "link-file").symlink_to(host_file)
        (r / "link-dir").symlink_to(host_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform/account")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "links")
    head = _git(r, "rev-parse", "HEAD")
    seen: dict[str, list[str]] = {}

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        tree: Path = kw["repo"]
        seen["files"] = sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*"))
        return _passing_output(), {"cli": "x", "model": "m"}

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "PASS", rep
    assert seen["files"] == ["a.py"]


def test_sanitized_copy_skips_links_and_excluded_names(tmp_path: Path) -> None:
    from ai_review import sanitized_copy

    src = tmp_path / "src"
    (src / "pkg" / ".CLAUDE").mkdir(parents=True)
    (src / "pkg" / ".CLAUDE" / "settings.json").write_text("{}")
    (src / "pkg" / "m.py").write_text("y = 1\n")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n")
    (src / ".mcp.json").write_text("{}")
    outside = tmp_path / "outside.txt"
    outside.write_text("host\n")
    expected = {".git", ".mcp.json", "pkg/.CLAUDE"}
    try:
        (src / "pkg" / "evil").symlink_to(outside)
        expected.add("pkg/evil")
    except OSError:
        pass
    if sys.platform == "win32":  # junctions need no privilege and must be skipped too
        import _winapi

        host_dir = tmp_path / "host-dir"
        host_dir.mkdir()
        (host_dir / "inner.txt").write_text("host\n")
        _winapi.CreateJunction(str(host_dir), str(src / "junction"))
        expected.add("junction")
    dest = tmp_path / "dest"
    skipped = sanitized_copy(src, dest)
    copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*"))
    assert copied == ["pkg", "pkg/m.py"]
    assert set(skipped) == expected


@pytest.mark.parametrize("where", ["copy-modify", "copy-add", "copy-delete", "checkout"])
def test_workspace_modification_discards_review(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    import ai_review

    r, base, head = repo

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        tree: Path = kw["repo"]
        if where == "copy-modify":
            (tree / "a.py").write_text("tampered\n")
        elif where == "copy-add":
            (tree / ".claude").mkdir()
            (tree / ".claude" / "settings.json").write_text("{}")
        elif where == "copy-delete":
            (tree / "a.py").unlink()
        else:
            (r / "a.py").write_text("tampered\n")
        return _passing_output(), {"cli": "x", "model": "m"}

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "FAIL"
    assert "modified the workspace" in rep["error"]
    assert rep["error_kind"] == "review"
    assert rep["model_output"] is None


def test_missing_reviewer_cli_is_an_infrastructure_error(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_review

    r, base, head = repo
    monkeypatch.setattr(ai_review.shutil, "which", lambda name: None)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "FAIL"
    assert rep["error_kind"] == "infrastructure"
    assert "not installed" in rep["error"]


def test_error_subcommand_marks_infrastructure(tmp_path: Path) -> None:
    from ai_review import main

    out = tmp_path / "review-a.json"
    argv = ["error", "--role", "A", "--base", "a" * 40, "--head", "b" * 40, "--pr", "1"]
    argv += ["--repository", "o/r", "--out", str(out), "--trusted-root", str(ROOT)]
    argv += ["--message", "reviewer not configured: repository secret OPENAI_API_KEY is missing"]
    assert main(argv) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["effective_verdict"] == "FAIL"
    assert rep["error_kind"] == "infrastructure"


def test_valid_review_passes_and_secrets_are_redacted(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_review

    r, base, head = repo
    secret = "sk-proj-THISISAFAKEKEYVALUE1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    captured: dict[str, str] = {}

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        captured["prompt"] = prompt
        out = {
            "verdict": "PASS",
            "summary": f"leaked {secret}",
            "files_inspected": ["a.py"],
            "tests_inspected": [],
            "findings": [],
            "required_changes": [],
        }
        return out, {"cli": "codex-cli test", "model": "m1"}

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "PASS", rep
    assert rep["head_sha"] == head
    assert secret not in json.dumps(rep)
    assert REDACTED in rep["model_output"]["summary"]
    assert "-x = 1" in captured["prompt"]
    assert "+x = 2" in captured["prompt"]
    assert "<untrusted_diff>" in captured["prompt"]
    md = render_markdown(rep)
    assert "PASS" in md
    assert head in md


def test_ci_execution_forbids_writable_codex_sandbox() -> None:
    from ai_review import main

    with pytest.raises(SystemExit):
        main(
            [
                "run",
                "--role",
                "A",
                "--repo-dir",
                ".",
                "--base",
                "a" * 40,
                "--head",
                "b" * 40,
                "--pr",
                "1",
                "--repository",
                "o/r",
                "--out",
                "x.json",
                "--execution",
                "github-actions",
                "--codex-sandbox",
                "workspace-write",
            ]
        )


def test_build_prompt_truncates_large_diffs() -> None:
    prompt = build_prompt(
        "ROLE",
        role="A",
        role_name="n",
        repository="o/r",
        pr=1,
        base="a" * 40,
        head="b" * 40,
        changed_files=["x"],
        diff="y" * 400_000,
        repo_dir=Path("."),
    )
    assert "truncated" in prompt
    assert len(prompt) < 360_000


def test_validate_model_output_rejects_extra_and_bad_types() -> None:
    assert validate_model_output([]) == ["output is not a JSON object"]
    errs = validate_model_output({"verdict": "MAYBE", "extra": 1})
    assert any("missing keys" in e for e in errs)
    assert any("unexpected keys" in e for e in errs)
    assert any("verdict" in e for e in errs)


def test_redact_patterns() -> None:
    text = "token ghp_abcdefghijklmnopqrstuvwxyz0123 and sk-ant-api03-abcdefghijkl"
    out = redact({"k": [text]}, [])
    assert "ghp_" not in out["k"][0]
    assert "sk-ant" not in out["k"][0]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        (".github/**", ".github/workflows/ci.yml", True),
        (".github/**", "x/.github/a", False),
        ("src/semiedge/safety/**", "src/semiedge/safety/broker.py", True),
        ("src/semiedge/safety/**", "src/semiedge/safety_extra.py", False),
        ("AGENTS.md", "AGENTS.md", True),
        ("AGENTS.md", "docs/AGENTS.md", False),
        ("docs/*.md", "docs/a/b.md", False),
        ("**/secret.txt", "a/b/secret.txt", True),
        ("**/secret.txt", "secret.txt", True),
    ],
)
def test_glob_to_regex(pattern: str, path: str, expected: bool) -> None:
    assert bool(glob_to_regex(pattern).match(path)) is expected


def test_truncated_diff_hiding_excluded_config_fails_closed(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-077: padding the diff must not push an excluded-config change out of review."""
    import ai_review

    r, base, _ = repo
    monkeypatch.setattr(ai_review, "MAX_DIFF_CHARS", 200)
    (r / "a_padding.py").write_text("x = 1\n" * 200)
    (r / "z" / ".claude").mkdir(parents=True)
    (r / "z" / ".claude" / "settings.json").write_text('{"permissions": {}}')
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "pad then config")
    head = _git(r, "rev-parse", "HEAD")

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        pytest.fail("the reviewer must not run when excluded changes cannot be shown")

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "FAIL"
    assert "truncated" in rep["error"]


def test_truncated_diff_without_excluded_config_still_reviews(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_review

    r, base, _ = repo
    monkeypatch.setattr(ai_review, "MAX_DIFF_CHARS", 200)
    (r / "a_padding.py").write_text("x = 1\n" * 200)
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "large ordinary change")
    head = _git(r, "rev-parse", "HEAD")
    calls: list[str] = []

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        calls.append(prompt)
        return {"verdict": "PASS"}, {"cli": "x", "model": "m"}

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    run_review(_args(r, base, head))
    assert calls
    assert "truncated" in calls[0]
