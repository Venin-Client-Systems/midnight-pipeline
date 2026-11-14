#!/opt/homebrew/bin/bash
# Dedicated local-only entry point for the user's vendored Autoissue/Ralphy loop.
# Vendor files remain byte-identical to PROVENANCE.json; adaptations live here.
set -euo pipefail

RALPHY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for module in config utils help args github coordination notifications cleanup domain \
  sources/markdown sources/yaml sources/github sources/dispatch git monitor prompt \
  engines cost preflight guardrails execution parallel summary main; do
  source "$RALPHY_DIR/lib/$module.sh"
done

LOCAL_CODE_BIN="${MIDNIGHT_LOCAL_CODE_BIN:-${EXAMPLEAPP_LOCAL_CODE_BIN:-${HOME}/.local/bin/local-code}}"
RALPHY_LOCK_DIR="${EXAMPLEAPP_RALPHY_STATE_DIR:-${TMPDIR:-/tmp}/exampleapp-ralphy-${UID}}/instances"

# Never touch unrelated host processes or make audible/external notifications.
reap_orphaned_processes() { :; }
notify_done() { :; }
notify_error() { :; }
notify_task_done() { :; }
notify_task_stuck() { :; }
project_board_init() { :; }
# Upstream returns 1 for non-GitHub sources here, aborting main under errexit.
auto_label_docs_issue() { :; }
# This adapter never creates Ralphy branches; the host owns candidate clones.
guardrail_init_ledger() { RALPHY_BRANCH_LEDGER=""; }

# The supervisor owns a disposable standalone clone, so Ralphy must not stash,
# switch branches, push, publish or change any other repository.
guardrail_preflight() {
  git rev-parse --is-inside-work-tree >/dev/null || return 1
  local git_dir
  git_dir=$(git rev-parse --git-dir)
  for operation in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply; do
    if [[ -e "$git_dir/$operation" ]]; then
      log_error "An unfinished Git operation exists: $operation"
      return 1
    fi
  done
}

require_engine_cli() {
  [[ "$1" == codex ]] || { log_error "This runtime supports the local engine only"; return 1; }
  [[ -x "$LOCAL_CODE_BIN" ]] || { log_error "Local coding client is unavailable"; return 1; }
}

# This host-only reader is included in the adapter's existing source binding.
# Capture trusted parent identities before the client; never resolve client links.
result_handoff() {
  /opt/homebrew/bin/python3 -I -B - "$@" <<'MIDNIGHT_RESULT_PY'
import json, os, stat, sys
from pathlib import Path

LIMIT = 65536
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def parent_fd(path):
    fd = os.open('/', DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            new = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd); fd = new
        return fd
    except BaseException:
        os.close(fd); raise

def prepare(path):
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    fd = parent_fd(parent)
    try:
        try: os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: pass
        else: raise ValueError('Result destination already exists')
        info = os.fstat(fd)
        return dict(parent=str(parent), name=path.name, device=info.st_dev, inode=info.st_ino)
    finally: os.close(fd)

def bound_parent(binding):
    fd = parent_fd(binding['parent']); info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (binding['device'], binding['inode']):
        os.close(fd); raise ValueError('Result parent changed')
    return fd

def publish(binding):
    source, target = binding
    fd = bound_parent(source)
    try:
        stream = os.open(source['name'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(stream)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or not 0 < before.st_size <= LIMIT):
                raise ValueError('Result is not a bounded regular file')
            data = bytearray()
            while len(data) <= LIMIT:
                chunk = os.read(stream, LIMIT + 1 - len(data))
                if not chunk: break
                data.extend(chunk)
            after = os.fstat(stream)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                                     info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if (len(data) != before.st_size or len(data) > LIMIT or identity(after) != identity(before)):
                raise ValueError('Result changed while reading')
            data.decode('utf-8')
        finally: os.close(stream)
    finally: os.close(fd)
    fd = bound_parent(target)
    try:
        out = os.open(target['name'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
        identity = os.fstat(out)
        try:
            with os.fdopen(out, 'wb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
        except BaseException:
            current = os.stat(target['name'], dir_fd=fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                os.unlink(target['name'], dir_fd=fd)
            raise
    finally: os.close(fd)

try:
    if sys.argv[1] == 'prepare': print(json.dumps([prepare(p) for p in sys.argv[2:]]))
    elif sys.argv[1] == 'publish': publish(json.loads(sys.argv[2]))
    else: raise ValueError('Unknown result handoff phase')
except (OSError, ValueError, KeyError, TypeError) as exc:
    print('Worker result handoff refused: ' + type(exc).__name__, file=sys.stderr)
    sys.exit(1)
MIDNIGHT_RESULT_PY
}

# This is the actual engine hook used by Ralphy's sequential execution loop.
# Its Codex parser continues to handle the resulting JSONL and final-message file.
run_ai_command() {
  local prompt=$1 output_file=$2
  local events_path="${EXAMPLEAPP_AGENT_EVENTS:-${EXAMPLEAPP_AGENT_LOG:-}}"
  local result_path="${EXAMPLEAPP_AGENT_RESULT:-}"
  if [[ -n "$events_path" && -z "$result_path" ]]; then
    result_path="${events_path}.last"
  fi
  local worker_result_path="${result_path:-${output_file}.last}"
  local parser_path="${output_file}.parser-last" binding=""
  # Upstream cleanup may delete this variable even when the child never ran.
  # Never give it a pre-existing or refused parser destination.
  CODEX_LAST_MESSAGE_FILE=""
  if binding=$(result_handoff prepare "$worker_result_path" "$parser_path"); then
    CODEX_LAST_MESSAGE_FILE="$parser_path"
  fi
  (
    set +e
    # A child must acquire the shared local-model lock in its own right.
    unset LOCAL_CODE_EVALUATION_LOCK_HELD
    local agent_rc handoff_state
    # The candidate can write only inside its host-owned run directory. Keep
    # Ralphy's disposable parser file separate: upstream deletes it on cleanup.
    if [[ -z "$CODEX_LAST_MESSAGE_FILE" ]]; then
      # A retained result belongs to the original run, never a new invocation.
      printf '{"type":"error","error":{"message":"Worker final-message destination already exists or is unsafe; reconcile the retained run"}}\n' >> "$output_file"
      if [[ -n "$events_path" ]]; then
        printf '{"type":"error","error":{"message":"Worker final-message destination already exists or is unsafe; reconcile the retained run"}}\n' >> "$events_path"
      fi
      exit 73
    fi
    if [[ -n "$events_path" ]]; then
      "$LOCAL_CODE_BIN" -a never exec --ephemeral --json -c features.goals=false \
        --output-last-message "$worker_result_path" "$prompt" 2>&1 \
        | tee "$output_file" >> "$events_path"
      agent_rc=${PIPESTATUS[0]}
    else
      "$LOCAL_CODE_BIN" -a never exec --ephemeral --json -c features.goals=false \
        --output-last-message "$worker_result_path" "$prompt" > "$output_file" 2>&1
      agent_rc=$?
    fi
    if [[ $agent_rc -eq 0 ]]; then
      if result_handoff publish "$binding"; then
        handoff_state=completed
        [[ -z "$result_path" ]] && rm -f -- "$worker_result_path"
      else
        handoff_state=failed
        printf '{"type":"error","error":{"message":"Worker final-message handoff failed; retained result requires inspection"}}\n' >> "$output_file"
        if [[ -n "$events_path" ]]; then
          printf '{"type":"error","error":{"message":"Worker final-message handoff failed; retained result requires inspection"}}\n' >> "$events_path"
        fi
      fi
      printf '{"type":"worker.result_handoff","schema_version":1,"status":"%s"}\n' "$handoff_state" >> "$output_file"
      if [[ -n "$events_path" ]]; then
        printf '{"type":"worker.result_handoff","schema_version":1,"status":"%s"}\n' "$handoff_state" >> "$events_path"
      fi
    fi
    # Preserve the actual client result independently of the outer loop status.
    printf '{"type":"worker.client_result","schema_version":1,"exit_code":%d}\n' "$agent_rc" >> "$output_file"
    if [[ -n "$events_path" ]]; then
      printf '{"type":"worker.client_result","schema_version":1,"exit_code":%d}\n' "$agent_rc" >> "$events_path"
    fi
    # Upstream ignores process status. Preserve failure as a recognised event.
    if [[ $agent_rc -ne 0 ]]; then
      printf '{"type":"error","error":{"message":"Local coding client exited %d"}}\n' "$agent_rc" >> "$output_file"
      if [[ -n "$events_path" ]]; then
        printf '{"type":"error","error":{"message":"Local coding client exited %d"}}\n' "$agent_rc" >> "$events_path"
      fi
    fi
    exit "$agent_rc"
  ) &
  ai_pid=$!
}

build_prompt() {
  cat <<EOF
Read ${PRD_FILE}, AGENTS.md, and progress.txt in this repository, using plain file
paths (the @ character is not part of a filename). Read any contract section the
task names before editing. The Midnight worker uses host-checked read_file,
edit_file, run_tests and finish JSON decisions. Read before each edit, then submit
one exact matching old_text/new_text replacement. Preserve the rest of the file.
Do not create temporary scripts or patch files.
Call the actual command/edit tools; never print a literal <tool_call> as a
substitute. A repair checklist in PRD.md must be implemented, not just explained.
Implement only the single incomplete task in ${PRD_FILE}. Use tools to read and
edit the repository; a plan alone is insufficient. If tools are unavailable,
respond exactly TOOL_ACCESS_FAILED.

Follow the task's exact acceptance requirements and allowed file scope. Do not
modify orchestration, launch agents, credentials, model configuration, account
state, or unrelated files. Do not use the network or start a listening service.

Write meaningful unit tests and run the worker-safe check named in ${PRD_FILE}.
Use the pinned runtime already supplied by the host. Do not substitute another
project's test command. The host separately runs protected acceptance checks.
Do not run socket/integration tests in the worker, and do not weaken tests to
make failures disappear. Run the project's documented worker-safe syntax/lint
checks if present. Record any failure honestly in progress.txt.

The coordinator owns operating records and goals; report progress in progress.txt.
Do not call persistent goal tools in this ephemeral worker. Do not stage or commit
Git changes: the host owns repository operations and canonical commits.

When the task is implemented and unit tests pass, mark its checkbox complete,
append a concise progress entry, then use the submission handoff in PRD.md as
your LAST file write. The host may stop you immediately to begin its checks.
Finish with a short factual summary if the process remains open. Do not
push, publish, or access external accounts. Work on exactly one task. The host
will independently verify the candidate before accepting it.
EOF
}

# Explicitly label upstream status as a candidate; host checks decide acceptance.
show_summary() {
  printf '\nRalphy candidate run ended. Host validation determines task acceptance.\n'
  printf 'Iterations: %s; candidate completions: %s\n' "$iteration" "$session_processed"
}

usage() {
  cat <<'EOF'
Usage: run.sh [--prd PATH] [--dry-run]

Runs one Autoissue/Ralphy task with the local coding model, one attempt, and no
parallel workers or cloud fallback. The caller must use a disposable Git clone,
enforce its wall-clock deadline, and independently validate all proposed changes.

EXAMPLEAPP_AGENT_EVENTS optionally retains raw model JSONL throughout the run.
EXAMPLEAPP_AGENT_RESULT optionally retains the final message (default EVENTS.last).
The host must allocate a fresh result path; retained results require reconciliation.
EXAMPLEAPP_RALPHY_STATE_DIR optionally selects this run's local lock directory.
EXAMPLEAPP_LOCAL_CODE_BIN is an explicit host override, primarily for testing.
Exit zero means the bounded Ralphy loop ended; it is not proof of completed work.
EOF
}

prd=PRD.md
dry_run=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prd)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      prd=$2
      shift 2
      ;;
    --dry-run) dry_run=(--dry-run); shift ;;
    --help|-h) usage; exit 0 ;;
    --version) printf 'Autoissue/Ralphy %s, ExampleApp local adapter\n' "$VERSION"; exit 0 ;;
    *) printf 'Unsupported argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

main --codex --prd "$prd" --no-auto-parallel --max-iterations 1 --max-retries 1 \
  "${dry_run[@]}"
