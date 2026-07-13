#!/usr/bin/env bash

set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: $(basename "$0") [<ref> [<ref2>]] [--] [<path> ...]" \
        "" \
        "Show diff-like output for Git files with Git-LFS content expanded on" \
        "both sides (so LFS-tracked files show real content, not pointers)." \
        "Works on non-LFS files too." \
        "" \
        "Comparison sides (mirrors 'git diff'):" \
        "  (no refs)        index (staged) vs HEAD          [original behavior]" \
        "  <ref>            <ref>          vs working tree   (like 'git diff <ref>')" \
        "  <ref1> <ref2>    <ref1>         vs <ref2>         (like 'git diff <ref1> <ref2>')" \
        "" \
        "If no <path> is given, all files that differ between the two sides are shown." \
        "Use '--' to separate refs from paths when a path could look like a ref." \
        "" \
        "Examples:" \
        "  $(basename "$0") HEAD~ src/models/foo.usda" \
        "  $(basename "$0") HEAD~3 HEAD -- path/to/asset.blend" \
        "  $(basename "$0")                      # staged LFS/non-LFS files vs HEAD"
}

# Expand one side's content for a path into out_file.
# side is one of: WORKTREE | INDEX | <ref>
write_side() {
    local side="$1" path="$2" out="$3"
    case "$side" in
        WORKTREE)
            if [[ -f "$path" ]]; then cp -- "$path" "$out"; else : >"$out"; fi
            ;;
        INDEX)
            if git cat-file -e ":$path" 2>/dev/null; then
                git cat-file --filters ":$path" >"$out"
            else
                : >"$out"
            fi
            ;;
        *)  # a ref/commit
            if git cat-file -e "$side:$path" 2>/dev/null; then
                git cat-file --filters "$side:$path" >"$out"
            else
                : >"$out"
            fi
            ;;
    esac
}

# Human-readable label for a side, used in the diff header.
side_label() {
    case "$1" in
        WORKTREE) printf 'working tree' ;;
        INDEX)    printf 'index' ;;
        *)        printf '%s' "$1" ;;
    esac
}

show_diff() {
    local left="$1" right="$2" path="$3"
    local old_file="$tmp_dir/old" new_file="$tmp_dir/new"
    local status=0

    write_side "$left" "$path" "$old_file"
    write_side "$right" "$path" "$new_file"

    printf '\n=== %s  (%s -> %s) ===\n' "$path" "$(side_label "$left")" "$(side_label "$right")"

    diff -u --label "a/$path" --label "b/$path" "$old_file" "$new_file" || status=$?
    # diff: 0 = same, 1 = differs (incl. "Binary files differ"), >=2 = trouble
    if [[ "$status" -ge 2 ]]; then
        return "$status"
    fi
    return 0
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

orig_cwd="$PWD"
repo_root="$(git rev-parse --show-toplevel)"

# Parse args into refs and paths (auto-detect, with optional '--' separator).
refs=()
paths=()
collecting_refs=1
for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
        collecting_refs=0
        continue
    fi
    if [[ "$collecting_refs" -eq 1 && "${#refs[@]}" -lt 2 \
          && ! -e "$arg" ]] \
       && git rev-parse --verify --quiet "${arg}^{object}" >/dev/null 2>&1; then
        refs+=("$arg")
        continue
    fi
    collecting_refs=0
    paths+=("$arg")
done

# Resolve user-supplied paths (relative to the invoking cwd) to repo-root-relative.
repo_paths=()
for p in "${paths[@]:-}"; do
    [[ -z "$p" ]] && continue
    abs="$(realpath -m -- "$orig_cwd/$p" 2>/dev/null || realpath -m -- "$p")"
    repo_paths+=("${abs#"$repo_root"/}")
done

cd "$repo_root"

# Determine comparison sides.
case "${#refs[@]}" in
    0) left="INDEX"; right="HEAD"
       name_cmd=(git --no-pager diff --cached --name-only -z) ;;
    1) left="${refs[0]}"; right="WORKTREE"
       name_cmd=(git --no-pager diff --name-only -z "${refs[0]}") ;;
    2) left="${refs[0]}"; right="${refs[1]}"
       name_cmd=(git --no-pager diff --name-only -z "${refs[0]}" "${refs[1]}") ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# If no paths given, enumerate everything that differs between the two sides.
if [[ "${#repo_paths[@]}" -eq 0 ]]; then
    while IFS= read -r -d '' p; do
        repo_paths+=("$p")
    done < <("${name_cmd[@]}")
fi

if [[ "${#repo_paths[@]}" -eq 0 ]]; then
    printf 'No differing files found.\n' >&2
    exit 0
fi

for path in "${repo_paths[@]}"; do
    show_diff "$left" "$right" "$path"
done
