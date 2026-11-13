#!/usr/bin/env bash
# RESTRICTED (.github/**): changes require explicit human approval.
#
# Classifies the files changed by this event so workflows can skip expensive jobs on
# documentation-only changes. FAIL CLOSED: any doubt (unknown event, missing or unreachable
# base commit, git error, empty diff) reports every category as changed, so everything runs.
#
# Inputs (environment, never interpolated into the script by the workflow):
#   EVENT_NAME  github.event_name
#   BASE_SHA    pull_request: github.event.pull_request.base.sha; push: github.event.before
#   HEAD_SHA    pull_request: github.event.pull_request.head.sha; push: github.sha
# Outputs (appended to $GITHUB_OUTPUT, each "true" or "false"):
#   code       anything other than documentation changed (runs lint/types/tests/build/safety)
#   deps       pyproject.toml, uv.lock or .github/** changed (runs the dependency audit)
#   workflows  .github/** changed (runs the workflow linter)
#   dashboard  project-status/** or .github/** changed (runs the dashboard DOM tests)
#
# "Documentation" is deliberately narrow: Markdown files outside docs/specifications/ and
# outside .github/, plus assets/**. Specifications are read by tests (contract checks), so a
# spec change is a code change here. Tests that read non-spec Markdown run in the docs-check
# job of ci.yml, which runs on every event.
set -uo pipefail

out="${GITHUB_OUTPUT:-/dev/stdout}"

emit() { # code deps workflows dashboard reason
  {
    echo "code=$1"
    echo "deps=$2"
    echo "workflows=$3"
    echo "dashboard=$4"
  } >>"$out"
  echo "classification: code=$1 deps=$2 workflows=$3 dashboard=$4 ($5)"
  exit 0
}

all() { emit true true true true "$1"; }

case "${EVENT_NAME:-}" in
  pull_request) range_sep="..." ;; # merge-base diff: only what the PR changes
  push) range_sep=".." ;;
  *) all "event '${EVENT_NAME:-}' is not classified" ;;
esac

base="${BASE_SHA:-}"
head="${HEAD_SHA:-}"
sha_re='^[0-9a-f]{40}$'
[[ "$base" =~ $sha_re && "$head" =~ $sha_re ]] || all "base or head is not a full commit SHA"
[[ "$base" =~ ^0+$ ]] && all "new branch: no base commit"
git cat-file -e "${base}^{commit}" 2>/dev/null || all "base commit not in the checkout"
git cat-file -e "${head}^{commit}" 2>/dev/null || all "head commit not in the checkout"

if ! files="$(git diff --name-only --no-renames "${base}${range_sep}${head}")"; then
  all "git diff failed"
fi
[[ -n "$files" ]] || all "empty diff"

code=false deps=false workflows=false dashboard=false
while IFS= read -r f; do
  case "$f" in
    .github/*) code=true deps=true workflows=true dashboard=true ;;
    pyproject.toml | uv.lock) code=true deps=true ;;
    project-status/*) code=true dashboard=true ;;
    docs/specifications/*) code=true ;;
    assets/* | *.md) ;;
    *) code=true ;;
  esac
done <<<"$files"

emit "$code" "$deps" "$workflows" "$dashboard" "$(wc -l <<<"$files" | tr -d ' ') files"
