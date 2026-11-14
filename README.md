# Midnight

**Your Mac writes code while you sleep.** Midnight is an autonomous overnight coding pipeline that runs on a local model. It picks a task from your backlog, writes the change, proves it against your tests in a sandbox, has a second pass review it, and delivers accepted work as a commit, all on a schedule, with no cloud bill for routine work.

Built by [VENIN](https://venin.space).

## Why it's different

- **Local first.** Routine planning, coding and review run on a model served by Ollama on your own machine. Cloud models are optional and used sparingly, for guidance when the local model is stuck.
- **The model never gets a shell.** The worker chooses one small typed action at a time: read a file, make an exact edit, run the tests, finish. No arbitrary commands, no surprise file writes.
- **Trust the host, not the model.** A worker saying "done" counts for nothing. Midnight reruns the tests itself in a macOS sandbox with loopback-only network, then a separate review decides.
- **Retries need a reason.** A failed attempt cannot simply run again. Another try is admitted only with new evidence: a reviewed narrowing of the task, saved guidance, or an explicit owner release.
- **Built to run unattended.** Schedule windows, pause and override controls, a guardian service that watches the controller, and a read-only dashboard.
- **Everything is kept.** Every run retains its brief, model requests, tool results, check logs and outcome, so you can see exactly why a change was accepted or rejected.
- **Zero dependencies.** Pure Python standard library, with about 1,700 tests.

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

Retry admission is described in [PROGRESS_RECOVERY.md](PROGRESS_RECOVERY.md). Run evidence is kept under `runtime/`, which Git ignores.

## Project status

Midnight is experimental and currently between development phases.

- **What works today:** the full loop, from scheduling through task briefing, the edit-test-fix worker loop, sandboxed host verification, independent review, delivery and retry admission. It has delivered accepted changes to a real project.
- **Where it's limited:** quality tracks the local model. With a roughly 27-billion-parameter model on a 32 GB Mac, small, well-scoped tasks are the realistic target; multi-file repairs with end-to-end tests often stall at review. Larger local models are the next step, and the reason active development resumes when bigger hardware is on the bench.
- **Known rough edges:** it has been run on one machine so far, so installation takes some manual setup (see [Porting](#porting)), and the release installer needs rework before it is reusable.

Issues and pull requests are welcome, especially results from larger models.

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

Midnight has been developed on a single machine, so a few locations are fixed in code for now. As published, it expects:

- the checkout at `~/Developer/midnight`, with run data under its `runtime/` directory
- launchd services labelled `space.venin.midnight*`
- project entries in `projects.json` (the one shipped is an example and points at a path that does not exist)
- a GitHub organisation and delivery account set in `git_delivery.py` (placeholders as shipped)
- an authority record at `~/Developer/midnight-records/midnight-authority.json`, read by `maintenance_authority.py`

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
