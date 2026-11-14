# Midnight backend contract

Worker-quality API **4** adds [progress-based local recovery](PROGRESS_RECOVERY.md)
to protocol5. Registered local backends enroll immutable existing tasks, classify
actual receipts and require one-use host evidence for each new coding launch.
This supersedes the numerical local ceilings described in historical continuation
sections below. Original charges, task scopes, frozen paths, checkpoints and
complete final gates remain. Routine diagnosis/review stay local; Codex alone
repairs the harness through the existing charged checked-maintenance path. Cloud
rescue and audit conditions/rolling quotas do not increase. Every backend must
attest the current recovery module hash before dispatch.

The [persistent agent workspace](AGENT_WORKSPACE.md) adds role-bound `read_chat`
and `post_chat` tools, frozen advisory context for local planning/review, and
host-recorded worker handoffs. The shared model contract attests these tools and
workspace modules. All registered projects inherit the hooks without a
product-specific adapter. Chat capabilities cannot record architect decisions;
live Principal Architect delivery holds also apply to uncommitted recovery
promotions. Ordinary quality and publication gates remain authoritative.

Managed local implementation uses Ralphy with the shared typed worker. The host
supplies a manifest outside the clone with exact writable files and a fixed test
command. Qwen selects read_file, edit_file, run_tests and finish JSON decisions;
decisions cannot contain source code. A separate bounded edit response supplies
one exact old_text/new_text replacement after a fresh read. The host requires one
unique match, rejects stale/ambiguous/no-op replacements and preserves all bytes
outside that snippet. An absent file needs an explicit read before creation.
it cannot choose shell commands or write its own completion marker. The worker's
fixed check parses syntax without executing candidate code, forking, network,
private home reads or candidate writes, with bounded output and time. Python uses
ast.parse; JavaScript/TypeScript uses the project's existing locked compiler.
Completion requires a real permitted edit and a successful syntax check after
the last edit. The parent verifies manifest, model, final file hashes, test log and
handoff, then runs the original focused/full semantic host checks before the
applicable independent review. A syntax pass alone cannot establish acceptance.
Exit zero alone is unfinished. An actual
coding launch cannot use the first-host-verification exception to resubmit an
unchanged retained candidate. Full scope, frozen source and final checks remain.
Standalone compatibility invocations retain their previous process protocol;
every Midnight-owned managed action uses the stricter typed contract.

An explicit owner direction may authorize one additive two-launch exception for
the exhausted current microstep after a verified worker fix. It never modifies
the original plan or charges, grants another step's allowance, or renews cloud
limits. Two actual disposable production-worker edit/test/handoff probes and
separate independent review are required. Exact worker sources, model, proof and
owner direction are revalidated before either extra launch; changed evidence
holds dispatch before charging. The exception is journalled once per issue.

After a retained test-only narrow continuation exhausts, the local manager may
propose one immutable decomposition into two to four genuinely smaller steps.
A fresh independent local review must approve it. The same issue, task, canonical
base/profile, initial checkpoint, single writable test file and frozen production
remain bound. Each step has at most two durably reserved local launches; original
dispatches and cloud reservations are never refunded or renewed. This separately
reviewed supplemental local-only ceiling supersedes the earlier continuation
ceiling; it does not change functional acceptance or cloud limits. Python and
configured JavaScript focused runners reuse the existing backend test recipes.

Intermediate steps receive actual focused checks, exact-byte/frozen-path
revalidation and independent local review, then retain a private checkpoint only.
They do not count as product acceptance or quality failures. No intermediate
candidate may enter saved-draft promotion. The final step still needs the original
complete task, every full protected check, independent rated review and ordinary
delivery. An exhausted decomposition cannot be replaced by another or by a renamed
task. The dashboard distinguishes checkpoints from accepted changes.

The independently owned project action performs fresh capability validation before
any product process launch. The central controller does not duplicate that import;
a slow project's admission therefore cannot abort other active work. Repair-only
projects with no open findings use a visible five-minute reassessment interval,
with immediate eligibility for changed direction or newly ready work. Retired
repair contracts remain history rather than appearing in the ready queue.

Routine planning and independent review use fresh, tool-free structured Ollama
requests through `local_supervisor.py`. The pinned selected model, complete prompt,
schema, process, output and metrics are hashed into private receipts. Local phases
use the shared GPU lease and local-code file lock, never a cloud fallback. Full
host checks, frozen candidates, protected quality profiles and source-bound
delivery still decide acceptance. Ratings and sampled audit reservations live in
the project's private indexed history database. See the quality policy in README.

Manager-owned repair holds now dispatch a bounded manager diagnosis from the
retained source and receipt evidence. A newer passing assessment does not erase
the failure log referenced by an open finding. Guard terminal receipts distinguish
an interrupted command from a completed command returning the same exit code.

After ordinary repair contracts exhaust, the manager may propose one independently
reviewed local continuation of a verified rejected candidate per issue. It retains
the original episode, all failures and cloud reservations. Writable worker paths
must be a strict subset of the original scope; other candidate paths are frozen by
hash. At most two additional local dispatches are reserved durably before launch,
including unknown or cancelled launches. No intermediate checkpoint is promoted.
The entire final candidate still needs protected full checks, exact-byte replay,
independent product review and normal delivery. A renamed task cannot renew this
grant. Correcting a demonstrably false manager-specified reproducer requires an
explicit original/final comparison and independent review of actual host evidence.

One separately reviewed host-bug replay is available only when both additional
dispatches are already charged and exact failed task, outcome and compiled brief
receipts demonstrate a corrected host instruction defect. It pins the corrected
backend hash and immutable comparison/review, permits one more local dispatch
ever for that issue, and retains all original dispatches and cloud charges. It
cannot renew a task, expand scope, promote an intermediate patch or waive checks.

Legacy host-generated findings can be explicitly invalidated only after inspection
of every observation in their current episode, exact host interruption provenance,
a complete identical-source/profile pass and separate review. Invalidation is an
audited classification correction, never an accepted product repair. Old repair
contracts are retired against their pinned finding episodes so later genuine
regressions receive fresh planning without resurrecting exhausted old contracts.

The shared implementation is now `project_backend.py`. Each project has a thin
binding that calls `load_backend` with its canonical root, stable project ID and
protected configuration, then `entrypoint`. Each instance owns separate globals,
state, issue identity and stop flag. Only the actual CLI entry installs its stop
handlers. `MIDNIGHT_SHARED_ROOT`, supplied by the coordinator for capabilities,
recovery and dispatch, selects this installation. The worker-quality descriptor
also binds the shared core's SHA-256; stale paired installations fail admission.

ExampleApp preserves its existing default checks and private GitHub delivery.
SecondApp binds an isolated local branch, a supported pinned Node runtime and locked
dependencies, with explicitly scoped local checks and no GitHub delivery. See
SECONDAPP.md. Both use the same licensed Autoissue/Ralphy loop under
`ralphy/`, retry evidence, durable state, review, pause/deadline and recovery code.

Coordinator `worker_allowed_patterns` is an enforced intersection with task
scope, not merely prompt advice. Optional strict file selection rejects task
globs. Retry compatibility includes each binding's configured acceptance
directory, configuration, brief and protected instructions. An unrelated source
advance can retain a candidate; a changed acceptance contract needs explicit
coordinator reconciliation and fresh verification.

## Evidence-only repair follow-ups

A coordinator can admit a `verification_only: true` repair with exact `retry_of`
lineage and a named check recipe in protected `verification_checks` configuration.
Cloud planners cannot generate that authority. It must have an exact retained
candidate and receives at most one host verification cycle per immutable task;
failed contracts are held while independent repairs continue. Pending review or
delivery can resume their original receipts. No local/cloud implementation is
launched, no local attempt is charged and no cloud implementation reservation is
created or refunded. Normal profile checks, independent review, source checks
and configured delivery still establish acceptance. Original failed runs remain
history. Current ExampleApp V001/V002 collect the missing explicit receipts for
already code-reviewed repairs.

Reviews now receive actual combined stdout/stderr as well as command/exit
receipts from primary and protected profile checks. The combined output budget
is 64,000 bytes, with tails labelled and full files retained privately. Focused
test selection derives only from scoped files and a coordinator-defined runner;
ordinary complete host gates remain mandatory.

Node validation additionally permits only each exact host check-output file and
metadata traversal of approved paths. SecondApp's TSX children deliberately clear
TMPDIR; its binding permits only the actual user's verified TSX cache under
`/private/tmp`, never the whole temporary tree. Other coordinator state remains
denied. Project dependencies are reused read-only only when the protected lock
matches; workers never install them.

Midnight owns shared engineering controls for **every registered project**. Projects own their product code, writable file scope, acceptance oracles and implementation backlog. ExampleApp is the current adapter, not the scope of Midnight's reliability policy.

## Admission: protocol 5

Every projects.json entry declares `backend_protocol: 5`. Before recovery or dispatch, Midnight invokes the registered controller's read-only `capabilities` command. It must return protocol 5, `recovery: "model-free"`, the exact `worker_quality.model_contract()` descriptor, and the exact current `worker_quality.policy()` descriptor, including its instruction and installed-module hashes. A missing/stale response holds dispatch. This verifies version adoption; coordinator review and conformance tests still establish that a backend uses the controls correctly.

Backends implement `managed-once --until ABSOLUTE --brief FILE [--limits FILE]` and model-free `recover`. The direction snapshot includes both the shared open-source reuse policy and worker-quality policy. Backends verify these descriptors and propagate them into worker/planner/reviewer context. Task code cannot edit either policy or bypass project acceptance.

## Shared worker quality

Import `worker_quality.py` from the sibling Midnight installation. Use its numbered repair plans, candidate-progress receipts and no-change diagnostics; do not copy a private variant into each product. The module has no ExampleApp assumptions and is tested against independent synthetic project/task names.

- Retain the last structured independent review findings even when a later infrastructure/no-change message is recorded.
- Seed permitted prior candidate code only when its provenance, file scope and base remain valid. This remains the adapter's Git/workspace responsibility.
- Save a host-owned `repair-plan.json` and put its numbered findings prominently in the worker task.
- Compare the submitted patch with the seeded candidate. Save `retry-progress.json` with the seed identity, hash and actual delta. An unchanged rejected patch stops before host checks/cloud review. A host-owned `retry-verification.json` may permit the first verification of a saved draft with no previous host checks or review. Carry verified rejection history through unchanged checkpoint descendants; an interrupted retry cannot erase it. A changed patch still needs all checks and review.
- Include the saved findings and actual delta in independent review. A model's claim to have repaired something is not evidence of acceptance.
- Diagnose no-change exits and unfinished tool-call fragments in `worker-diagnostic.json`; preserve unsuccessful attempts in history.

Quality API version 2 also attests `candidate_hygiene.py`. Before enforcing file scope, backends may quarantine an untracked `.bak`, `.backup`, `.orig` or `.fixed` copy only when its associated source is permitted and unprotected and its bytes exactly match that source's host-known base, seeded or current version. Retain the copy outside the clone and journal its hash and reference in `candidate-hygiene.json` before removing it from the clone. Unique content, tracked paths, symlinks, hard links, traversal and oversized files remain blocked. This is mechanical evidence preservation; it neither edits source nor establishes acceptance. Include the receipt in independent review.

The host now selects the qualified local configuration through the shared model manager; see [MODELS.md](MODELS.md). Every admitted backend must invoke `MIDNIGHT_LOCAL_CODE_BIN` when set, preserving its argv and ordinary local-code GPU lock, and retain the supplied `worker_model` brief descriptor in each run's `worker-model.json`. Do not hardcode a model or change the selection from a product task. Model changes happen only between tasks and still require all project checks/reviews. Retry allowances, schedule, override expiry and recovery controls remain in force.

Give workers one observable behaviour, concrete examples and a focused test command. Prefer one implementation file and one test file, and split independent validation/storage/presentation/CLI work with dependencies. Keep the full original integration gate on the final successor. Save complete host logs, but use shared `check_feedback` to send only failing check output to the worker. Complete independent reviewer findings must still be retained and addressed.

## Repair planning

Lead retry briefs with `repair_preamble` and the numbered unresolved findings,
before historical task prose. Retained staged work is a rejected candidate, not
a completed implementation. Reissued worker guidance replaces earlier guidance
text in the brief while parent tasks and journals retain it. Fallback management
receives `RescueStore.failure_evidence`: at most two actual initial and two guided
host failures, excluding provider faults. A legacy counter-only manager hold may
be reconsidered once with these receipts; a receipt-aware hold remains cached and
is excluded from implementation selection. This does not reset retries or quotas.

Quality API version 3 keeps each successor's own latest exact-contract candidate
ahead of its parent seed. Use `substantive_failure(directory, feedback,
interrupted=False)` for local failure classification. A host `ready_for_review`
handoff counts an unchanged submission even if the deliberate process stop exits
130 or -15. A host-only verification of a saved candidate, a cloud implementation,
provider failure or controlled interruption is not another local failure.
Journal `implementation-backend.json` before saved-candidate verification too.
After two initial failures, saved concrete guidance and two guided failures, do
not keep dispatching the same local contract. A manager's cached rescue hold
excludes that task while independent eligible repairs continue; it must not
create another implementation cycle or project-wide error/backoff.

The bounded episode importer reconciles pre-v3 host receipts once. Its correction
can exclude a mistakenly counted host-only run without deleting its original
event/evidence. Guidance issued before two actual failures is retained in an
invalidation event and must be reissued after the corrected gate. Existing cloud
reservations remain charged. Renames do not create another episode or quota.

Codex may replace an exhausted broad task with smaller coordinator-maintained tasks. Keep the original task and attempt receipts; record `superseded_by` in the backlog and migrate dependants explicitly. Never reset tries or describe supersession as completion. The final replacement must retain the original requirements and regression oracles. A new backend must respect supersession metadata and dependency ordering.

A successor may explicitly bind `retry_of: {task_id, task_sha256}` to the exact preserved source task. Use the shared source_candidate selector, retain the source's complete findings, then independently validate patch provenance, allowed paths, protected tests and base compatibility. Unrelated accepted work can advance HEAD if the candidate paths and contract/acceptance anchors remain unchanged; every resumed candidate still needs fresh full checks and independent review. This is coordinator metadata, never a field generated by the bounded cloud planner or product worker.

## Evidence and observation

The existing runtime protocol retains `state.json` with accepted receipts, done/tries, feedback, active attempt and pending review; per-run folders contain task, worker/check/review/outcome receipts. Boot-bound leases, transactional checkpoints and promotion reconciliation follow RECOVERY.md. Backends store evidence in their registered private Developer state directory.

The project-agnostic status, performance, CSV ledger and dashboard enumerate projects.json. They display all registered projects and aggregate receipt-based outcomes, with project-specific failures and queue details. The loopback dashboard cannot dispatch work, alter controls or access arbitrary files.

Before adding a project: supply its contract adapter, run isolated admission/recovery/quality checks, verify policy propagation and independent acceptance, then obtain admission through an owner-directed task. No unrelated project is added by this release.

Protocol 4 also retains `worker-completion.json` with `response_observed` from
actual local worker events and `finished` from the verified typed handoff for managed local work. Do not
treat a prepared brief, clone, client startup failure or missing receipt as a
successful canary observation. The inherited `MIDNIGHT_WORKER_DESCRIPTOR` pins
the model and promotion generation for the task; the shared entry verifies it
before executing the typed worker. Interactive local-code is unchanged.

Protocol 4 additionally attests quality_cycle.policy(), cloud_policy.policy(),
cloud_rescue.policy() and git_delivery.policy(). The managed brief binds the
coordinator quality profile, task-pinned cloud descriptor and repository mapping.
Backends must call shared quality admission/revalidation and rescue accounting,
index run transitions, retain never-checked drafts before cloud escalation, and
accept GitHub delivery only after a confirmed remote merge. Pending deliveries
resume from the original patch/review receipt without re-running implementation.

Phase bounds are local implementation720s, cloud planning/guidance/review900s,
exceptional implementation1200s, and an enclosing90-minute cycle. Absolute
schedule/override cutoff always wins. Pending evidence survives phase expiry.

## Stall recovery and evidence

Selection and seeding share read-only candidate preflight. An unusable old draft remains evidence but cannot suppress required guidance. Guided successors are deterministic and recover committed SQL guidance without a duplicate call. Same-issue attempt and reservation history remains authoritative.

The shared evidence collector passes actual canonical failures and focused source to no-tool managers, with exact source/profile binding, secret redaction and whole-JSON bounds. Rescue holds are keyed by evidence; new verified canonical evidence permits reconsideration without resetting retries. Existing eligible repairs precede unbound planning. Per-finding dispatch receipts pin episodes, and old accepted runs cannot close newer regressions.

Status includes a read-only control object describing blockers, recovery owner and next recheck. Preparation requires a fresh verified supervisor; an inactive task is labelled as the last task. Status and dashboard observation never dispatch a repair.


### Durable phase ownership (protocol 5)

Managed dispatch uses one background action per project, an immutable brief and
pinned model descriptor. The child registers its parent-bound lease before
recovering product state. Commands self-register their execution group before
work, retain the resource descriptor in a fixed guard and persist observed
children. A terminal task result does not erase a live execution lease. The
controller and independent guardian reconcile both active and terminal orphans.

Local GPU capacity is one, host-check capacity one and cloud capacity two.
Admission is serialized with an execution fence, while source adoption has a
separate deadline-aware lock. An owner pause, project removal or absolute cutoff
stops owned work; session CSV finalization waits for draining. Completed, exact
cloud decisions are reused only with matching source/input/model and immutable
process/result/tool-validation receipts.

Automatic core maintenance is limited by recovery_policy.json and the durable
incident store. It cannot modify its frozen recovery bootstrap, policies,
permissions or existing acceptance tests. After explicitly reviewed version-two
adoption, new harness coding requires one-use verified intervention evidence;
original attempt counts remain spent history. Saved stages continue separately.
Unchanged failures park while other eligible work continues. The legacy API keeps
its four-attempt behaviour before adoption. See RECOVERY.md for migration and the
unchanged private PR, installation, health and rollback gates.
