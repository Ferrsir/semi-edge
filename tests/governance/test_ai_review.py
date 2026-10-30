from __future__ import annotations

import json
import subprocess
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


@pytest.mark.parametrize("cfg", [".claude/settings.json", ".codex/config.toml"])
def test_refuses_pr_with_reviewer_config(repo: tuple[Path, str, str], cfg: str) -> None:
    r, base, _ = repo
    (r / cfg).parent.mkdir(parents=True, exist_ok=True)
    (r / cfg).write_text("{}")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "inject")
    head = _git(r, "rev-parse", "HEAD")
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "FAIL"
    assert "reviewer config" in rep["error"]


def test_workspace_modification_discards_review(
    repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_review

    r, base, head = repo

    def fake_codex(prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, str]]:
        (r / "a.py").write_text("tampered\n")
        return {"verdict": "PASS"}, {"cli": "x", "model": "m"}

    monkeypatch.setattr(ai_review, "run_codex", fake_codex)
    rep = run_review(_args(r, base, head))
    assert rep["effective_verdict"] == "FAIL"
    assert "modified the workspace" in rep["error"]


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
