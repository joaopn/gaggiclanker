#!/usr/bin/env bash
#
# Run exactly the gates a branch owes, and nothing it does not:
#
#   scripts/gates.sh                   # against origin/dev
#   scripts/gates.sh --base <ref>      # a stacked branch: diff against its parent
#   scripts/gates.sh --dry-run         # print the changed files and the plan
#   scripts/gates.sh --keep-going      # run every gate, report all failures
#   scripts/gates.sh --sim             # also run the firmware simulator suite
#
#   GATES_EXTRA="<command>" scripts/gates.sh
#                                      # an extra command to run before the gates,
#                                      # e.g. a local policy check
#
# What a branch owes follows from what it changed: the files in
# `git diff <base>...HEAD`, plus anything uncommitted (staged, unstaged or
# untracked), because the gates are run before a push and the push is what
# they protect.
#
#   gaggiclanker/, tests/, pyproject.toml, uv.lock
#       ruff check, ruff format --check, mypy, the Python suite, then
#       `npm run gen:api`, which must not change web/src/api/schema.d.ts
#   web/ (a regenerated schema.d.ts included), except its Markdown
#       npm run check, npm run build
#   gaggiclanker/{device,sync,drafts,notes,cleanup}/, scripts/sim*, tests/simulator/
#       scripts/sim.sh test -- about three minutes, so reported as owed and
#       run only with --sim
#   anything else (docs, prompt YAML, scripts outside the simulator)
#       ruff format --check on the changed Python files, if there are any
#
# pyproject.toml and uv.lock count as back end: they carry the dependencies and
# the ruff, mypy and pytest configuration, so a change to them can break any of
# those gates without a single .py file moving. Markdown under web/ does not
# count as web: Biome ignores it and neither tsc nor Vite reads it, so a README
# edit there is a docs change. Markdown under gaggiclanker/ does count: it is
# the knowledge seed, which the application loads and the suite tests.
#
# Every gate prints its duration and the run ends with a summary. The script
# stops at the first failure unless --keep-going is given; either way it exits
# non-zero if any gate failed.
#
# The web gates need `npm` on PATH (Node 22) and web/node_modules installed
# (`npm ci` in web/). The script does not look for Node anywhere else and does
# not install anything: a missing tool is a failed gate with a message saying
# which one.
#
# GATES_EXTRA, when set, is a shell command run from the repository root before
# any other gate. It appears in the plan and the summary as "extra" and fails
# the run like any other gate. It exists for checks a particular checkout wants
# that the repository itself does not carry.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SCHEMA="web/src/api/schema.d.ts"

extra="${GATES_EXTRA:-}"

die() { echo "gates.sh: $*" >&2; exit 2; }

usage() {
    sed -n '3,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

base="origin/dev"
dry_run=0
keep_going=0
with_sim=0
while (($# > 0)); do
    case "$1" in
        --base)
            [[ $# -ge 2 ]] || die "--base needs a ref"
            base="$2"
            shift 2
            ;;
        --base=*) base="${1#--base=}"; shift ;;
        --dry-run) dry_run=1; shift ;;
        --keep-going) keep_going=1; shift ;;
        --sim) with_sim=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done

git rev-parse --verify --quiet "$base^{commit}" >/dev/null ||
    die "no such ref: $base (fetch it, or pass --base <ref>)"
merge_base="$(git merge-base "$base" HEAD)" ||
    die "$base and HEAD share no history"

# ── what changed ─────────────────────────────────────────────────────────────

# --no-renames so a moved file counts at both ends: moving a module out of
# gaggiclanker/ still owes the back-end gates. quotepath off so a non-ASCII
# name arrives as itself rather than as an escaped string.
changed=()
while IFS= read -r path; do
    [[ -n "$path" ]] && changed+=("$path")
done < <(
    {
        git -c core.quotepath=off diff --name-only --no-renames "$merge_base" HEAD
        git -c core.quotepath=off diff --name-only --no-renames HEAD
        git -c core.quotepath=off ls-files --others --exclude-standard
    } | sort -u
)

owes_backend=0
owes_web=0
owes_sim=0
python_files=()
for path in "${changed[@]}"; do
    case "$path" in
        gaggiclanker/* | tests/* | pyproject.toml | uv.lock) owes_backend=1 ;;
    esac
    case "$path" in
        web/*.md) ;;
        web/*) owes_web=1 ;;
    esac
    case "$path" in
        gaggiclanker/device/* | gaggiclanker/sync/* | gaggiclanker/drafts/* | \
            gaggiclanker/notes/* | gaggiclanker/cleanup/* | scripts/sim* | tests/simulator/*)
            owes_sim=1
            ;;
    esac
    # A deleted file has nothing left to format.
    if [[ "$path" == *.py && -f "$path" ]]; then
        python_files+=("$path")
    fi
done

# ── the gates ────────────────────────────────────────────────────────────────
#
# Each is a function so it can run in its own directory and be timed as one
# step. They are called as an `if` condition, where `set -e` does not apply, so
# a gate with more than one command chains them explicitly.

need_npm() {
    if ! command -v npm >/dev/null 2>&1; then
        echo "gates.sh: npm is not on PATH. The web gates and gen:api need Node 22:" >&2
        echo "gates.sh: install it, or put the bin directory of an existing one on PATH." >&2
        return 1
    fi
    if [[ ! -d web/node_modules ]]; then
        echo "gates.sh: web/node_modules is missing; run \`npm ci\` in web/ first." >&2
        return 1
    fi
}

gate_extra() { bash -c "$extra"; }
gate_ruff_check() { uv run ruff check .; }
gate_ruff_format() { uv run ruff format --check .; }
gate_ruff_format_files() { uv run ruff format --check -- "${python_files[@]}"; }
gate_mypy() { uv run mypy; }
gate_pytest() { uv run pytest; }
gate_web_check() { need_npm && (cd web && npm run check); }
gate_web_build() { need_npm && (cd web && npm run build); }
gate_sim() { scripts/sim.sh test; }

# The generated schema is committed, so regenerating it from the back end must
# be a no-op. Compared by content rather than by `git status`, because a branch
# being gated before its commit is dirty anyway; what matters is whether the
# generator disagrees with the file the branch is about to push.
gate_gen_api() {
    need_npm || return 1
    local before after
    before="$(git hash-object "$SCHEMA")"
    (cd web && npm run gen:api) || return 1
    after="$(git hash-object "$SCHEMA")"
    if [[ "$before" != "$after" ]]; then
        echo "gates.sh: npm run gen:api changed $SCHEMA; the file does not match the API:" >&2
        git --no-pager diff -- "$SCHEMA" >&2 || true
        echo "gates.sh: commit the regenerated file, which also makes the web gates owed." >&2
        return 1
    fi
}

ids=()
labels=()
reasons=()
plan() {
    ids+=("$1")
    labels+=("$2")
    reasons+=("$3")
}

if [[ -n "$extra" ]]; then
    plan extra "extra" "GATES_EXTRA: $extra"
fi
if ((owes_backend)); then
    plan ruff_check "ruff check" "back end changed"
    plan ruff_format "ruff format --check" "back end changed"
    plan mypy "mypy" "back end changed"
    plan pytest "pytest" "back end changed"
    plan gen_api "npm run gen:api (no-op)" "back end changed"
elif ((${#python_files[@]} > 0)); then
    plan ruff_format_files "ruff format --check (files)" "${#python_files[@]} Python file(s) outside the back end"
fi
if ((owes_web)); then
    plan web_check "npm run check" "web/ changed"
    plan web_build "npm run build" "web/ changed"
fi
if ((owes_sim && with_sim)); then
    plan sim "scripts/sim.sh test" "simulator path changed, --sim given"
fi

# ── the plan ─────────────────────────────────────────────────────────────────

echo "gates.sh: base $base (merge base $(git rev-parse --short "$merge_base")), ${#changed[@]} changed file(s)"
if ((dry_run)); then
    for path in "${changed[@]}"; do
        echo "    $path"
    done
fi
if ((${#ids[@]} == 0)); then
    echo "gates.sh: no gates owed"
else
    echo "gates.sh: plan"
    for i in "${!ids[@]}"; do
        printf '    %d. %-30s %s\n' "$((i + 1))" "${labels[i]}" "${reasons[i]}"
    done
fi
if ((owes_sim && !with_sim)); then
    echo "gates.sh: owed, not run: scripts/sim.sh test (the simulator path changed; pass --sim)"
fi
((dry_run)) && exit 0

# ── the run ──────────────────────────────────────────────────────────────────

# Microseconds since the epoch, from bash itself: no date(1) round trip per gate.
now_us() { echo "${EPOCHREALTIME/./}"; }
seconds() { printf '%d.%02d' "$(($1 / 1000000))" "$(($1 % 1000000 / 10000))"; }

statuses=()
durations=()
failed=0
run_started="$(now_us)"
for i in "${!ids[@]}"; do
    if ((failed && !keep_going)); then
        statuses+=("not run")
        durations+=("")
        continue
    fi
    echo
    echo "==> [$((i + 1))/${#ids[@]}] ${labels[i]}"
    started="$(now_us)"
    if "gate_${ids[i]}"; then
        status="ok"
    else
        status="FAILED"
        failed=1
    fi
    elapsed="$(seconds "$(($(now_us) - started))")"
    statuses+=("$status")
    durations+=("${elapsed}s")
    echo "<== ${labels[i]}: $status in ${elapsed}s"
done

echo
echo "gates.sh: summary against $base"
for i in "${!ids[@]}"; do
    printf '    %-30s %-8s %8s\n' "${labels[i]}" "${statuses[i]}" "${durations[i]}"
done
if ((owes_sim && !with_sim)); then
    printf '    %-30s %-8s\n' "scripts/sim.sh test" "owed"
fi
printf '    %-30s %-8s %8s\n' "total" "$( ((failed)) && echo FAILED || echo ok)" \
    "$(seconds "$(($(now_us) - run_started))")s"

exit "$failed"
