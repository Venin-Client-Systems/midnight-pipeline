# Security

## Reporting a problem

Please report a suspected vulnerability privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue for it. Include the Midnight commit, what you did and what happened.

## What Midnight protects against

Midnight lets a local AI model change code on your Mac while nobody is watching. The design assumes the model can be wrong, and can be misled by text in the project it is working on.

- **The model has no shell.** It can only read a file, replace exact text in a file the task names, run the project's tests, or finish. Paths are checked; symlinks and paths outside the workspace are refused.
- **It works in a private copy.** Your working tree is not touched while a task is attempted. `orchestration/`, and tests that already exist, cannot be changed by a task.
- **Tests run in the macOS sandbox.** Code the model wrote runs with no internet access, no ability to write outside a scratch folder and its workspace copy, and no ability to read your home folder, other users' folders, external drives or keychains.
- **Local network access is off for new projects.** See "Loopback" below.
- **The reviewed change is the delivered change.** The patch is fixed before the checks run; if it differs afterwards, nothing is delivered.
- **Delivery is a local commit.** Midnight does not push. Pull-request delivery exists but is off unless you configure it.
- **Cloud help is off by default.** If you enable it, the cloud tool is started read-only, with its own tools and settings switched off, within a daily budget.
- **The dashboard listens only on this Mac** (`127.0.0.1`), rejects requests from other websites, and needs a per-start token for its few controls (pause, resume, and cloud provider choice).

## What it does not protect against

- **A project you connect is trusted.** `orchestration/supervisor.py` runs with your permissions, and `orchestration/config.json` sets the sandbox limits for that project. `midnight init` refuses to adopt copies of these files that came with a repository unless you pass `--trust-existing`. Read them before you do.
- **Accepted code is still model-written code.** A second model pass reviews it and your tests must pass, but neither is a guarantee. Read what was committed before you run it outside the sandbox or ship it. Every change is an ordinary commit you can revert.
- **Text in your project can steer the model.** A dependency or document that says "ignore your instructions" may influence what the model writes. The limits above still apply to what it can do.
- **Other accounts on the same Mac.** Another logged-in user can open the dashboard address and read project status, and could use its controls. Do not run Midnight on a Mac you share with people you do not trust.
- **The model itself.** Midnight runs whatever model you install in Ollama, and pins it so it cannot be swapped silently. Choose models from sources you trust.
- **The installer.** `curl … | bash` runs a script from this repository. Read `install.sh` first if you prefer; it does not use `sudo`.

## Loopback

Some projects have tests that start a small server on the same machine. That needs local ("loopback") network access, and with it test code can also reach every other service listening on your Mac: Ollama, the Midnight dashboard, a local database.

Projects created by `midnight init` have it switched off (`"test_loopback": false` in `orchestration/config.json`). Turn it on only if your tests need it and you accept that trade.
