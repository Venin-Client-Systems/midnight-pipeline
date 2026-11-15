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

## Quick start

New to the terminal? The illustrated [install guide and FAQ (PDF)](docs/guide/Midnight-Install-Guide-and-FAQ.pdf) walks through every step.

You need an Apple Silicon Mac. Open Terminal and paste the install command. On a new Mac it first lists what is missing (Apple's developer tools, Homebrew, Python, Ollama, a coding model sized to your memory) with the exact command for each; do those, then run it again.

**1. Install**

```sh
curl -fsSL https://raw.githubusercontent.com/Venin-Client-Systems/midnight-pipeline/main/install.sh | bash
```

This clones Midnight into `~/Developer/midnight`, picks the largest installed model that fits in memory, sets a nightly window (00:00 to 09:00, Monday to Friday, in your timezone), loads two background services and adds a `midnight` command. No sudo. Prefer to read before you run? It is the same as:

```sh
git clone https://github.com/Venin-Client-Systems/midnight-pipeline.git ~/Developer/midnight
/opt/homebrew/bin/python3 ~/Developer/midnight/midnight.py setup
```

**2. Watch it work**

```sh
midnight try
```

Creates a tiny demo project, gives it one task and runs it straight away. It took about three minutes on a 32 GB Mac with a 27B model and about six on a 16 GB MacBook Air with a 14B model; allow much longer if something else is using the model or memory is short. When it finishes there is a new commit in `~/Developer/midnight-demo`, written by the local model, checked by your Mac and reviewed before it landed. Follow along with `midnight status` or the dashboard at <http://127.0.0.1:8766>.

**3. Point it at your own project**

```sh
midnight init ~/code/myapp
midnight add-task myapp \
  --title "Reject empty usernames" \
  --description "validate_username(name) in myapp/users.py must raise ValueError for an empty or whitespace-only name." \
  --accept "validate_username('') raises ValueError." \
  --accept "validate_username('ada') returns 'ada'." \
  --file myapp/users.py --file tests/test_unit_users.py     # a new test file for this task
```

That is it. The task runs in tonight's window, or now with `midnight override --for 60m --reason "run my task"`. In the morning, `midnight report` shows what was accepted and why anything was not.

Midnight v0.1 works on **Python projects tested with `unittest`**. The full walkthrough, including how to write tasks a local model can finish, is in [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

## Requirements

- A Mac with Apple Silicon. 32 GB of memory runs a 24–30B coding model; more memory means a better model and better results.
- macOS (sandboxing uses `sandbox-exec`, scheduling uses launchd).
- Python 3.12 or later from Homebrew, at `/opt/homebrew/bin/python3`.
- [Ollama](https://ollama.com) on its default port, with a model that supports tool calls.
- Git. Optional: the GitHub CLI for pull-request delivery, and the Claude or Codex CLI for cloud guidance.

## Everyday use

```sh
midnight try                     # run a demo task now
midnight init <path>             # connect a project
midnight add-task <project> …    # add a task to its backlog
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

Setup installs the `midnight` command in `~/.local/bin`. Most read commands take `--json`. To remove Midnight: `midnight uninstall` (add `--purge` to delete run history too).

## What to expect

Midnight is experimental, and results track the local model.

- **What works today:** the whole loop, from scheduling and briefing through the edit-test-fix worker loop, sandboxed verification, independent review, delivery and retry admission. It has delivered accepted changes to a real project.
- **Where a ~27B model on 32 GB lands:** small, well-scoped tasks are the realistic target. Multi-file repairs that need new end-to-end tests often stall at review.
- **Where it gets interesting:** the pipeline is model-agnostic. A bigger local model drops straight in, and that is where development is headed.

## Roadmap

- Projects beyond Python and `unittest`
- GitHub pull-request delivery for any organisation (today the organisation is a constant in `git_delivery.py`; local commits work everywhere)
- Qualification and tuning for larger local models
- Linux support (the sandbox and scheduler are macOS-specific today)

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
| `install.sh`, `setup_cli.py` | Installer and the `setup`, `init`, `add-task`, `try` commands |
| `examples/` | launchd definitions and a sample project adapter |
| `ralphy/` | Vendored task runner (MIT, see `ralphy/LICENSE`) |
| `tests/`, `native_tests/` | Unit tests; tests that need the real sandbox |
| `docs/` | Operator notes and design records |

## Documentation

- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md): install, first project, writing tasks, reading results, troubleshooting
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
