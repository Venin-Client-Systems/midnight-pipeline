# Midnight

**Your Mac writes code while you sleep.**

Midnight is an autonomous overnight coding pipeline that runs on a local model. Give it a backlog. Each night it picks a task, writes the change, proves it against your tests in a sandbox, has a second pass review it, and commits what passes. You wake up to finished work and a full record of how it got there, with no cloud bill for routine coding.

Built by [VENIN](https://venin.space). Apache-2.0.

A night, illustrated:

```
00:00  window opens                 ──▶  pick task T014 from the backlog
00:01  local model plans            ──▶  one bounded brief, explicit file scope
00:02  local model implements       ──▶  read · edit · run tests · fix · finish
00:08  host reruns every check      ──▶  sandboxed, loopback-only network
00:10  independent review           ──▶  accept, or revise with reasons
00:11  delivered                    ──▶  commit (and a pull request, if you want)
       …next task…
09:00  window closes                ──▶  morning report on the dashboard
```

## Why use it

- **It works while you don't.** A schedule window, not a chat session. Start it once; it runs every night.
- **Your code stays on your machine.** Planning, coding and review use a model served locally by Ollama. Nothing leaves the Mac unless you switch on optional cloud guidance.
- **No per-token bill.** Routine work costs electricity. Cloud models are optional, budgeted, and only consulted when the local model is stuck.
- **You can trust what it merges.** The model's word counts for nothing. Midnight reruns your tests itself, then a separate review decides.
- **You can see everything.** Every attempt keeps its brief, every model request, every tool result, every check log and the verdict.

## Features

**A worker that can't go rogue**
- The model never gets a shell. It chooses one small typed action at a time: `read_file`, `edit_file`, `run_tests`, `finish`.
- Edits are exact text replacements after a fresh read; stale or ambiguous matches are rejected.
- Writes are limited to the files the task names. Tests you mark as protected can't be weakened.
- Test runs happen in a macOS sandbox: no forks, no writes outside a scratch directory, loopback-only network.

**Verification the model can't talk its way past**
- The host reruns the task's own tests and your project's full checks after every attempt.
- A separate review pass scores correctness, security, maintainability and tests, and must accept before anything is delivered.
- A candidate that fails keeps its evidence, and the next attempt is told exactly what failed.

**Retries that need a reason**
- A failed attempt can't just run again. Another try is admitted only with new evidence: a reviewed narrowing of the task, saved guidance, or an explicit release by you.
- Harness faults are told apart from product faults, so a broken tool doesn't burn attempts on a good task.

**Built to run unattended**
- Schedule windows with pause, resume and expiring overrides.
- Per-night limits on attempts, failures and cloud calls.
- A guardian service watches the controller, records incidents and can roll back a bad release.
- Recovers cleanly from restarts and power loss; interrupted work is resumed or held, never silently repeated.

**Optional cloud help, on a leash**
- Claude or Codex can be consulted for guidance when the local model is stuck, and for periodic quality audits.
- Hard daily budgets, one provider as primary with the other as fallback, and every call recorded.
- Set the budget to zero and Midnight is fully local.

**A dashboard you'll actually open**
- Read-only local web dashboard: what it's doing now, what was accepted and rejected overnight, code-quality ratings, model and hardware telemetry.
- The same facts from the command line, in plain text or JSON.

**Engineering you can read**
- Pure Python standard library. No dependencies to install.
- About 1,700 tests.
- Delivery by local commit, or by GitHub pull request with status checks.

## How it works

```
launchd (every minute, inside the schedule window)
  └─ midnight.py tick
       ├─ select task        backlog + open quality findings, one project at a time
       ├─ plan               local model writes a bounded task brief
       ├─ implement          local model, typed actions only
       ├─ host checks        the task's tests and the project's full checks, sandboxed
       ├─ review             separate local pass: accept or revise
       ├─ deliver            commit, optional GitHub pull request
       └─ recover            a failed attempt needs new evidence before a retry
guardian (separate launchd service, frozen copy of the source)
  └─ watches the controller, records incidents, can roll back a bad release
```

Retry admission is described in [PROGRESS_RECOVERY.md](PROGRESS_RECOVERY.md). Run evidence is kept under `runtime/`, which Git ignores.

## Requirements

- A Mac with Apple Silicon. 32 GB of memory runs a 24–30B coding model; more memory means a better model and better results.
- macOS (sandboxing uses `sandbox-exec`, scheduling uses launchd).
- Python 3.12 or later, at `/opt/homebrew/bin/python3`.
- [Ollama](https://ollama.com) on its default port.
- Git. Optional: the GitHub CLI for pull-request delivery, and the Claude or Codex CLI for cloud guidance.

## Install

Setup is manual for now; a one-command installer is on the [roadmap](#roadmap). These steps mirror the reference installation.

**1. Get Midnight**

```sh
mkdir -p ~/Developer && cd ~/Developer
git clone https://github.com/Venin-Client-Systems/midnight-pipeline.git midnight
cd midnight
python3 -m unittest discover -s tests     # about four minutes; should end in OK
```

Midnight expects to live at `~/Developer/midnight`.

**2. Give it a model**

```sh
ollama pull qwen3-coder:30b
curl -s http://127.0.0.1:11434/api/tags      # note the model's name and full digest
```

Put that name and digest in the `baseline` entry of [`models.json`](models.json). Midnight pins the exact model it was told to use and refuses a silent swap. See [MODELS.md](MODELS.md) for qualifying a different model.

**3. Connect a project**

Your project needs a Git repository, a test suite, and three small files in an `orchestration/` directory. Copy them from [`examples/project/orchestration/`](examples/project/orchestration):

| File | Purpose |
| --- | --- |
| `supervisor.py` | Ten lines that bind the project to Midnight. Change the project id. |
| `config.json` | Schedule window, per-night limits, which paths the worker may edit, which tests are protected. |
| `backlog.json` | Your tasks: a title, a description, acceptance criteria and the files in scope. |

Then describe the project in [`projects.json`](projects.json): its path, a `state_dir` under `runtime/projects/`, and one sentence on why it exists. The entry shipped there is an example; replace it. [BACKENDS.md](BACKENDS.md) has the full contract.

Write tasks the way you would brief a careful junior: one observable behaviour, the exact files, and acceptance criteria a test can prove.

**4. Schedule it**

```sh
sed "s/YOU/$USER/g" examples/launchd/space.venin.midnight.plist            > ~/Library/LaunchAgents/space.venin.midnight.plist
sed "s/YOU/$USER/g" examples/launchd/space.venin.midnight-dashboard.plist  > ~/Library/LaunchAgents/space.venin.midnight-dashboard.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/space.venin.midnight.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/space.venin.midnight-dashboard.plist
```

Set your timezone in the two plist files and in `projects.json` first. The default window is midnight to 09:00, Monday to Friday.

The supervising guardian is a third, optional service installed by `install_guardian.py` from a committed checkout; see [RECOVERY.md](RECOVERY.md).

**5. Check it**

```sh
python3 midnight.py doctor       # reports anything missing
python3 midnight.py status       # what it will do next, and when
open http://127.0.0.1:8766       # the dashboard
```

To try it now without waiting for midnight:

```sh
python3 midnight.py override --for 60m --reason "first run"
```

## Everyday use

```sh
midnight status                  # running, waiting, paused or offline, and why
midnight report                  # accepted work, next tasks, review feedback
midnight projects                # what is registered and scheduled
midnight pause [project]         # stop after the current step
midnight resume [project]
midnight override --for 90m --reason "…"    # run outside the window, with an expiry
midnight mode 24x7 | scheduled   # continuous, or back to the saved window
midnight quality --json          # check receipts and review ratings
midnight repair-status --json    # why a task is or isn't allowed another attempt
midnight cloud                   # cloud provider, budget and calls today
```

`midnight` here is `python3 ~/Developer/midnight/midnight.py`; add a shell alias. Most read commands take `--json`.

## What to expect

Midnight is experimental, and results track the local model.

- **What works today:** the whole loop, from scheduling and briefing through the edit-test-fix worker loop, sandboxed verification, independent review, delivery and retry admission. It has delivered accepted changes to a real project.
- **Where a ~27B model on 32 GB lands:** small, well-scoped tasks are the realistic target. Multi-file repairs that need new end-to-end tests often stall at review.
- **Where it gets interesting:** the pipeline is model-agnostic. A bigger local model drops straight in, and that is where development is headed.

## Roadmap

- One-command installer and configurable install location (today: `~/Developer/midnight`, service labels `space.venin.midnight*`, and a GitHub organisation set in `git_delivery.py`)
- Qualification and tuning for larger local models
- Linux support (the sandbox and scheduler are macOS-specific today)
- A project template generator

If you run Midnight with a larger model, open an issue with what you saw. That is the most useful contribution right now.

## Repository layout

| Path | Contents |
| --- | --- |
| `midnight.py` | Controller and command-line interface |
| `project_backend.py` | Per-project loop: task selection, brief, attempt, checks, review, delivery |
| `structured_worker.py`, `local_worker.py` | The typed-action local worker |
| `progress_recovery.py` | Retry admission ledger |
| `verification_sandbox.py`, `verification_runner.py` | Host check sandbox |
| `guardian.py`, `coordinator_install.py` | Supervision, release and rollback |
| `coordinator.py`, `cloud_policy.py`, `cloud_rescue.py` | Optional cloud guidance and escalation |
| `dashboard.py`, `dashboard/` | Read-only local dashboard |
| `examples/` | launchd definitions and a sample project adapter |
| `ralphy/` | Vendored task runner (MIT, see `ralphy/LICENSE`) |
| `tests/`, `native_tests/` | Unit tests; tests that need the real sandbox |
| `docs/` | Operator notes and design records |

## Documentation

- [docs/OPERATIONS.md](docs/OPERATIONS.md): the operator's guide from the reference installation
- [BACKENDS.md](BACKENDS.md): what a project must provide to be scheduled
- [MODELS.md](MODELS.md): local model selection, qualification and memory admission
- [PROGRESS_RECOVERY.md](PROGRESS_RECOVERY.md), [RECOVERY.md](RECOVERY.md): retry admission and failure recovery
- [OVERSIGHT.md](OVERSIGHT.md), [CONTINUOUS_IMPROVEMENT.md](CONTINUOUS_IMPROVEMENT.md): quality monitoring
- [OPEN_SOURCE.md](OPEN_SOURCE.md), [REUSE.md](REUSE.md): the reuse policy given to workers

The design records were written during development of the reference installation and read that way. Project names in them (`exampleapp`, `secondapp`) are placeholders.

## Contributing

Issues and pull requests are welcome. Run `python3 -m unittest discover -s tests` before you open one.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
