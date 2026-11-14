# Continuous quality and indexed history

Midnight treats the health of shipped software as a project-wide admission gate.
An open material defect or material technical debt blocks every new feature in
that project. Exhausting a repair's attempts leaves the finding open. Independent
repairs may continue while other findings, unavailable evaluations or a stale
assessment block feature admission. Other projects retain independent state.

The coordinator owns `quality_profiles/<project>.json`. Its versioned content
lists shipped capabilities, contracts, required host checks, operator workflows
and measurable outcomes. A proposed capability belongs in the backlog until an
accepted commit and its evidence establish delivery. Workers cannot rewrite the
profile. The coordinator increments its version when accepted work changes the
shipped surface or acceptance contract. Every admission binds the canonical JSON
content hash as well as the canonical source commit.

The host now queues each accepted source feature through `profile_manager.py`.
The queue holds further feature admission while the qualified cloud manager
proposes an additive capability, contracts, workflow and evidence connecting the
accepted changed source to accepted changed regression tests. The host verifies
the paths at the accepted commit and that existing protected required unittest
checks select those tests. The proposal cannot remove or replace any capability,
contract, workflow or check, introduce a new command, or use an unrelated test as
coverage. Insufficient coverage or a proposed check change becomes `needs_manager`;
independent repairs continue. New commands require a separate coordinator update.

Successful growth increments the effective profile version and hash, preserves
the original static-base hash and journals the complete accepted proposal in the
shared SQLite history. The private current snapshot is separate from the worker
clone. Applying the update does not establish project health: its new profile
must pass a fresh baseline against the accepted canonical source before features
resume. Runtime snapshots and status use the effective profile read-only; workers
receive a pinned copy. A later static-profile change needs coordinator
reconciliation with retained additions and fails closed until reconciled.

The manager API is `effective_profile(runtime, project_id, static)`,
`queue_update(runtime, project_id, accepted_task, commit, evidence=None)`,
`pending_updates(...)`, `status(...)`,
`validate_proposal(current_profile, pending_update, proposal, verify_source)`,
`apply_update(runtime, project_id, static, update_id, proposal, verify_source)` and
`mark_needs_manager(...)`. Accepted task `files` must be actual changed paths, not
the full allowed scope. The pure verifier callback receives `(commit, path)` and
must establish committed source identity. The host supplies bounded context for
one accepted feature; it need not place the entire growing profile into a model
prompt. Authoritative history stores additive version journals and one current
profile instead of duplicating every lifetime profile version.

## Host adapter sequence

The shared engine does not run shell commands, call models or perform Git writes.
The host adapter supplies real check receipts and owns those effects:

1. Instantiate `QualityCycle(runtime, project_id, profile)`. `profile` is a dict
   or a JSON path. Construction and `status(head)` are read-only and create no
   files when a project has never been assessed.
2. Project task metadata identifies `kind` as `repair`, `feature`, `maintenance`
   or `research`. Missing kind means feature. Supply only tasks whose dependencies
   the host has validated. `done`, `superseded_by` and `blocked` exclude a task;
   `exhausted` prevents another local implementation attempt. A repair must bind
   `finding_ids` for observed shipped-product defects, not an unfinished feature
   draft. `admit_task(task, source_task=None)` persists issue/episode identity.
3. `next_decision(head, tasks, local_available=True, cloud_available=False)` emits
   `action` equal to `assess`, `implement_locally`, `cloud_rescue`, `wait` or
   `needs_manager`, plus `reason`, source/profile/revision and the selected `task`
   or `assessment_kind`. It records the decision. Available independent repairs
   and finding-bound maintenance precede assessments and features. Independent
   research and upkeep remain eligible after due assessments while material debt
   holds features. The coordinator owns this classification. `cloud_rescue` additionally requires the
   host to set `rescue_eligible` after the shared rescue policy qualifies the
   episode; merely being exhausted or having a cloud account is insufficient.
4. For `assess`, execute the profile's required checks against the unchanged
   canonical head using host deadlines. Save complete output, expected exit code
   and actual completion status. Call
   `record_assessment(head, kind, outcome, evidence, checks, findings=None,
   metrics=None)`. Check receipts contain `id`, `status` and `evidence`; a passed
   receipt uses `status: "passed"`. Outcomes are `passed`, `no_change`, `failed`,
   `incomplete` and `unavailable`. Both successful outcomes require every required
   check and saved evidence. A model's conclusion alone cannot establish health.
5. Baseline establishes the initial periodic clocks. Every accepted head needs
   `after_accepted` evaluation; daily, weekly and monthly checks become due after
   24 hours, 7 days and 30 days respectively. These are elapsed windows. They do
   not depend on a calendar week, midnight, or new commits. The latest failed,
   incomplete, unavailable or expired assessment blocks features. A profile
   change requires a new baseline. The initial baseline is also the initial
   weekly/monthly reference; subsequent periodic reports must include current
   findings and indexed metrics for their respective windows.
6. Before feature implementation, call `issue_feature_ticket(head, task_id)` and
   save the returned ticket with the host-owned attempt receipts. It binds the
   project, task, exact canonical head, profile hash, findings revision and
   supporting assessment. Default lifetime is one hour; it cannot outlive quality
   freshness. Do not consume it at dispatch.
7. After full candidate checks and independent review, call
   `revalidate_ticket(ticket, current_canonical_head, task_id)` and then
   `consume_ticket(...)` immediately before promotion while the host holds its
   existing canonical-promotion lock. Consumption rechecks the entire gate in a
   SQLite write transaction and is one-use. The host records a durable promotion
   intent and uses exact-head Git checks; SQLite cannot lock a separate Git
   repository. A crash after consumption does not grant a second attempt. Recovery
   can reconcile only the already-journalled exact promotion, or reassess and
   request a new ticket. Repair promotions still require full host checks and
   review, but do not need a feature ticket.

`status(head)` includes `feature_allowed`, `reasons`, `assessment_due`, the current
and periodic assessment receipts, `findings_revision` and up to 100 material open
findings with a full count and truncation indicator. This is observation only.
When no useful task exists, `next_decision` saves an `idle_assessment` observation
and waits. It does not execute or refresh quality checks, count an accepted change,
invent an improvement, or continually manufacture implementation tasks.

## Stable findings and issue episodes

`upsert_finding({id, title, evidence, material, ...})` requires an explicit stable
identifier or a stable `key` that is hashed into one. The key describes the
contract defect and does not depend on a task title. It preserves open state and
episode identity. Identical replay does not change the findings revision.

`admit_task` inherits issue and episode identity through `retry_of.task_id` or
`source_task`, including renamed and split tasks. Changing a name or model does
not reset the issue's attempt eligibility. The rescue policy's separate `cr_`
tables own attempt qualification and quota. An unimplemented feature draft has
its own issue episode; its implementation failure alone is not a defect in the
shipped product.

`resolve_finding(id, evidence)` requires `accepted_commit`, `checks_passed: true`
and `review_approved: true`. The host must supply actual saved acceptance
receipts. A passing general assessment never silently closes a finding.
`reopen_finding(id, {head, regression: evidence})` is allowed only after verified
closure and retains the prior episode's complete history. A new episode requires
a demonstrated later regression, not an exhausted retry allowance. Every material
finding state change invalidates older admission tickets, including a finding
opened and then resolved after ticket issuance.

## Indexed history and durability

Task-boundary maintenance also replays legacy `attempt_finished` host events in
pages of at most 500 events and 2 MiB. It records the original closure, source
offset and hash beside the run before advancing a resumable byte cursor. An
unterminated appended line waits for the next pass. Subsequent indexing reads
this receipt, so older closed runs do not reappear as active after migration.
Age and worker exit codes alone never establish closure; accepted counts still
require an accepted commit receipt. Pending review/delivery retains its state.
Dashboard and status reads do not perform this replay or scan lifetime logs.

`history_store.HistoryStore(runtime)` uses `runtime/history.sqlite3`. This is a
separate database from `durability.py`'s `checkpoints.sqlite3`; no checkpoint schema
or readable critical runtime file is replaced. `qc_` tables own quality data and
indexed runs. Shared services can own independent prefixes such as rescue's
`cr_` tables. SQLite transactions use `synchronous=EXTRA` and `fullfsync=ON`;
databases are mode 0600. The file and any rollback journal must remain together in
backups. Readers neither migrate schemas nor repair state. Unsupported database
identities or quality schema versions stop access for coordinator inspection.

Public history APIs:

- `record_run(project_id, run_id, payload, created_at=None)` idempotently indexes
  a host run and can advance pending work to a terminal outcome. `accepted`
  requires an `accepted_commit` or `commit` receipt. A saved accepted commit cannot
  be replaced or downgraded. `worker_kind` distinguishes `local` and `cloud`.
- `page_runs(project_id, limit=50, offset=0, before=None)` returns indexed rows
  including their original payload. `before=(created_at, run_id)` is an efficient
  stable cursor for deep history. Offset remains available for existing UI pages.
- `counters(project_id, since=None, until=None)` calculates grouped outcome and
  worker counts in SQL, returning accepted counts/rate and separate local/cloud
  attempts. Time bounds are inclusive `since`, exclusive `until`. It never opens
  per-run directories. A caller can request the preceding 7 or 30 days directly.
- `append(kind, project_id, payload, event_id=None, episode_id=None, task_id=None,
  occurred_at=None)` stores an immutable event with replay conflict detection.
  `record_attempt`, `record_guidance` and `log_git_event` are explicit wrappers.
- `query(project_id, kind=None, episode_id=None, limit=50, cursor=None, since=None,
  until=None)` returns bounded event pages; `cursor` is the last sequence number
  of the preceding page. Every public page caps its limit at 500.
- `transaction()` supplies a connection so importing historical run receipts or
  recording coordinated events can commit as one atomic batch. `record_run` and
  `append` accept `connection=con` for that purpose. Missing or conflicting source
  receipts must remain visible as incomplete imports, not inferred achievements.

The store retains history; it never deletes old runs to keep dashboards fast.
Raw evidence retention/archival remains a host policy. It should preserve accepted
receipts, failure/guidance lineage and manifest hashes before removing expendable
logs. SQL indexes cover project/time pages, outcomes, events, current findings,
assessment kind/head and issue episodes. No status API walks every run folder.

## Reuse and verification

Python's standard `sqlite3`, `json`, hashing and `unittest` libraries provide the
storage, serialization and tests. This small adapter owns Midnight-specific
quality/episode semantics; adding an ORM or external queue would introduce a new
runtime dependency without improving the existing serialized host workflow. No
third-party source was copied and no upstream licence obligations were added.

The isolated tests use two fake projects, fake clocks and temporary databases.
They cover missing/stale/failed evaluation gates, independent repair admission,
exhausted repairs blocking features, source/profile/revision ticket binding and
single consumption, evidence-backed closure and regression episodes, task rename
lineage, transaction rollback, concurrent writes, replay, and 100,000 indexed runs
spanning 36 thirty-day months. The scale rehearsal verifies bounded history pages
and SQL index selection; it is not a claim of 36 months of real operation or of
physical power-cut validation. The host adapter separately verifies actual
project checks, promotion and recovery integration.
