# Midnight recovery

[Progress-based local recovery](PROGRESS_RECOVERY.md) records exact failed work,
its diagnosis, immutable interventions and transactional one-use admission in
each project's existing HistoryStore. Preserve the `pr_` tables and all legacy
manager/microstep/owner-extension records together. A consumed proof remains
spent after crash, restart or rollback; do not clear tables, rename tasks or
re-enroll an exhausted issue as fresh work. Read `midnight repair-status --json`
without inference. New reviewed harness certificates are separate private
runtime records and are usable only with their exact verified source/model and
current adopted generation. Pending obsolete requests are retired before cloud
maintenance reservation; original request and charge history remain.

[Agent conversations](AGENT_WORKSPACE.md) persist in their own private SQLite
history under `runtime/agent-workspace`. Preserve its database and journal in
backups. Completed exact-source replies recover from verified saved receipts
without inference; uncertain invocations remain failed and charged. A restart
opens today's overdue midnight scrum once, recording actual time. Conversation
actions honor recipient controls throughout resource waits and execution and
cannot take the maintenance lane while an orchestrator repair owns it.

Midnight recovers its durable runtime state when its user LaunchAgent starts. It then reconciles each registered project's unfinished work and accepted commits before finalising a session or dispatching another task. A chat does not need to be open.

## After a power outage

Midnight assumes the Mac restarts automatically after a power failure, with FileVault on and automatic login off. A cold boot therefore requires the owner to unlock and log in before Midnight and the local model's user services can run. Midnight does not change these settings. After login, the installed RunAtLoad/one-minute LaunchAgent checks eligibility and recovers automatically.

The saved Adelaide schedule and absolute override expiry remain authoritative. An outage does not extend an override. Cancelled overrides have durable deletion records so an older readable file cannot reactivate one. Prepared, uncommitted promotions wait for an eligible window; already completed commits can be reconciled at any time.

## Durable storage

Critical state, the current session, override and promotion intent retain two committed generations in private `checkpoints.sqlite3` databases beside their readable files. The session CSV uses the same checkpoint mechanism. SQLite uses rollback journals, synchronous EXTRA and fullfsync; a transaction commits before the readable mirror is atomically replaced and its directory synced. Python's standard-library SQLite support supplies this mechanism; there are no new runtime dependencies.

Committed checkpoints are authoritative for controller-owned runtime files. Status and doctor reads do not repair mirrors. The owning scheduler repairs missing, stale or truncated mirrors at startup and archives displaced bytes with a `.recovered-…` suffix. Invalid database identity/schema or unverifiable checkpoints stop dispatch and retain evidence for inspection. Use the CLI for state changes; direct edits to readable runtime mirrors are not checkpoint transactions.

Keep the runtime directory, checkpoint databases and any SQLite rollback journal together in normal backups. SQLite journals must not be manually removed during recovery. This setup recovers interrupted work on existing storage; a failed or lost storage device needs a separate backup restore.

## Attempts, reviews and commits

The backend journals a task's run identity before starting its worker. After interruption, it checks process identity against this boot, retains a permitted candidate patch where possible and records an interrupted outcome. Process leases from a previous boot cannot kill a reused PID. Existing attempt counts, retry bounds and accepted history are preserved; exhausted tasks remain visible for Codex to replan.

Pending review survives a restart. Accepted-run receipts are checked against actual Git history. A prepared promotion can finish when its durable review/check receipts and exact patch match the source state, including a fully staged patch awaiting its commit. A completed commit is reconciled once, even if another owner commit follows it. Unproven or conflicting owner edits are preserved and reported for inspection.

For an approved local microstep, a completed coding handoff can resume its focused checks and independent local review without another coding reservation. Reuse requires the same task, source, profile, patch, instructions and host-check receipts. Rejected or unfinished candidates still need repair; intermediate steps cannot enter final promotion. The durable SQL checkpoint also repairs a missing outcome mirror after a crash, retaining the earlier interrupted outcome as evidence. Routine local supervision has its own launch charge and never consumes a cloud implementation charge.

An interactive Codex audit can inspect a host-completed local checkpoint before product delivery. This uses the same single routine audit reservation per project per rolling seven days; it does not create another automatic model call or budget. Concrete missed criteria create a source-bound merge hold and reach the same task's subsequent worker and reviewer briefs. Local repair can continue with its original charges and scope. The existing one corrective audit must approve the exact final patch and base before delivery; only the accepted matching repair clears the hold. The original checkpoint and the local review that missed the issue remain visible history.

Outcomes and session finalisation are replay-safe. Interrupted attempts are distinct from quality failures, and a handoff/checkpoint is not an achievement. The CSV records accepted commit receipts rather than completion claims.

## Local controls

```sh
midnight doctor --json
midnight status --json
midnight performance --json
```

Doctor reports checkpoint integrity, service state, boot identity and current power/login observations without starting a worker or repairing files. For explicit maintenance, pause and let the controller stop, then run:

```sh
midnight recover --json
midnight resume
```

Recover repairs local state and reconciles project evidence. It does not invoke a model, call the cloud reviewer, or start an assessment. It returns busy while another controller owns recovery. Preserve the working tree and evidence if an interrupted promotion requires inspection; do not reset or clean the canonical repository to clear the message.

## Verification scope

Redundant editing copies are preserved under each run's `quarantine/` directory with a host-owned `candidate-hygiene.json` journal. Recovery may reconcile a preserved-but-not-removed copy only while both hashes still match the recorded reference. Conflicting bytes or unsafe paths hold the candidate for inspection. Never clean away a unique backup to make a task pass scope checks. An unchanged saved candidate is eligible for its first host verification only when no earlier host checks or review exist; unchanged descendants of a verified rejection retain that history.

Tests use temporary state, synthetic Git repositories and real child-process kills. They cover SQLite rollback after an uncommitted transaction, a committed checkpoint before mirror replacement, corrupt/missing mirrors, original override expiry and cancellation, CSV replay, old-boot PID leases, pending evidence and promotion crash points. Deployment checks the installed commands and live worker after maintenance. No physical power cut or live Mac reboot is performed as part of this validation.

Protocol4 retains indexed quality/history and cloud reservation tables separately
from critical JSON snapshots. GitHub journals precede push/create/merge; replay
queries deterministic branch/PR/head identities before repeating a write. A
confirmed remote merge is durable before local synchronization. Status/recovery
never starts a model. Session-history.sqlite3 supersedes whole-ledger snapshots
for CSV schema2; export rebuild verifies its digest. Archive evidence and
checkpoints stay private under Developer.


## Guardian and installation replay

The independent guardian loads a hash-verified recovery bundle from
runtime/resilience. A broken controller import does not load that broken source
into the guardian. The bundle and policy are updated only by a reviewed Codex
release, not by an automatic maintenance proposal. FileVault still requires an
unlock and login after cold boot.

workflow.sqlite3 holds actions, phases, execution groups, incidents, patch
reservations and transition events. Unknown maintenance execution remains charged
and owns its writer lane until its exact source/delivery receipts are reconciled.
A source-install intent and a distinct completion receipt make every adoption
step replayable. The health journal likewise records rollback intent before
changing source. Recovery never restores an older copy of runtime history.

## Evidence-based harness recovery

The version-two coordinator release adds `maintenance_recovery.py`. Its runtime
activation requires the exact installed frozen guardian and a version-two seal
of the current Automation Registry. Without that explicit adoption, the legacy
four-attempt API retains its original behaviour. The workflow snapshot reports
the effective mode; a checked source change alone is not activation evidence.

In the adopted mode, historical attempt counts describe spent reservations.
They are not a lifetime ceiling. Every new implementation needs one unconsumed
host intervention bound to the original incident, source, retained candidate,
failure evidence, protected kernel and qualified cloud model. Local diagnosis
and a separate local reviewer can propose one demonstrably unmet, smaller source
unit. Actual source quotation, syntax identity and saved reviews are checked by
the host. Renaming an incident or function, cosmetic edits, changed timestamps,
elapsed time and repeated prose do not renew a spent unit. Original charged and
uncertain attempts are retained; migration gives them no new initial grant.
Fresh maintenance labels also require diagnosis; they never carry an automatic
initial coding grant. The same syntactic maintenance unit cannot be reused across
different incident labels.

Preparation, coding, regression, full verification, final review, qualification,
private delivery, installation and health are distinct stages. A continuation
owns its action before waiting for a resource. Only the implementation callback,
after resource admission and fresh control/source checks, consumes the proof and
appends the numbered launch reservation in one workflow transaction. A crash
after commitment cannot replay that implementation. Retained bytes may instead
receive host verification. Complete saved review/process receipts recover without
another provider call. Uncertain external results keep their original keys and
charges and cannot be blindly resent.

The guardian inspects original incident history, including terminal old repair
attempts, before new generation. A saved same-base unchecked candidate may receive
verification without coding. A rejected candidate supplies actual findings to
diagnosis. Stale-base work stays explicit evidence and is not silently rebased
or credited as accepted work. Repeated identical evidence parks without more
inference while other eligible projects continue. A complete test receipt proving
only recognised infrastructure errors permits host-only verification retry with
five-minute exponential backoff, up to one hour between checks. A failed assertion
does not receive that treatment. Neither host retry nor elapsed time creates a
coding intervention.

Final acceptance still needs an original-source reproducing regression, complete
confined checks, unchanged native oracles, strongest qualified separate code
review, applicable real worker qualification, confirmed checked private PR,
idle exact-tree adoption and generation health with rollback. Automatic patches
cannot change the maintenance admission kernel, bootstrap, authority or existing
tests. Protected dependencies include the ledger/transaction implementation,
phase guard, source/evidence validators and the separate tool-free maintenance
supervisor; routine backend edits cannot change maintenance admission. This
coordinator release repairs history connection setup, while future changes to
that protected primitive require another reviewed coordinator release.
After healthy adoption, the trusted installer can refresh the frozen bundle
only when all admission and qualification inputs remain byte-identical. Changing
those inputs requires a separately reviewed coordinator release.

Product cloud rescue/audit budgets, account scope, pauses, the saved schedule and
24/7 override, finite phase deadlines and one-local/one-host/two-cloud resource
capacity remain unchanged. Read-only status and deterministic `recover` never
start inference. Runtime `mr_`, `pr_` and `wf_` records must be backed up together.

Initialized history/workflow connections validate their existing identity and
schema without rewriting the database header or recreating tables before the
caller's transaction. Bootstrap sets the application identity and schema together
once. An initialized WAL connection can open while another writer holds its real
transaction; another write still waits or fails at `BEGIN IMMEDIATE`. EXTRA,
fullfsync, checkpoint_fullfsync, writer-only WAL migration and private file modes
remain. An incomplete initialized schema is preserved and rejected. The native
connection regression reproduces the old metadata-lock failure with a real
disposable SQLite writer; it does not establish every production lock owner.

The shared workflow ledger uses SQLite write-ahead logging, enabled only by a
validated writer. This allows status, telemetry and the guardian to read the last
committed state while action heartbeats commit. Writer commits retain synchronous
EXTRA and fullfsync, with checkpoint_fullfsync enabled; authorization still checks
fresh evidence inside its normal transaction. Project history databases and the
critical mirror checkpoints retain their existing rollback-journal configuration.
Read-only workflow APIs do not change SQL rows, schema or journal mode. SQLite may
maintain its own -wal/-shm coherence files; immutable mode is never used for live
state. Short reader lifetimes and SQLite's automatic checkpoints remain in force.

For an active workflow ledger, use SQLite's backup API for a coherent backup.
Copying or hashing workflow.sqlite3 alone does not capture committed WAL rows.
A filesystem backup instead requires a stopped, fully checkpointed store. Do not
remove SQLite-owned journal/WAL/shared-memory files during recovery. These
concurrency and durability rules follow the upstream
[WAL](https://www.sqlite.org/wal.html) and
[isolation](https://www.sqlite.org/isolation.html) contracts.

Rollback retains the exact owned installation fence across failed or interrupted restart requests. Dispatch resumes only after the rejected controller has drained and fresh native controller and dashboard identities prove the restored clean source generation. A foreign or replaced fence is preserved for its owner.

A coordinator source adoption leaves the preceding health record unchanged. Before arming new generation health, its installer unloads and drains the prior guardian with native process identities, holds the audit lock, and establishes the reviewed frozen evaluator. Durable handoff receipts resume interruptions without exposing new health to the previous evaluator. Saved rollback completion also reconciles an interrupted exact owned-fence release.

Guardian quiescence journals the exact native identities before unloading the service, retaining them when an absent service is replayed. A source-bound saved installation may reconcile through the guardian and progress CLI while new coding admission is disabled. This route requires the original checked merge, candidate, model, owned fence and receipts and cannot enter diagnosis, coding, host test execution, fetching or private delivery. Initial health registration and every restart require fresh native process births.

A saved ordinary installation completion does not finish its continuation while its owned fence remains. The executor reconciles completion, restart epoch, source/tree, health generation and exact fence bytes under the source lock, journals the final release effect, then clears only those bytes before marking the original continuation complete.

The coordinator verifies the current checked remote merge before initial source adoption. Once that exact generation is adopted, recovery after guardian quiescence and bookkeeping after fence release use the approved local intent, current source/tree, owner controls and exact receipts. Network loss or subsequent remote progress cannot strand that already-adopted installation or cause another fetch.

A process advisory installation lock serializes coordinator helpers across adoption, restart effects, activation and fence release. Re-entry by the same flow is allowed; overlapping helpers must wait and read current receipts. Final release rechecks the exact source, tree, current verified health and every service restart epoch under the source lock, even when an activation record already exists.

Failed-adoption cleanup can release only a proven never-adopted clean base after idle locks were obtained, no mutation intent or installation receipt exists, original base health is verified and owner controls still match. Rejected or rolled-back generations retain their exact fence for guardian finalization; neither main replay nor a controller-lock timeout can bypass fresh restored-generation proof.

Exact approved policy and permission hashes are re-read during adoption, guardian publication, activation and final release, including long waits. Guardian publication/sealing requires the expected reviewed permission digest; matching maintenance wording cannot reseal changed permissions. Superseded harness continuations drain owned work and retire without refunding reservations or charges, allowing other admitted incidents to proceed. Pauses and unavailable evidence remain resumable rather than being treated as supersession.

The task-completion and microstep admission dependencies are protected from automatic patching and frozen-bundle refresh. A completed rollback preserves rejected commit/tree history, drains and retires pending installation ownership, and atomically establishes verified current health for the freshly proven prior source. Fence release precedes prior-bundle bookkeeping, which the current guardian can resume after interruption. Rejected trees cannot be fetched/re-adopted or selected as ordinary saved verification candidates. Validated nonzero local diagnosis phases park unchanged evidence without another inference; their original charges remain spent and other eligible repairs can proceed.

Immediately before signalling the controller, coordinator adoption rechecks the exact owned fence, current owner controls and reviewed authority under the source lock. A validated failed final-review process is reconciled from its original charged invocation and immutable terminal phase, parked without another provider call, and retained as failure evidence while other admitted repairs proceed. Changed or unbound logs remain uncertain.

Restart intent, fresh ownership/control/authority/health revalidation, the actual service restart and its receipt are serialized against rollback by the source lock. A saved installation continuation rechecks rejected generation health under that lock before any source or fence mutation, including while restored-generation proof is still pending.

Guardian quiescence holds the rollback source lock before checking source, health, fence, owner controls and authority at unload. A failed handoff reloads only the existing hash-verified published evaluator and its validated bootstrap service, preserving the fence and health record for reconciliation.

Before any guardian unload, the coordinator arms a separately loaded one-minute LaunchAgent that retains the exact reviewed frozen helper and installation journal. Its ticks yield to a live installer, restore the verified evaluator and resume that same approved installation after process death, without a model or a new PR. A completed journal retires the service. Ordinary and saved repair installation revalidate current controls, authority, exact fence and generation immediately before each controller/dashboard restart effect.

The independent coordinator consumer is armed from the exact approved plan before controller termination or source mutation, and can reconcile process death immediately after source adoption. Its retirement intent precedes durable removal of the exact owned LaunchAgent definition, and later ticks cannot resume a retired installation. Rollback restart intent is followed by fresh controls, authority, restored source, health generation and exact-fence checks before each service effect.

The ordinary coordinator helper retires its temporary resumer immediately after durable completion. A later tick recognizes the bound completion and release receipts before reading current evaluator/source state, so source advancement or owner edits cannot revive a completed installation or prevent cleanup of its exact owned service definition.

Retained complete host failures reconcile before any retry: assertion failures park unchanged evidence, while only validated infrastructure failures can authorize their source- and receipt-bound timed host retry. This also applies to the original-source baseline. The coordinator resumer freezes the complete dependency archive from the exact reviewed commit and verifies its inventory and hashes with stdlib before importing it; terminal cleanup does not import mutable canonical dependencies.

A complete verifier result without its matching terminal phase remains uncertain, including a crash before phase finalization; it cannot authorize another unchanged verification run. If dispatch was already released but completion bookkeeping was interrupted, the bound release journal permits retirement of only the old temporary resumer across later source changes. A matching original fence still present blocks that terminal shortcut, and foreign replacement fences remain untouched.

Fresh gates also follow durable recovery arming before controller termination, source intent persistence before canonical merge, and release-intent persistence before owned-fence removal. Intervening source, authority, pause, health or fence changes cannot be hidden by an earlier successful preflight.

Deployment-helper adoption revalidates its own source, fence, controls, authority and rejected-health bindings after intent persistence and immediately before merge. A retained successful old-source regression is a terminal non-reproducer that parks the proposal; missing terminal proof remains uncertain. Only a complete validated infrastructure error can authorize a receipt-bound timed regression retry.

A launched verifier with a retained launch/log but no completion receipt remains uncertain even when its phase is nonterminal or interrupted. Once independent recovery is armed, a failed adoption-intent write preserves the exact owned maintenance fence for the scheduled consumer; never-adopted cleanup cannot strand that armed continuation.

Saved maintenance installation fence release binds the original installation
authority and revalidates current permissions, clean installed commit/tree,
health, controls and exact marker after persisting release intent. A historical
completion without an original authority receipt stays held. A host check
interrupted while waiting for resource admission may continue only with a
terminal prelaunch record, no admitted lease, no launch/output/completion files
and no historical execution row. Any launched or ambiguous outcome stays
uncertain and cannot be replaced by a rerun.

Ordinary installation completion and saved reconciliation share one final
authority/source/control/health/fence gate under the existing source lock.
Completion persistence and worker-certificate publication cannot bypass it.
Original-source regression recovery uses the same strict never-launched proof
as baseline, candidate and native checks; an admitted or ambiguous regression
remains uncertain rather than being repeated.

Before initial version-two activation, rollback refreshes restored native
process proof and exact authority, window, pause, source and fence ownership
after completion persistence. Legacy candidate selection reconciles original
action, phase, command, output and completion receipts first. A validated host
assertion or old-source non-reproducer reaches diagnosis rather than unchanged
verification. Missing, altered or ambiguous results block verification; an
original failed infrastructure result cannot authorize a retry without its
bound retry evidence. All original attempts and phases remain immutable.
