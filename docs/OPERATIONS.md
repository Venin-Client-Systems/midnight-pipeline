# Midnight

[Progress-based recovery](../PROGRESS_RECOVERY.md) makes a verified intervention the
reason for another local coding run. Earlier launch counts remain visible history;
identical failure returns to diagnosis. The dashboard's **Local recovery** panel
and `midnight repair-status [project] --json` show diagnosis, proof, checkpoint
and next action. Codex owns harness fixes through the existing limited checked
maintenance path. Routine work stays local and all final acceptance gates remain.

The shared [persistent agent workspace](../AGENT_WORKSPACE.md) supplies project and
architecture channels, group chats, direct conversations, a Codex Principal
Architect inbox and daily **00:00 Adelaide** scrum check-ins. Inspect it in the
read-only dashboard or with `midnight chat rooms`, `midnight chat inbox` and
`midnight scrum`. Routine replies stay local and retain every existing quality,
pause, retry, cloud-budget and publication boundary.

The managed worker selects a small action before generating code. An edit is one
exact matching text replacement after a fresh file read, with stale or ambiguous
matches rejected. This avoids generating whole replacement files in the action
decision. Fixed syntax checks, original semantic host checks and independent
review still gate every submission and final acceptance.

ExampleApp is the only registered project and uses the shared development backend
and single local-model lane. SecondApp was taken off the schedule on 11 November
2026, archived on 12 November and deregistered in PR #44. Its earlier pilot
remains a historical admission record, not current permission to run.
See [backend controls](../BACKENDS.md).

Midnight is the owner's local development pipeline. The owner supplies broad goals to Codex. Codex maintains the project register, project briefs and priorities. Routine implementation, planning and separate independent review run locally with the selected model. Limited cloud rescues and sampled Codex audits retain the strongest qualified cloud profile. Each project retains its own implementation backlog, protected tests, review gates and Git history.

The dashboard's **Code quality** panel reports actual host-check receipts, independent local review ratings and sampled Codex audit results separately. Ratings are subjective 0–5 judgements for correctness, security, maintainability and regression tests, with confidence, base commit, exact patch identity and accepted commit. Missing ratings and unaudited changes remain explicit. A healthy sample does not certify an entire project. Dashboard reads never invoke a model.

Automatic audits reserve at most **one routine and one corrective Codex call per project in seven rolling days**, including unknown outcomes. Routine audits require a changed, accepted, locally reviewed source and never resample identical source. A concrete regression holds that project's automatic merging and becomes a repair finding. Local repairs still need the full checks and independent local review; one budgeted corrective audit can approve only the exact repaired patch. Its hold clears after that patch is accepted. Cloud implementation retains the existing two failures, guidance, two guided failures and exceptional rescue limits; routine local failures never silently fall back to cloud. Automatic cloud qualification calls are disabled; official discovery can identify successors for deliberate, budgeted qualification.

## Check progress

In a local Codex chat, say **“Check Midnight”**, **“What did Midnight achieve last night?”**, or **“Which projects are on Midnight?”** The installed Midnight skill and global instructions point future chats to fresh local observations. This requires a Codex environment with access to this Mac's files and commands.

```sh
midnight                         # current pipeline and project progress
midnight projects                # projects registered on/off the schedule
midnight report                  # direction, accepted work, next tasks and feedback
midnight status --json           # structured fresh observations for Codex
midnight report exampleapp        # one project
midnight performance              # accepted/failed runs and worker/review time
midnight dashboard                # read-only local dashboard, port 8766
midnight mode                     # current operating mode and saved schedule
midnight mode 24x7                # continuous operation, no expiry
midnight mode scheduled           # restore saved weekday 00:00–09:00 Adelaide window
midnight commissioning            # inspect the one-use retained-action grant
```

`projects.json` (local and git-ignored; created by `midnight setup` from [projects.example.json](../projects.example.json)) is the authoritative Midnight membership and direction register. It records which projects are on the schedule, where their backlogs and evidence live, why each is being built, and Codex's current instruction. The shared MAP records infrastructure; the VENIN Developer authority record preserves the owner's scoped permissions. Newer owner instructions and current controls remain authoritative.

Shared retry-quality, reuse, recovery and reporting controls apply to **all registered projects**. [BACKENDS.md](../BACKENDS.md) defines protocol5 and the required version-checked adapter contract. Product-specific feature work remains in each project's own repository. Superseded failed tasks retain their original history and are not counted as completed.

The local dashboard is at **http://127.0.0.1:8766**. Its separate RunAtLoad/KeepAlive user service stays online while Midnight works or waits, restarts if it exits, and starts after login. It requires no open chat or manual foreground launch. `midnight dashboard --port PORT` serves a foreground instance for inspection; avoid the installed port. Runtime/logs remain under Developer.

Ask Codex **“Is Midnight running?”**, **“What is Midnight doing?”** or **“When does Midnight run next?”**. The existing Midnight skill reads fresh status and answers running/waiting/paused/offline explicitly. `midnight status` leads with the same plain-language answer; `status --json` includes an additive `operation` object. Scheduled times display in Adelaide time, and a pause/offline service remains an explicit barrier to the next run.

The **Next launch** panel and `readiness` status object distinguish scheduled
eligibility from current resource admission. They verify the selected GLM profile
and report a fresh native memory sample, including any shortfall. One sample is
never proof of the continuous quiet interval; dispatch still checks all leases,
task controls and the full admission gate before charging inference. An existing
worker can be active while the next-launch sample shows insufficient free memory.
Project cards label the time of their retained observation separately.

Local planning and review preserve a spent request's stable input key across
actions and restarts. Verified complete output can be recovered and rechecked
without inference; failed or uncertain unchanged input stays held. A delayed
host recheck does not create another model allowance. Native sessions retain a
bounded `memory-history.json` with the latest 96 samples and lifetime charged/RSS
peaks, request identity, last observed activity and actual cleanup result. These
are diagnostic observations, separate from coding quality or accepted work.

The dashboard starts with those three answers and a live telemetry panel. One background sampler observes native CPU utilisation, logical cores/load, thermal state, kernel memory pressure, occupied RAM/compression/wired memory, swap usage and swap rates, Apple GPU utilisation/allocation, loaded Ollama model allocation/context/quantisation, and scoped worker/server processes. It samples every two seconds and keeps at most 300 points in memory (10 minutes). The fixed read-only `/api/telemetry` and `/api/telemetry/events` routes serve cached samples independently of project status; more browsers do not create more samplers. Source failures stay unavailable, stale readings are labelled, and telemetry does not invoke inference or require privileged power-sampling tools.

Resource scope matters: GPU figures include other apps; GPU/model allocations share and overlap system RAM; occupied RAM includes cache; process CPU100% equals one logical core. Cloud hardware is not exposed. Worker activity uses verified host process receipts and bounded event categories, without prompts, command arguments, response text or tool output. Token counts, when supplied by the worker, describe completed turns; the current client does not expose live generation token/s or context occupancy. Sampling and charts do not change scheduling, model settings or project state. History resets when the dashboard restarts.

The dashboard streams fresh observations every two seconds, with polling fallback. It shows every registered project, current work, receipt-based accepted/failed results, review waits, schedule/override expiry, checkpoint/power health, task register, paged full run history and per-run checks/review/repair/outcome receipts. Bounded check-log tails are labelled; raw model prompt files and arbitrary filesystem browsing are not exposed. Session summaries and a spreadsheet-safe CSV download remain available. Host/Origin checks, fixed routes and a loopback bind preserve the read-only control boundary; there are no job or settings endpoints.

Native macOS notification requests run in the dashboard service for accepted work, failed attempts, supervision errors, override/recovery changes and an unexpectedly stale/stopped active controller. They work without a browser tab; macOS notification/Focus settings still determine visible delivery. An OS-helper exit code does not prove that a notification appeared. The local activity journal preserves delivery attempts and restart offsets under `runtime/dashboard/`. Browser alerts are optional: click **Enable browser alerts** once and grant the browser permission. They supplement native alerts while the page is connected; no external Web Push provider or account is configured.

The interactive 3D orchestration scene uses WebGL 2 (the browser interface based on OpenGL ES) and a locally vendored, pinned Three.js release with its MIT licence/provenance. No CDN is contacted. It renders on updates, resize or drag, uses low-power settings and a capped pixel ratio, and pauses when hidden. The ordinary data view remains available if graphics are unsupported. See dashboard/vendor/UPSTREAM.json and REUSE.md.

The model panel provides paired qualification scores and timing totals, checkpoint health, and inspectable current and archived probe receipts. Open a comparison to see each recorded pass, timeout and memory observation; live inspections refresh automatically. Run inspections also expose host worker-completion and redundant editing-copy quarantine receipts. All inspection remains file-only and read-only.

## Schedule and override

The user LaunchAgent `space.venin.midnight` checks every minute and at midnight. Normal work is **00:00–09:00 Australia/Adelaide, Monday–Friday**, including daylight-saving transitions. The saved `schedule.weekdays` list controls eligible start days; omitting it retains the all-days interpretation. At 09:00 the controller stops ordinary work and drains owned processes; a slow prefill does not extend the window. Registered projects have independent durable supervisors. One local coding phase and one host-check phase may run at a time, alongside up to two cloud phases. Existing attempt, rescue and acceptance rules apply. Live endurance is recorded separately from test rehearsals.

The owner has restored the weekday schedule. An explicit future `midnight mode 24x7` selection can still store continuous operation with **no expiry**, retaining the saved normal schedule; `midnight mode scheduled` restores it. Model selection does not change the schedule or manual pauses. Per-action/worker deadlines, resource capacities, independent acceptance, rolling audit budgets and exceptional rescue reservations remain enforced. Routine local supervision shares the same single GPU lane as local implementation. Waiting phases enter in creation order so long checks cannot continually overtake another project's queued work.

The reviewed GLM memory profile waits for two minutes of normal memory pressure,
at least 24 GB of free plus inactive memory, and at most 64 MiB of new swap-out.
Missing observations hold admission. Before native launch, unsuitable-memory or
GPU waits release the heavy-memory lease so eligible host work can proceed;
reacquisition starts a fresh full two-minute quiet interval, and personal use
still requires the existing pause-and-drain handoff. A shared heavy-memory lease excludes owned
local/host work from the coordinated MacDriveBackup runner. Backups defer during
the weekday window, the five minutes before it, and active commissioning; a
running backup yields as incomplete after stopping its owned child processes.
Unconfirmed cleanup stays fenced. No unrelated process is killed. Exact-request
memory failures retain durable holds and bounded recovery; see [MODELS.md](../MODELS.md).

An explicitly activated commissioning grant admits **one unchanged retained
project action**, pinned to its task, source and model, for at most nine hours.
It does not start general planning, scrum, maintenance or another project action.
The grant cannot be renewed or reused; after completion or expiry, ordinary work
waits for the next scheduled start. Pauses, installation gates, memory admission
and all acceptance checks still apply. Inspect with `midnight commissioning`;
activation requires explicit project, task, absolute deadline and owner reason.

The existing seven-day SecondApp/ExampleApp review campaign keeps its original start,
end, seven 24-hour intervals and seven paid-attempt slots. A reviewed append-only
amendment binds the new source, profile and schedule without replacing old reports
or attempts. Due cloud assessments may run outside the local work window, subject
to global pause, installation, memory and cloud-capacity holds. Profile/source
cohorts and sampled paused, outside-schedule and unknown time remain distinct.
See the amendment contract.

```sh
midnight override --for 2h
midnight override --until "2026-11-02 00:00" --continuous
midnight override --until "2026-10-31 18:00" --reason "Extra development time"
midnight override                # inspect the expiry
midnight override --cancel
midnight pause                   # pause the whole pipeline
midnight resume
midnight pause exampleapp         # pause only one registered project
midnight resume exampleapp
```

An override starts eligibility immediately and automatically expires. Local dates without offsets use Adelaide time; ambiguous or nonexistent clock times require an explicit offset. At expiry, normal weekday eligibility applies: outside an eligible midnight–09:00 window work stops; inside one it may continue until 09:00. Ordinary overrides preserve all pause settings and project review/attempt limits. Resume makes the controller eligible; it does not extend the time window. The controller checks expiry and pause during active work and passes the absolute deadline to the backend worker. Stopping may take a few seconds while processes shut down.

An explicitly requested `--continuous` override lifts the daily attempt, cloud-call and failure-count ceilings until its expiry. The ordinary configuration is not edited; eligibility and the backend independently restore ordinary limits after expiry. Per-task retries, worker/review deadlines, pause controls and independent acceptance remain. Eight consecutive failed attempts trigger a 15-minute recovery backoff; provider errors also retain their existing retry delay. Provider usage limits and unavailable models can interrupt productive work. Continuous means the dispatcher remains eligible across daytime hours, not that a stalled or unavailable model is reported as working.

The Mac must be powered on, awake and logged in at the scheduled start. Midnight holds a sleep assertion while active. The chat does not need to remain open. Commands that change override or pause state are local controls, not cloud account operations.

## Session ledger

All Midnight runtime state lives under `~/Developer/real-projects/midnight/runtime/`, excluded from Git. The private CSV is runtime/sessions.csv. Open it in Numbers, Excel, a text editor, or ask Codex to read it. It contains one row per scheduled project per session: local date, start/end timestamps, scheduled/override mode, project, why, achieved changes, accepted/attempt counts, outcome, a short paragraph and evidence path. A session normally spans a scheduled window or override; pauses, shutdowns and controller errors also close a session. A restart resumes its checkpoint; duplicate finalisation cannot duplicate ledger rows. All-paused or allowance-limited projects remain waiting until the window ends, allowing a later resume within the same session. A session with no attempts records zero work explicitly.

Summaries derive from accepted commit receipts and saved attempt counters. A failed attempt or pending review is not accepted work. Long overrides create a daily checkpoint at the first task boundary after the Adelaide date changes. A labelled historical commissioning row records the already-verified first local-model task; synthetic test sessions are kept outside the live ledger. `session.json` and `status.json` show work still in progress, before its final CSV row is written. `sessions/<session-id>/` retains backend logs, and the project's own run directories retain patches, checks and reviews.

`midnight performance` reads those receipts and reports accepted and failed attempts, work still running or awaiting review, acceptance rate, recorded worker time and review time. It defaults to the current/last override's creation time and ends at now or the expired override's endpoint, so the weekend report stays bounded after Monday. Without an override it covers the last 24 hours. Use `--since ISO_TIMESTAMP` and `--until ISO_TIMESTAMP` for another interval and `--json` for machine-readable output. Missing or malformed worker event lines are ignored; no token throughput or hardware utilisation is inferred from wall-clock duration. Detailed evidence remains in each run directory.

## Codex operations

[Open-source reuse](../OPEN_SOURCE.md) is a standing policy for every Midnight project. Consider proven components and adapters before substantial new work, and record the choice or a short reason for original implementation. Copied code retains its licence/notices, immutable upstream revision and modification record. The coordinator handles research/acquisition; bounded workers use supplied material within existing permissions. Independent review checks the decision, provenance and integration tests.

Every dispatched instruction snapshot includes the policy and its SHA-256. Project backends propagate it to workers, planning and independent review; verify this when admitting another project. `midnight status --json` exposes its current identity/hash. Retained project run prompts record the policy version used for their cycle.

When the owner gives a broad instruction, Codex updates the appropriate project brief and backlog and the `instruction`/`why` fields here. The dispatcher writes that direction into a private instruction snapshot supplied to the worker and cloud planner. Use the existing project scope and review rules; registering a project does not grant account, publishing or assessment authority.

To add a project, prepare and verify a backend compatible with `managed-once --until ISO_TIMESTAMP --brief PATH`, register its absolute paths and rationale, validate with `midnight status --json`, and update the shared operational records. Only set `on_schedule: true` after that project is explicitly admitted. The adapter must honour the deadline and pause marker, independently verify changes, and maintain durable `state.json` accepted-run receipts and attempt counters. No second per-project LaunchAgent should dispatch the same work. The retired ExampleApp LaunchAgent is retained privately for recovery.

## Worker efficiency and retry evidence

The current backend keeps bounded candidate checkpoints and earlier failure feedback. A retry can continue a reviewed or timed-out patch when its task, product inputs, allowed paths and hash still match; coordinator-only orchestration changes need not discard it. Stale/tampered candidates and out-of-scope changes are rejected. Every resumed candidate still needs full host checks and independent review. Checkpoints do not count as accepted work, and retry counts/deadlines stay in force.

Ephemeral local workers have persistent goal tools disabled, a repository import path for socket-free checks, and instructions to leave Git operations and socket integration tests to the host. These are workflow controls; the selected Qwen model, quantisation and context are unchanged. Apply and verify equivalent discipline when admitting another backend.

A task-specific `.midnight-ready.json` handoff stops an ephemeral worker once its edited files pass the fixed syntax check. Managed local work uses Ralphy with Midnight's schema-checked read_file, edit_file, run_tests and finish decisions. Edits use a separate bounded exact-text replacement after a fresh read, preserving all unrelated bytes. The host supplies exact writable files and a fixed sandboxed parser check without executing candidate code or permitting forks, and creates the marker only after a real edit and a passing check after the final edit. The parent verifies model, manifest, final file and check-log identities, then runs the original focused and full semantic checks in the independent host phase. Exit zero or printed tool markup cannot submit managed local work. Unchanged coding retries stop before another review. Full protected acceptance and independent review remain mandatory; only an accepted commit receipt establishes an achievement. Interactive local-code settings are unchanged.

## Stability and power-loss recovery

See [RECOVERY.md](../RECOVERY.md) for durable checkpoints, restart reconciliation and power/login requirements, and [STABILITY.md](../STABILITY.md) for Codex's ongoing maintenance backlog. `midnight doctor --json` is a read-only health check; `midnight recover --json` performs local maintenance while the controller is idle. Startup repairs checkpoint mirrors and reconciles project commits before finalising the session CSV or starting work. Interrupted attempts remain distinct from quality failures.

New backends must implement a local `recover` command that invokes neither a model nor cloud review. Current ExampleApp recovery uses the shared `durability.py` module in this sibling Midnight checkout. Keep that module and backend protocol available when moving or deploying either repository.

## Verify the pipeline

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary registries/state and a synthetic backend, without invoking a model or changing the live schedule. They cover schedule boundaries, DST, override expiry and fallback, session replay, unchanged acceptance semantics, missing/corrupt observations, and actual subprocess cutoff.

## Shared local model maintenance

Midnight checks trusted publisher releases daily and qualifies eligible local
configurations in small paired coding exercises between project tasks. A
challenger must pass all host assertions, fit this Mac and beat the incumbent
before every admitted project receives it. The previous model remains available
for rollback. This selects the best configuration proven in the tracked
catalogue; it does not claim a universal ranking. See [MODELS.md](../MODELS.md),
`midnight models --json`, and the dashboard's model panel for exact digests,
candidates, benchmark progress and promotion/rollback history.

Worker tasks follow one observable behaviour with explicit examples and files.
Storage, validation, presentation and CLI wiring are dependency-linked increments;
full integration acceptance remains on the final increment. Shared host failure
feedback excludes successful check noise while retaining complete receipts.

## Continuous improvement and GitHub delivery

Protocol 4 adds a shared product-quality cycle. Projects remain enrolled while
assessments, repairs and milestones finish. Confirmed material defects and debt
block new features across the project; independent repair and research work can
continue. A current baseline and protected checks are required before a
single-use feature ticket is issued. See [CONTINUOUS_IMPROVEMENT.md](../CONTINUOUS_IMPROVEMENT.md).

All cloud roles use the pinned strongest qualified profile, initially
`gpt-6-astra` with `ultra` reasoning. Official-release discovery and available
CLI catalogue checks can qualify successors at task boundaries. A model is not
promoted simply because it is newer. See [CLOUD_MODELS.md](../CLOUD_MODELS.md).
Local implementation remains the normal path. Exceptional cloud implementation
requires two substantive failures, concrete Codex guidance, two guided failures,
and an explicit manager rescue decision. At most three reservations per project
in 168 hours and one per unresolved issue are allowed. Uncertain launches consume
the reservation; failed cloud work needs manager attention.

`midnight quality`, `midnight findings`, `midnight improvement-history` and
`midnight rescue` are read-only views. Add a project ID and `--json` as needed.
The dashboard includes the same assessment, debt, model, rescue and delivery
observations. Status schema 2 bounds accepted receipts to 50, with a separate
total and truncation flag. History pages use indexed SQLite queries.

The authorised GitHub identity is `example-delivery-account`. Managed private
repositories are `example-org/midnight` and
`example-org/exampleapp`. Product delivery uses a deterministic task
branch, PR, exact-head host and independent-review statuses, strict current-base
checks, automatic squash merge and confirmed remote receipt before acceptance.
Mandatory human/code-owner approvals are zero; automated checks, PRs, admin
enforcement and force-push/deletion protections remain required. Existing local
accepted commits retain their historical meaning.

GitHub Team support for private branch protection is an external prerequisite.
The owner handles billing. At task boundaries, repository setup rechecks that
prerequisite no more than twice an hour and configures only these two authorised
repositories when available. It never purchases a plan or makes repositories
public. While unavailable, the dashboard reports the reason and delivery holds
verified work. A coordinator source-upgrade PR must be merged/synchronised
before product PRs can use the new canonical base.

Session CSV schema 2 appends `cloud_implementation_count` and `schema_version`.
`attempt_count` keeps its local-attempt meaning; old rows migrate with cloud=0.
An indexed session database is authoritative and the CSV export appends durably,
rebuilding after damage. Closed unpinned clones become eligible for archival
after 30 days; exact base/patch reconstruction and a complete current working-tree
archive are verified before deletion. Unresolved/pending work stays pinned.
Under 5 GiB free, generation holds to preserve evidence. No physical power-cut
or 36-month live endurance claim is made by the synthetic recovery/scale tests.


## Automatic stall recovery

Protocol 5 gives each project a durable owned action and journals every local,
host-check and cloud phase. Waiting for one project’s reviewer does not block
another project’s local work. Resource waiting does not consume an implementation
attempt. Execution-group receipts remain capacity owners until their processes
are proved stopped, even if a phase or its supervisor has failed.

The separate `space.venin.midnight-guardian` LaunchAgent audits every 60
seconds from a verified frozen bundle under Developer. Known internal errors are
handled at the next audit, an unexplained runnable project after two minutes,
and an unexplained queue-wide hold after five minutes. Owner pauses, resource
pressure, provider holds, review decisions and schedule expiry remain explicit.
The guardian tries deterministic recovery once per incident before requesting an
isolated repair of Midnight itself. After a reviewed version-two coordinator
adoption, new coding needs a one-use host-verified intervention; original charged
attempts remain history. Preparation, saved-work verification, independent review
and delivery continue as distinct stages. Identical failures park without another
inference while other work continues. Before that explicit adoption, the legacy
API retains its four-attempt limit. Product cloud rescue/audit limits remain
separate and unchanged. See [RECOVERY.md](../RECOVERY.md) for the effective-mode,
one-transaction launch gate, retained-stage recovery and migration contracts.

A maintenance patch must reproduce its defect in a new regression, preserve all
existing tests and policy files, pass the complete checks and a separate strongest
qualified Codex review, and merge through a private checked PR. Candidate tests
run with read-only source, isolated temporary writes and no account access.
Native sandbox tests run from the unchanged frozen trusted bundle because macOS
cannot nest seatbelt sandboxes. Both complete-suite receipts are required.
Installation waits for idle product checkpoints, records intent before changing
source, confirms the loaded controller/dashboard generation and rolls code back
if health fails. Runtime evidence and session history are preserved.

The read-only dashboard includes the guardian audit time, resource slots,
incidents, recovery attempts and current blockers. These commands are also read-only:

```sh
midnight incidents --json
midnight recovery-history --json
midnight doctor --json
```

The installed guardian reads the recorded AUT-0004 permission cells afresh before
account delivery. Changed permission text holds delivery for Codex to reconcile;
changes to unrelated automation rows do not interrupt Midnight. No guardian can
clear an owner pause or extend an override.


## Mac temperature, fan speed and prepared release handoff

The telemetry sampler reads native AppleSMC temperatures and fan RPM every ten
seconds through a bounded read-only child process. HTTP/SSE requests return the
cached observation. The dashboard shows CPU/GPU sensor averages, all detected
fans, recent charts and individual sensor readings under measurement details.
Missing/failed sensors are unavailable; real zero RPM means a stopped fan.
Sensor groups follow macmon conventions rather than identified physical die
locations. The small standard-library port retains macmon's MIT licence and
immutable provenance in `third_party/macmon/`. No extra daemon, root access or
fan-control writes are used. Temperature/fan fields are under `hardware` in
`/api/telemetry` and have their own reading timestamp.

A prepared update can be handed off with `deferred_bootstrap.py` and
`deferred_release.py`. The manager first completes all checks and independent
review, confirms the private GitHub merge, freezes the entire tested source and
pins its manifest and minimal cleanup hashes in a one-minute user LaunchAgent.
The handoff invokes no model and cannot invent tasks or change permissions.
It waits for the actual exact-source four-hour commissioning result, then lets
owned work finish and adopts only the prepared clean source. It preserves
pauses, original override expiry and normal run windows. Individual restart
intents and the commissioning-bound adoption intent survive interruption.
The independent guardian verifies adoption and restores prior code on failed
health; failed native sensor verification requests rollback after six minutes.
State, proof copies and completion receipts stay under
`runtime/deferred-releases/`, outside Git. Personal operating-picture publication
is retired: configured legacy completion requests receive an idempotent local
`venin-completion-record.json` receipt. Historical personal outbox/results remain
unchanged. VENIN progress is tracked in GitHub and the Site / Project Map.
A queue entry by itself does not schedule or wake an agent.
