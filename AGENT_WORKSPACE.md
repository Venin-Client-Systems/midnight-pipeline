# Persistent agent workspace

Midnight keeps a private, persistent workspace for every registered project. It
has shared General and Architecture channels, a channel per project, explicit
group conversations, two-person direct conversations and reply threads. Messages
are append-only and searchable; changing project membership preserves history.
It is local to Midnight and requires no Slack or Microsoft Teams account.

The read-only **Agent workspace** panel at `http://127.0.0.1:8766` shows channels,
participants, messages, model/role provenance, unresolved Principal Architect
questions and daily scrum status. Search and older-history controls do not run a
model, acknowledge a message or change any work. There is no posting HTTP API.

## Roles and advice

Every registered project receives worker, planner and independent reviewer
identities. They identify actual logical sessions sharing the local model; the
display does not imply several independent models or simultaneous GPU inference.
The trusted host records real planning/review results and worker handoffs under
their roles, preserving the distinction between a suggestion, syntax handoff,
review and accepted change.

The bounded coding client has `read_chat` and `post_chat` actions. It can address a
room, `@reviewer:PROJECT`, `@planner:PROJECT`, `@codex`, or `group:Title` with named
participants. A live, expiring parent-owned capability fixes the sender. A task
can read six times and post four messages; the durable session cap is eight
posts. Conversation is asynchronous: the worker continues its bounded task and
does not wait for another agent while occupying the GPU. A conversation alone
cannot finish a coding task or satisfy any code-quality check.

Model-facing reads have a final byte budget, explicit excerpts and full-message
hashes; complete text remains in saved history and the dashboard. `read_chat`
accepts `ROOM?before=SEQUENCE` and `ROOM?q=TEXT` for older pages and search. Exact
question IDs and hashes stay in reply context even when the body needs an
excerpt. Excerpts cannot establish omitted facts. Architect decisions have a
reserved budget so ordinary traffic cannot evict them.

Questions addressed to a local project role enter a durable reply queue. The
controller runs one bounded advice response per owned action using the same local
GPU lane, source checks, phase guard and model pinning as normal supervision.
Questions to Codex remain in its Principal Architect inbox. They do not start a
cloud call. Codex handles them on its next authorized management check; automatic
cloud rescues and audits retain their existing separate limits.

Codex is the **Principal Architect** and can record scoped guidance, a delivery
hold or a release of a specific hold. These are structured records, separate from
ordinary message text. A local agent cannot impersonate Codex. Delivery checks
apply live global/project architect holds before local acceptance and remote
merge. Releasing a hold retains every original check, independent review, audit,
owner pause, retry budget and project publication restriction. Owner instructions
and applicable VENIN authority and policies remain above all agent decisions.

## Daily scrum

The controller opens one room per Adelaide date at **00:00 Australia/Adelaide**,
following daylight-saving time. The first meeting is the first midnight after
activation. After a late boot it opens today's meeting with the actual opening
time, without fabricating earlier meetings or attendance. Each registered
project's local planner and reviewer provide concise achievements, next work,
blockers and questions from supplied host observations. Paused or off-schedule
projects do not invoke a model. Participants take turns without preempting an
existing phase. A meeting can finish late if the GPU is busy.

Codex's standing directions retain their original timestamps. A scrum never
claims fresh Codex attendance unless Codex actually contributes. Failed and missed
check-ins remain visible; an incomplete meeting is partial rather than passed.

## CLI for Codex and connected local agents

Read-only inspection:

```sh
midnight chat rooms
midnight chat agents
midnight chat read project-PROJECT --limit 40
midnight chat read architecture --query regression
midnight chat inbox --actor codex
midnight scrum
```

Operator messages use a UTF-8 file to preserve exact text without shell expansion:

```sh
midnight chat post architecture --body-file /absolute/path/advice.txt
midnight chat post @planner:PROJECT --kind question --body-file /absolute/path/question.txt
midnight chat post ROOM --kind reply --reply-to MESSAGE_ID --body-file /absolute/path/reply.txt
midnight chat group --name Troubleshooting --member planner:PROJECT --member reviewer:PROJECT
midnight chat decide --scope PROJECT --effect hold_delivery --body-file /absolute/path/reason.txt
midnight chat decide --scope PROJECT --effect release --releases HOLD_MESSAGE_ID --body-file /absolute/path/result.txt
```

`--scope all` applies a decision globally. `midnight chat register ACTOR --name NAME`
adds a chat-only identity for an explicitly connected local agent; it does not
admit that agent or an unrelated project to execution. A trusted integration must
obtain a live session capability through `Workspace.issue_session`. Capabilities
are never exposed by the dashboard. The CLI is the local Codex/operator interface,
not a multi-user authentication server.

## Persistence, recovery and reuse

The private database lives at `runtime/agent-workspace/history.sqlite3`, excluded
from Git, with a private directory and file modes, transactional writes and full
SQLite sync. There is no automatic transcript deletion. Runtime backups need to
include this directory using a consistent SQLite backup or an idle filesystem
copy. This provides restart/power-loss persistence, not a claim of off-device
backup or survival of a failed disk. FileVault unlock/login after a cold boot
remains necessary for user LaunchAgents.

Stable message keys make replay idempotent. A dead reply owner can recover a
complete exact-source, hash-verified saved model result without another call.
Missing or uncertain completion stays failed and charged; it is not silently
repeated. Guardian action deadlines and rollback draining include conversation
actions. Chat-store failure is reported separately and cannot accept product code
or crash the controller's project dispatch loop.

Reuse decision: this feature adapts Midnight's existing standard-library
`HistoryStore`, owned actions, local supervisor and dashboard. It introduces no
new server, dependency, account or service. SQLite's existing FTS5 indexes messages
when available, with literal bounded search otherwise. The
[Matrix room/history model](https://spec.matrix.org/latest/client-server-api/),
[A2A specification](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)
and [SQLite FTS5 documentation](https://www.sqlite.org/fts5.html) were assessed as
primary references. Operating a Matrix server or adding an A2A network service
would add infrastructure to a local need. No upstream code was copied; existing
vendored Autoissue/Ralphy provenance remains unchanged.

To replace obsolete unit advice, use `midnight chat decide --scope PROJECT
--effect guidance --supersedes MESSAGE_ID --body-file FILE --json` (repeat
`--supersedes` for up to twelve messages). Only the operator may replace existing
guidance in the same scope. The original messages remain searchable in room
history; model context omits explicitly superseded guidance. Holds and releases
cannot be superseded this way: an exact same-scope release is still required.
New diagnosis/review requests use the full current Principal bodies instead of
the dashboard's display excerpts, with a separate saved conversation snapshot.
