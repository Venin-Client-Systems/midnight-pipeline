# Vendored Autoissue/Ralphy runtime

This directory contains the retained Ralphy shell runtime from the owner's Autoissue
fork. `PROVENANCE.json` records the original checkout commit, its dirty state and
SHA-256 of every copied file. The source checkout has not been modified.

`run.sh` uses the real Ralphy sequential task loop with a dedicated local coding
engine hook. It calls `${HOME}/.local/bin/local-code -a never exec
--ephemeral --json --output-last-message ...` and supports one task and one attempt
per invocation. It disables cloud fallback, auto-parallelism, external account
task sources, the broad orphan-process reaper, and sounds/notifications. The
supervisor supplies a disposable standalone clone and enforces the night window.

```sh
/opt/homebrew/bin/bash /absolute/path/to/orchestration/ralphy/run.sh --prd PRD.md
```

The caller can set `EXAMPLEAPP_AGENT_EVENTS` to retain raw JSONL continuously and
`EXAMPLEAPP_AGENT_RESULT` for the final message (defaults to `EVENTS.last`), and
`EXAMPLEAPP_RALPHY_STATE_DIR` to isolate local instance metadata. The older
`EXAMPLEAPP_AGENT_LOG` name is also accepted for the event stream. The adapter
unsets `LOCAL_CODE_EVALUATION_LOCK_HELD` so the local client acquires its own
shared model lock. It creates no new process session or detached daemon; the
host can terminate the entire inherited process group at its deadline.
`EXAMPLEAPP_LOCAL_CODE_BIN` is an explicit host override used by the offline smoke
tests. Production should leave it unset or set it to the trusted local wrapper.

The upstream process may exit zero even when a model task failed or merely
reached its iteration limit. A completed checkbox or final message is a model
claim. The host must inspect actual changes and run its independent verification
before accepting a task. Raw runs are candidates until those checks pass.

The worker prompt allows only `python3 -m unittest discover -s tests -p
'test_unit*.py'` and documented worker-safe syntax/lint checks. The host separately
owns the socket lab and full acceptance suite.

`legacy/ralphy.sh` is retained as source evidence. Its upstream relative import
path does not match the current Autoissue layout; invoke `run.sh` instead.
