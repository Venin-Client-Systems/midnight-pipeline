# Midnight install guide and FAQ

## What Midnight is

Midnight is a free, open-source tool that writes code on your Mac while you sleep. You give it a list of small tasks. Each night it picks one, has an AI model on your own Mac write the change, runs your tests to prove it works, has a second pass review it, and saves what passes as a Git commit.

Everything runs on your Mac. There is no account, no subscription and no per-use charge. Your code does not leave your machine.

It suits small, clearly described coding tasks in Python projects that have tests. It is version 0.1: it works, and it is still early.

| You need | Detail |
| --- | --- |
| A Mac with Apple Silicon | M1 or later. 16 GB of memory works; 32 GB or more runs a better model |
| 10 to 25 GB of free disk space | Most of it is the AI model; bigger Macs get a bigger model |
| 20 to 40 minutes the first time | Mostly waiting for downloads |

## Install

Open the Terminal app (press Command and Space, type Terminal, press Return). Paste this line and press Return:

`curl -fsSL https://raw.githubusercontent.com/Venin-Client-Systems/midnight-pipeline/main/install.sh | bash`

### If your Mac is missing something

On most Macs the first run stops and shows a short numbered list like the one below. This is normal. Midnight needs a few free tools, and the list gives the exact command for each one, chosen for your Mac.

{{FIGURE:missing}}

Paste each command in order and wait for each to finish. The model download is the slow one. Then paste the install line again.

| Tool | What it is |
| --- | --- |
| Apple's developer tools | Supplies Git, which tracks changes to code |
| Homebrew | A free installer for developer tools on a Mac. It asks for your Mac password |
| Python | The language Midnight is written in |
| Ollama | Runs AI models on your own Mac |
| A coding model | The AI that writes the code. The installer picks one that fits your memory |

### When everything is in place

The second run installs Midnight and sets it up. Nothing needs an administrator password.

{{FIGURE:install}}

Setup chose a model, set a nightly schedule (midnight to 9 am, Monday to Friday, in your time zone), started two background services and installed the `midnight` command. If Terminal later says `midnight: command not found`, paste the "Add it to your PATH" line that setup printed, then open a new Terminal window.

## Run the demo

See it work before you point it at your own code:

`midnight try`

{{FIGURE:try}}

Midnight creates a tiny practice project and gives itself one task: add a small function, with tests. You do not need to do anything. On a 16 GB MacBook Air the whole run took about six minutes; on a 32 GB Mac about three.

When it finishes, the practice project has a new commit written by Midnight:

{{FIGURE:result}}

{{FIGURE:commit}}

The commit records which model wrote it and that a separate review accepted it. You can also watch from the dashboard: open `http://127.0.0.1:8766` in your browser.

{{FIGURE:dashboard}}

## Use it on your own project

Your project needs to be a Git repository with at least one commit, written in Python, with a package folder at the top level and tests in a `tests` folder that run with `python3 -m unittest`.

Connect it:

`midnight init ~/code/myapp`

Then give it a task. Name the files and say exactly what "done" means:

`midnight add-task myapp --title "Reject empty usernames" --description "validate_username(name) in myapp/users.py must raise ValueError for an empty name and return the stripped name otherwise." --accept "validate_username('') raises ValueError." --accept "validate_username('  ada ') returns 'ada'." --file myapp/users.py --file tests/test_unit_users.py`

Tasks that finish have four things in common:

- **One behaviour each.** "Add X" or "make Y reject Z". Not "tidy up the user module".
- **Named files.** One to four exact files, always including a new test file called `tests/test_unit_something.py`.
- **Checkable acceptance.** Each `--accept` line is something a test can prove.
- **Small.** Three small tasks finish. One large task stalls.

Midnight works through the tasks in the next nightly window. To run now instead:

`midnight override --for 90m --reason "run my task"`

## Everyday commands

| Command | What it does |
| --- | --- |
| `midnight status` | Is it running, and what is it doing |
| `midnight report` | What was accepted, and what the review said |
| `midnight pause` and `midnight resume` | Stop after the current step, and start again |
| `midnight override --for 90m --reason "..."` | Run now, outside the nightly window |
| `midnight mode 24x7` and `midnight mode scheduled` | Run all the time, or go back to nights only |
| `midnight doctor` | A full health check, useful when asking for help |

## Questions and answers

### Does it cost anything?

No. Midnight is free and open source (Apache 2.0 licence). The model runs on your Mac, so there are no usage charges. It uses electricity.

### Does my code leave my Mac?

No. Writing, testing and review all happen locally. There is an optional feature that asks a cloud AI for advice when the local model is stuck; it is switched off unless you turn it on.

### Will it break my project?

It is built not to. The model works in a private copy of your project, can only change the files the task names, and cannot weaken tests you already have. A change reaches your project only after your Mac has rerun the tests itself and a separate review has accepted it. Every accepted change is an ordinary Git commit, so you can undo it with `git revert`.

### Does it push to GitHub?

No. Accepted work is a commit on your current branch, on your Mac. You decide what to push.

### How good is the code?

That depends on the model, and the model depends on your Mac's memory. On 16 GB, expect small, well-described tasks to finish. More memory runs a larger model and finishes more. Describe tasks the way you would brief a careful junior on their first day.

### Does my Mac need to stay awake?

Yes, during the hours you want it to work. A Mac that is asleep runs nothing. Plug it in and set it not to sleep overnight (System Settings, then Battery or Energy).

### Can I use my Mac while it runs?

Yes, but it will be slower for both of you, because the model uses most of the memory. That is why the default schedule is overnight.

### Which languages does it support?

Version 0.1 supports Python projects with `unittest` tests. Other languages are not supported yet.

### Does it work on Intel Macs, Windows or Linux?

Not yet. It needs a Mac with Apple Silicon.

### A task did not finish. What now?

Run `midnight status` and `midnight repair-status`. The usual fix is to make the task smaller or clearer and add it again. Every attempt keeps its full record, so you can see exactly where it stopped.

### Can I use a different model?

Yes. Download it with `ollama pull`, then run `midnight setup --model` followed by its name.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `midnight: command not found` | Paste the "Add it to your PATH" line from setup, then open a new Terminal window |
| The installer keeps listing missing things | Do each command in the list, in order, then run the install line again |
| The demo takes far longer than ten minutes | Close other apps to free memory. A model that does not fit in memory runs very slowly |
| Status says the service is not loaded | Run the install line again |
| The dashboard does not open | Another program is using its port. Run `midnight setup --port 8770` and use that number in the address |

For anything else, open an issue at github.com/Venin-Client-Systems/midnight-pipeline and include the output of `midnight doctor`.

## Uninstall

`midnight uninstall --purge`

Then delete the Midnight folder (`Developer/midnight` in your home folder). Your own projects are never touched. If you no longer want the tools it needed, Homebrew can remove Ollama (`brew uninstall ollama`).
