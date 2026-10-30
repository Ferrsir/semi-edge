"""Shared, dependency-free helpers for the SEMI-EDGE AI review pipeline.

RESTRICTED PATH (scripts/governance/**): changes require explicit human approval.

Standard library only: these scripts run inside the privileged review workflow from the
TRUSTED base-branch checkout and must never install or import PR-controlled code.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEVERITIES = ("P0", "P1", "P2", "P3")
VERDICTS = ("PASS", "FAIL")
ISSUE_REF = re.compile(r"^#[1-9][0-9]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

MODEL_OUTPUT_KEYS = (
    "verdict",
    "summary",
    "files_inspected",
    "tests_inspected",
    "findings",
    "required_changes",
)
FINDING_KEYS = (
    "id",
    "severity",
    "title",
    "file",
    "line",
    "description",
    "suggested_fix",
    "tracked_issue",
)

SECRET_ENV_VARS = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "DISCORD_WEBHOOK_URL",
)
_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+"),
)
REDACTED = "***REDACTED***"


class PolicyError(ValueError):
    """The review policy file is missing or invalid."""


# --------------------------------------------------------------------------- policy


def load_policy(path: Path) -> dict[str, Any]:
    """Load and validate ``.github/review/policy.json``."""
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read policy {path}: {exc}") from exc
    if not isinstance(policy, dict):
        raise PolicyError("policy must be a JSON object")
    required = {
        "status_context": str,
        "required_reviews": list,
        "blocking_severities": list,
        "p2_requires_tracked_issue": bool,
        "max_review_cycles": int,
        "human_maintainers": list,
        "restricted_paths": list,
    }
    for key, typ in required.items():
        if not isinstance(policy.get(key), typ):
            raise PolicyError(f"policy.{key} missing or not {typ.__name__}")
    roles = [r.get("role") for r in policy["required_reviews"] if isinstance(r, dict)]
    if len(roles) < 2 or len(set(roles)) != len(roles):
        raise PolicyError("policy must require at least two distinct review roles")
    if policy["max_review_cycles"] < 1:
        raise PolicyError("max_review_cycles must be >= 1")
    if not set(policy["blocking_severities"]) >= {"P0", "P1"}:
        raise PolicyError("P0 and P1 must always be blocking")
    return policy


def role_spec(policy: Mapping[str, Any], role: str) -> dict[str, Any]:
    for spec in policy["required_reviews"]:
        if spec.get("role") == role:
            return dict(spec)
    raise PolicyError(f"role {role!r} not defined in policy")


# --------------------------------------------------------------------------- paths


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a repository glob to a regex.

    ``**`` matches any characters including ``/``; ``*`` and ``?`` never cross ``/``.
    Patterns are anchored to the repository root.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def restricted_matches(paths: Iterable[str], patterns: Sequence[str]) -> list[str]:
    """Return the subset of ``paths`` that match any restricted pattern (sorted, unique)."""
    regexes = [glob_to_regex(p) for p in patterns]
    hits = {p for p in paths if any(r.match(p.replace("\\", "/")) for r in regexes)}
    return sorted(hits)


# --------------------------------------------------------------------------- reports


def validate_model_output(obj: Any) -> list[str]:
    """Validate reviewer output against .github/review/review-schema.json (hand-coded)."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["output is not a JSON object"]
    missing = [k for k in MODEL_OUTPUT_KEYS if k not in obj]
    extra = sorted(set(obj) - set(MODEL_OUTPUT_KEYS))
    if missing:
        errors.append(f"missing keys: {missing}")
    if extra:
        errors.append(f"unexpected keys: {extra}")
    if obj.get("verdict") not in VERDICTS:
        errors.append("verdict must be PASS or FAIL")
    if not isinstance(obj.get("summary"), str):
        errors.append("summary must be a string")
    for key in ("files_inspected", "tests_inspected", "required_changes"):
        val = obj.get(key)
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            errors.append(f"{key} must be a list of strings")
    findings = obj.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        return errors
    for n, f in enumerate(findings):
        errors.extend(_validate_finding(n, f))
    return errors


def _validate_finding(n: int, f: Any) -> list[str]:
    if not isinstance(f, dict):
        return [f"findings[{n}] is not an object"]
    errors: list[str] = []
    if set(f) != set(FINDING_KEYS):
        errors.append(f"findings[{n}] keys must be exactly {list(FINDING_KEYS)}")
    if f.get("severity") not in SEVERITIES:
        errors.append(f"findings[{n}].severity invalid")
    line = f.get("line")
    if line is not None and (not isinstance(line, int) or isinstance(line, bool)):
        errors.append(f"findings[{n}].line must be integer or null")
    tracked = f.get("tracked_issue")
    if tracked is not None and not (isinstance(tracked, str) and ISSUE_REF.match(tracked)):
        errors.append(f"findings[{n}].tracked_issue must be null or '#<number>'")
    errors.extend(
        f"findings[{n}].{key} must be a string"
        for key in ("id", "title", "file", "description", "suggested_fix")
        if not isinstance(f.get(key), str)
    )
    return errors


@dataclass(frozen=True)
class VerdictResult:
    verdict: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


def effective_verdict(
    model_output: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
    *,
    error: str | None = None,
    valid_issue_refs: set[str] | None = None,
) -> VerdictResult:
    """Recompute the verdict from findings. The model's own verdict can only make it stricter.

    ``valid_issue_refs``: if given, P2 deferrals must reference one of these open issues.
    """
    if error:
        return VerdictResult("FAIL", (f"review error: {error}",))
    if model_output is None:
        return VerdictResult("FAIL", ("no review output",))
    schema_errors = validate_model_output(model_output)
    if schema_errors:
        return VerdictResult("FAIL", tuple(f"invalid review output: {e}" for e in schema_errors))
    reasons: list[str] = []
    blocking = set(policy["blocking_severities"])
    for f in model_output["findings"]:
        sev = f["severity"]
        if sev in blocking:
            reasons.append(f"blocking {sev}: {f['title']}")
        elif sev == "P2" and policy.get("p2_requires_tracked_issue", True):
            ref = f.get("tracked_issue")
            if not ref:
                reasons.append(f"P2 without tracked follow-up issue: {f['title']}")
            elif valid_issue_refs is not None and ref not in valid_issue_refs:
                reasons.append(f"P2 deferred to {ref}, which is not an open issue: {f['title']}")
    if not model_output["files_inspected"]:
        reasons.append("reviewer inspected no files")
    if model_output["required_changes"]:
        reasons.append("reviewer requires changes")
    if model_output["verdict"] == "FAIL":
        reasons.append("reviewer verdict FAIL")
    return VerdictResult("FAIL" if reasons else "PASS", tuple(reasons))


# --------------------------------------------------------------------------- redaction


def secret_values_from_env(env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [env[k] for k in SECRET_ENV_VARS if len(env.get(k, "")) >= 8]


def redact(value: Any, secrets: Sequence[str]) -> Any:
    """Recursively mask secret values and credential-looking strings."""
    if isinstance(value, str):
        for s in sorted(secrets, key=len, reverse=True):
            value = value.replace(s, REDACTED)
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    return value


def is_sha(value: str) -> bool:
    return bool(SHA_RE.match(value))
