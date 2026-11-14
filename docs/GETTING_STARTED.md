# Getting started with Midnight

This guide takes you from nothing to a project that Midnight works on every night. It assumes an Apple Silicon Mac.

- [1. Before you install](#1-before-you-install)
- [2. Install](#2-install)
- [3. Run the demo](#3-run-the-demo)
- [4. Connect your own project](#4-connect-your-own-project)
- [5. Write tasks a local model can finish](#5-write-tasks-a-local-model-can-finish)
- [6. What happens overnight](#6-what-happens-overnight)
- [7. Read the results](#7-read-the-results)
- [8. Control it](#8-control-it)
- [9. When a task is held](#9-when-a-task-is-held)
- [10. Change the model](#10-change-the-model)
- [11. Optional cloud guidance](#11-optional-cloud-guidance)
- [12. Troubleshooting](#12-troubleshooting)
- [13. Uninstall](#13-uninstall)

## 1. Before you install

| You need | How to get it |
| --- | --- |
| Apple Silicon Mac, ideally 32 GB of memory or more | Midnight uses the macOS sandbox and launchd |
| Homebrew Python 3.12+ | Install [Homebrew](https://brew.sh), then `brew install python` |
| Git | `xcode-select --install` |
| Ollama, running | Install from [ollama.com](https://ollama.com) and open it |
| One coding model with tool support | `ollama pull qwen3-coder:30b` (about 19 GB) |

The model matters more than anything else here. A 24–30B coding model on 32 GB finishes small, well-described tasks. With more memory, pull a bigger model: Midnight uses whatever you give it.

Close memory-hungry apps before a run. If the model does not fit in free memory, macOS swaps it to disk and every answer takes minutes.

## 2. Install

```sh
curl -fsSL https://raw.githubusercontent.com/Venin-Client-Systems/midnight-pipeline/main/install.sh | bash
```

The script checks your Mac, clones Midnight into `~/Developer/midnight` and runs `midnight setup`. Setup then:

1. checks each prerequisite and tells you the fix for anything missing
2. chooses the largest installed model that fits in memory and pins it, so it can never be swapped silently
3. writes your schedule to `projects.json` (00:00 to 09:00, Monday to Friday, your Mac's timezone)
4. loads two launchd services: the controller, and a read-only dashboard at <http://127.0.0.1:8766>
5. installs the `midnight` command in `~/.local/bin`

Nothing needs sudo. If `midnight` is "command not found" afterwards, add `~/.local/bin` to your `PATH`; setup prints the line to use.

Setup options, passed after `bash -s --` or to `midnight setup` directly:

| Option | Meaning |
| --- | --- |
| `--model NAME` | Use this Ollama model instead of the automatic choice |
| `--start HH:MM`, `--end HH:MM` | Nightly window (default 00:00 to 09:00) |
| `--every-day` | Run at weekends too |
| `--timezone ZONE` | IANA timezone, for example `Europe/London` |
| `--port N` | Dashboard port (default 8766) |
| `--no-services` | Skip launchd; you run `midnight tick` yourself |

To install somewhere else, set `MIDNIGHT_HOME` before the command. Running setup again is safe: it keeps your schedule and projects.

## 3. Run the demo

```sh
midnight try
```

This creates `~/Developer/midnight-demo` (a ten-line Python package with one test), connects it, adds one task ("add `normalize_label`, with tests") and opens a 90-minute run window.

What you will see, in order:

| Stage | What Midnight is doing | Typical time on 32 GB |
| --- | --- | --- |
| Assessment | Runs the project's tests and has the model review the code once, as a baseline | 5–10 min |
| Attempt | The model reads files, edits, runs the tests and fixes failures, one small action at a time | 5–15 min |
| Host checks | Your Mac reruns the tests itself, in a sandbox | under 1 min |
| Review | A separate pass reads the change and accepts it or sends it back with reasons | 3–10 min |
| Delivery | The accepted change is committed to the demo repository | seconds |

Watch with `midnight status`, or open the dashboard. When it finishes:

```sh
git -C ~/Developer/midnight-demo log --oneline     # the new commit
midnight report                                    # what was accepted, and the review
```

If the demo does not produce a commit, see [Troubleshooting](#12-troubleshooting). A small model on a busy Mac can run out of time; the evidence says exactly where it stopped.

## 4. Connect your own project

```sh
midnight init ~/code/myapp
```

Your project needs to be:

- a Git repository with at least one commit and no uncommitted changes
- a Python project with a package at the top level (a folder containing `__init__.py`)
- tested with `unittest`, in a `tests/` folder

`init` adds four small files and commits them to your repository:

| File | Purpose |
| --- | --- |
| `orchestration/supervisor.py` | Ten lines that bind the project to Midnight |
| `orchestration/config.json` | Limits per night, and which paths the worker may edit |
| `orchestration/backlog.json` | Your tasks |
| `ROADMAP.md` | What the project is for; the model reads it as background (only created if missing) |

It also registers the project in Midnight's own `projects.json`. Midnight never pushes anywhere: accepted work is a local commit on your current branch.

Check `orchestration/config.json` once. `worker_allowed_patterns` lists the only paths the worker can write, and by default every test that exists today is protected: the worker can add tests but cannot change the ones you already have.

## 5. Write tasks a local model can finish

```sh
midnight add-task myapp \
  --title "Reject empty usernames" \
  --description "validate_username(name) in myapp/users.py must raise ValueError for an empty or whitespace-only name, and return the stripped name otherwise." \
  --accept "validate_username('') raises ValueError." \
  --accept "validate_username('  ada ') returns 'ada'." \
  --file myapp/users.py --file tests/test_unit_users.py
```

Brief it the way you would brief a careful junior on their first day:

- **One behaviour per task.** "Add X" or "make Y reject Z", not "refactor the user module".
- **Name the files.** One to four, and always include a test file named `tests/test_unit_<something>.py`. The worker runs that file while it works.
- **Make acceptance checkable.** Each `--accept` line should be something a test can prove, ideally with a concrete input and output.
- **Say where things go.** Name the function and the file in the description.
- **Split anything bigger.** Three small tasks finish; one large task stalls at review.

Tasks are stored in `orchestration/backlog.json`, which you can also edit by hand and commit.

## 6. What happens overnight

Inside the schedule window, the controller wakes every minute and, for each connected project in turn:

1. picks the next task from the backlog
2. clones the project into a private workspace, so your working copy is never touched mid-task
3. lets the local model work through typed actions: `read_file`, `edit_file`, `run_tests`, `finish`. It has no shell and can only write the task's files
4. reruns the task's tests and the full test suite itself, in a macOS sandbox with loopback-only network
5. has a separate review pass score the change and accept or reject it
6. commits an accepted change to your repository, with a trailer naming the run

A failed attempt is not simply repeated. Midnight allows another try only when something changed: review feedback to act on, a narrower version of the task, or your explicit release. This stops a stuck model from burning the night on one task.

Per-night limits live in `orchestration/config.json`: `max_attempts_per_night` and `max_failures_per_night`.

## 7. Read the results

| Command | Shows |
| --- | --- |
| `midnight status` | Running, waiting, paused or offline, and why; per-project progress |
| `midnight report` | Accepted work, what is next, and review feedback |
| `midnight quality` | Check results and review ratings |
| `midnight repair-status` | Whether each task may be attempted again, and what would allow it |
| `midnight performance` | Attempts, acceptance and time for the last run window |

Most take `--json`. The dashboard shows the same facts and is read-only.

Every attempt keeps its evidence under `~/Developer/midnight/runtime/projects/<project>/runs/<run>/`: the brief, each model request and reply, each tool result, the check logs and the review. When something goes wrong, the answer is in there.

## 8. Control it

```sh
midnight pause [project]                     # stop after the current step
midnight resume [project]
midnight override --for 90m --reason "..."   # run now, outside the window, until the time is up
midnight override --cancel
midnight mode 24x7                           # run continuously
midnight mode scheduled                      # back to the nightly window
```

Change the nightly window by editing `schedule` in `~/Developer/midnight/projects.json`. Take a project off the schedule by setting its `on_schedule` to `false` there.

## 9. When a task is held

`midnight status` says when a task is waiting on you, and `midnight repair-status` says why.

- **Review sent it back.** Midnight retries by itself with the reviewer's reasons.
- **Out of attempts.** Rewrite the task smaller or clearer in `backlog.json` and commit. A changed task is a new task.
- **Harness problem** (`needs_harness_repair`). Something outside the model failed, such as the model server timing out. Once the cause is fixed, release it:

  ```sh
  midnight pause myapp
  /opt/homebrew/bin/python3 ~/Developer/midnight/progress_recovery.py --root ~/Developer/midnight \
      --owner-release myapp --task T001 --reason "Model server restarted after running out of memory"
  midnight resume myapp
  ```

## 10. Change the model

```sh
ollama pull <bigger-model>
midnight setup --model <bigger-model>
```

Setup pins the new model. Midnight refuses to run if the pinned model's files change underneath it, so re-run this after `ollama pull` updates a model you already use. [MODELS.md](../MODELS.md) covers automatic model comparison, which is off while a model is pinned.

## 11. Optional cloud guidance

Midnight is fully local out of the box: `max_cloud_calls_per_night` is `0`.

If you have the Claude or Codex command-line tool installed and signed in, Midnight can ask it for guidance when the local model is stuck. Set `max_cloud_calls_per_night` in the project's `orchestration/config.json` to a small number, commit, and choose the provider with `midnight cloud --primary claude` or `--primary codex`. The cloud model advises; the local model still writes the code. `midnight cloud` shows today's calls and budget.

## 12. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Midnight is not set up yet` | Run `midnight setup` |
| `midnight: command not found` | Add `~/.local/bin` to your `PATH`, or run `/opt/homebrew/bin/python3 ~/Developer/midnight/midnight.py` |
| Status says the scheduler service is not loaded | Run `midnight setup` again, or `midnight tick` in a terminal |
| `Coordinator has owner changes; no new action launched` | You edited a tracked file inside `~/Developer/midnight`. Midnight only runs from a clean checkout: `git -C ~/Developer/midnight status`, then commit or discard |
| `Selected local model is missing` or `digest changed` | The pinned model was removed or updated. Run `midnight setup --model <name>` |
| Attempts end with `stream_error` or `task_timeout` | The model is too slow, almost always because it does not fit in free memory. Close other apps, or use a smaller model |
| A project never starts | `midnight status` and `midnight repair-status` give the reason; a paused project, a dirty project checkout and a held task are the usual ones |
| Dashboard does not open | Another program has port 8766. Run `midnight setup --port 8770` |

`midnight doctor` prints a full health report. If you open an issue, attach it and the run folder of the failed attempt.

## 13. Uninstall

```sh
midnight uninstall            # removes the two services and the midnight command
midnight uninstall --purge    # also deletes the project list and all run history
rm -rf ~/Developer/midnight   # removes the program
```

Your projects are never touched by uninstall. The `orchestration/` folder in each one is yours to keep or delete.
