# Midnight

Midnight is a scheduled, unattended coding pipeline for one Mac. Overnight it picks a task from a project's backlog, has a **local model** write the change, checks the change on the host, has a second local pass review it, and delivers accepted work as a commit. Cloud models are used sparingly for guidance and oversight, not for routine coding.

It was built by [VENIN](https://venin.space) as an internal tool and is published as a record of what worked and what did not.

## Status

**Shelved, not production software.** Read this before investing time.

- The pipeline runs end to end on an Apple Silicon Mac with 32 GB of memory and a roughly 27-billion-parameter local model served by Ollama.
- On that hardware the local model is the limit. In the last commissioning session it took a two-file repair through every host check but did not produce a change that also passed independent review. The remaining failures were the model looping on edits and misquoting source, not harness faults.
- Development is paused until larger local hardware is available.
- **It has only ever run on one machine.** Install location, launchd service labels and the delivery account are fixed in code. See [Porting](#porting).
- The release installer (`coordinator_install.py`) only accepts control files from one past release and cannot run as written.

## How it works

```
launchd (every minute, inside the schedule window)
  └─ midnight.py tick
       ├─ select task        backlog + quality findings, one project at a time
       ├─ plan               local model writes a bounded task brief
       ├─ implement          local model, typed actions only:
       │                     read_file · edit_file · run_tests · finish
       ├─ host checks        the task's tests and the project's full checks,
       │                     in a sandbox (loopback network only)
       ├─ review             separate local pass: accept or revise
       ├─ deliver            commit, optional GitHub pull request
       └─ recover            a failed attempt needs new evidence before a retry
guardian (separate launchd service, frozen copy of the source)
  └─ watches the controller, records incidents, can roll back a bad release
```

Design choices worth knowing about:

- **The worker cannot run shell commands.** It chooses one small typed action at a time. An edit is an exact text replacement after a fresh read.
- **Worker claims are not acceptance.** The host reruns the tests itself, and a separate review decides.
- **Retries need a reason.** After a failure, another attempt on the same task is admitted only with a recorded proof: a reviewed narrowing of the task, saved guidance, a qualified harness fix, or an explicit owner release. See [PROGRESS_RECOVERY.md](PROGRESS_RECOVERY.md).
- **Everything is retained.** Each run keeps its brief, model requests, tool results, check logs and outcome under `runtime/` (not tracked in Git).

## Requirements

- macOS on Apple Silicon. Sandboxing uses `sandbox-exec`; scheduling uses launchd.
- Python 3.12 or later. Midnight itself uses only the standard library.
- [Ollama](https://ollama.com) serving a local coding model on `127.0.0.1:11434`.
- Git, and optionally the GitHub CLI for delivery by pull request.
- Optional: the Codex or Claude command-line clients for cloud guidance.

## Repository layout

| Path | Contents |
| --- | --- |
| `midnight.py` | Controller and command-line interface |
| `project_backend.py` | Per-project loop: task selection, brief, attempt, checks, review, delivery |
| `structured_worker.py`, `local_worker.py` | The typed-action local worker |
| `progress_recovery.py` | Retry admission ledger |
| `verification_sandbox.py`, `verification_runner.py` | Host check sandbox |
| `guardian.py`, `coordinator_install.py` | Supervision, release and rollback |
| `coordinator.py`, `cloud_policy.py`, `cloud_rescue.py` | Cloud guidance and escalation |
| `dashboard.py`, `dashboard/` | Read-only local dashboard |
| `projects.json` | Registered projects and schedule |
| `quality_profiles/` | Per-project quality checks |
| `ralphy/` | Vendored task runner (MIT, see `ralphy/LICENSE`) |
| `third_party/` | Provenance for referenced third-party sources |
| `tests/`, `native_tests/` | Unit tests; tests that need the real sandbox or model |
| `docs/` | Operator notes and design records |

## Running the tests

```sh
python3 -m unittest discover -s tests
```

About 1,700 tests, four minutes on the reference machine. They need macOS (some run `sandbox-exec`) but no model and no network.

## Documentation

- [docs/OPERATIONS.md](docs/OPERATIONS.md): the operator's guide written for the original installation
- [BACKENDS.md](BACKENDS.md): the contract a project must meet to be scheduled
- [MODELS.md](MODELS.md): local model selection, qualification and memory admission
- [PROGRESS_RECOVERY.md](PROGRESS_RECOVERY.md), [RECOVERY.md](RECOVERY.md): retry admission and failure recovery
- [OVERSIGHT.md](OVERSIGHT.md), [CONTINUOUS_IMPROVEMENT.md](CONTINUOUS_IMPROVEMENT.md): quality monitoring
- [OPEN_SOURCE.md](OPEN_SOURCE.md), [REUSE.md](REUSE.md): the reuse policy given to workers

These were written for one installation and one operator, and read as a record of it. Project names in them (`exampleapp`, `secondapp`) are placeholders for the private projects Midnight ran.

## Porting

Nothing here has been run on a second machine. As published, the code expects:

- the checkout at `~/Developer/midnight`, with run data under its `runtime/` directory
- launchd services labelled `space.venin.midnight*`
- project entries in `projects.json` (the one shipped is an example and points at a path that does not exist)
- a GitHub organisation and delivery account set in `git_delivery.py` (placeholders as shipped)
- an authority record at `~/Developer/midnight-records/midnight-authority.json`, read by `maintenance_authority.py`

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
