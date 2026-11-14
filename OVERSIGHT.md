# Midnight oversight

Use `midnight oversight --json` for the same evidence document served at
`http://127.0.0.1:8766/api/oversight`. Each project and task includes `explain`.
The dashboard reads this cached document and the independent cached telemetry
feed. Reading either endpoint never requests a model, benchmarks a worker,
replies to a message or changes a control. `midnight oversight` prints a compact
text view. `midnight status --json`, `repair-status --json`, `incidents --json`
and the retained receipts remain the focused investigation tools.

A responsive controller, a resident model and accepted product work are separate
observations. Count accepted commit receipts as delivery. A proposal, attempt,
passed local check or pending review does not establish acceptance.

## Reading the document

`operation` describes current activity, the next window and whether it is blocked.
`projects[].control` comes from `control_status.project_control`.
`projects[].repair_status` retains the progress-recovery scopes and admissions.
`projects[].tasks` includes the entire backend task list with plain state,
explanation and the latest attempt in the displayed history page. Task holds,
the current project hold tracker, events, acceptances and session rows retain
their original identities. `models` comes from the model manager;
`cloud_providers` comes from cloud policy and its existing invocation receipts.
`coordinator`, `guardian`, `incidents` and `owner_actions` expose their existing
journals and workflow metadata.

History is bounded for display: 200 coordinator entries, 200 owner actions,
150 events per project, 50 recent attempts per project and the status collector's
recent session rows. `history_limits`, `history_coverage` and
`next_history_cursor` make that scope explicit. A missing last attempt means it
was not in this page, not that it never ran. Download `/api/sessions.csv` for the
session ledger. The retained project/run endpoints support earlier history and
full receipt inspection. Invalid or unavailable project evidence is reported as
`oversight_error`; unavailable evidence must not be read as an empty successful
backlog. HTTP 503 means the current observation cannot be served. A stale cache
changes the operation to updating rather than claiming live activity.

## Task states

| State | Meaning | Action |
| --- | --- | --- |
| ready | No retained task gate excludes selection | Let the scheduler and project quality engine select it inside the permitted window |
| running | The backend retains an active attempt | Check the verified current phase; an attempt record alone does not prove live inference |
| awaiting review | A saved candidate awaits review or delivery | Inspect its current phase and receipts; retain all final checks |
| waiting for coordinator | The current recovery scope needs guidance or a harness decision | Inspect `midnight coordinator` and its latest decision; owner escalations remain in Needs you |
| held | A task hold or unavailable recovery proof prevents selection | Read the exact explanation, scope, source and diagnosis; resolve the cause without clearing charges |
| waiting for dependency | A prerequisite is unaccepted | Finish the prerequisite; do not rename the dependent task to evade it |
| retired | A coordinator decision removed the task from ordinary selection | Use Restore task or `midnight tasks --project PROJECT --unretire TASK` if it remains necessary |
| superseded | A recorded successor carries the work | Follow the successor while preserving the original task and failure history |
| accepted | Accepted evidence marks the task done | Inspect the accepted commit receipt when verifying product delivery |

Task-specific holds retain their recorded source, task fingerprint and timestamp.
The backend rechecks source/contract and its configured hold lifetime before
selection; the oversight document also exposes the raw retained record for
inspection. Retiring a task never accepts it or satisfies a dependency. Owner
restoration does not renew any spent proof or launch.

## Project controls and controller phases

Control `state` is `active`, `idle`, `waiting`, `recovering` or `blocked`.
The `code`, `explanation`, `recovery_owner` and `next_retry_at` explain the next
step. `automatic` means deterministic reconciliation or a scheduled retry owns
the next step; `codex` means coordinator or operator attention; `owner` means
the owner's explicit choice. A hold older than one hour is surfaced in Needs you.

| Code or phase | Meaning and next step |
| --- | --- |
| local_worker, cloud_implementation | A verified implementation process is active; wait for its bounded handoff |
| host_checks, independent_review, local_review | Checks or independent review are active; inspect the terminal receipts before claiming success |
| local_planning, task_management, management_active, preparing_workspace | Bounded planning or setup is active; watch the phase deadline and progress |
| github_delivery | Checked private delivery is being reconciled; do not repeat an uncertain push or merge |
| pending_continuation | Saved review or delivery remains; reconcile the exact retained work |
| phase_deadline | An owned phase expired; deterministic cleanup must finish before new work |
| stale_preparation | No fresh supervisor proves setup is progressing; inspect recovery and incident evidence |
| owner_pause, paused | Owner control prevents dispatch; resume only under owner authority |
| activation_pending | Reviewed runtime activation is pending; let its existing installer reconcile |
| INSTALLING | Source installation owns the fence; retain its exact ownership and receipts |
| orchestration_error, needs_attention | Inspect the complete error and incident; use model-free doctor/recover before any new repair |
| guided_retry_blocked | A journaled successor is needed; coordinator guidance cannot waive that contract |
| rescue_manager_held, manager_hold | The manager retained a same-issue hold; inspect its evidence and guidance |
| profile_evidence_missing | Required profile evidence is unavailable; restore the evidence through its existing bounded path |
| profile_manager_hold, profile_needs_manager | Profile coverage needs a coordinator decision |
| coordinator_required, needs_manager | The task has exhausted its available interventions; the coordinator must provide a bounded decision |
| missing_failure_evidence | Required failure evidence is missing; inspect saved receipts rather than inventing a diagnosis |
| planner_hold, quality_wait, no_useful_work | Quality planning cannot currently admit work; inspect `planning_wait`, quality findings and task admissions |
| scope_clear | Scope is clear and reassessment has a recorded future time; wait until that time |
| failure_backoff | A bounded retry delay is active; wait for `next_retry_at` without renewing charges |
| outside_window, waiting_for_midnight, finished_at_09:00, finished_at_managed_deadline | The permitted window ended; inspect `next_start`, cutoff and any override |
| waiting_for_project | Controller is responsive but no eligible project action was launched; inspect each project's explanation |
| working | Controller observes owned project jobs; their actual phases establish what is running |
| model_maintenance, cloud_model_maintenance | A scheduled qualification or maintenance activity owns resources; status reads never start it |
| low_disk_hold | Fewer than five GiB are available; preserve evidence and resolve disk capacity |
| no_active_phase, unobserved | No verified current phase is available; inspect freshness before inferring inactivity |

Recovery admissions expose `ready`, `diagnosing`, `needs_codex`,
`needs_harness_repair` or their retained blocked/wait state and exact reason.
A ready proof is one-use and bound to its original scope, source and model.
Unknown or interrupted launches stay charged. Redeployment, elapsed time and
renaming never restore admission. See RECOVERY.md and PROGRESS_RECOVERY.md for
the complete recovery contract.

## Cloud models and coordinator

The owner selected Claude Fable 5.2 (`claude-fable-5-2`, max effort) as main and
Codex (`gpt-6-astra`, ultra) as fallback. The Main selector and
`midnight cloud --primary claude|codex` persist selection in
`runtime/cloud-selection.json`. Local Qwen3.8 remains owner-pinned. A provider
breaker can be closed, open until its recorded reset, or unavailable with a
recorded failure. A closed breaker alone is not proof of a successful call.
Last success/failure and daily invocation counts come from retained receipts.
Unknown calls stay distinct from completed calls. Model substitution is refused;
Claude implementation remains disabled until its sandbox has been proven.
Maintenance repair and qualification retain their qualified Codex profile.

`midnight coordinator` shows mode, decisions and pending escalations.
`--run` requests one bounded pass; `--mode shadow|live|off` changes mode;
`--overrule ID` reverses the unconsumed effect of a live decision. The controller
starts a detached pass at most every 600 seconds in an active window. The
coordinator holds its own lock. Pause, installation and pending activation
prevent spawning and applying effects.

| Coordinator state | Meaning |
| --- | --- |
| shadow | Proposals are journaled without applying guidance or retirement |
| live | At most three decisions per pass and thirty per UTC day are applied within host bounds |
| off | No pass is started |
| busy | Another pass owns the coordinator lock; no duplicate call is started |
| guidance | One source/model-bound coordinator_guidance proof admits one local continuation under the original contract |
| retire | Ordinary selection skips the task; history remains and the owner can restore it |
| owner | No task change is made; the owner's question appears in Needs you |
| error | No successful decision is recorded; inspect its actual provider/validation/effect error |
| owner overrule | Unconsumed guidance is withdrawn or a retired task is restored; a launched proof remains spent |

Every live decision requests a Mac notification. A notification helper receipt
establishes the request, not that the owner saw it. No coordinator decision can
change acceptance tests, models, schedules, delivery holds/releases, merge or
deploy policy. The initial shadow proposals require the owner's review before live
mode is enabled. An explicit shadow `--limit` may inspect the whole current
backlog; normal live passes retain the three-decision limit.

## Owner controls and security

The dashboard binds only to `127.0.0.1`, normally port 8766. Each server start
generates a random token embedded in its page. `POST /api/control` requires that
token in `X-Midnight-Token`, the exact local Host and Origin, a bounded JSON
body and the allowlisted action and fields. Invalid authentication or origin
gets 403; malformed/non-JSON or invalid action fields get 400. Other write
methods get 403. No cross-origin access is enabled. Refresh an old browser tab
after a server restart to obtain its new token.

Allowed actions are set_primary_provider, overrule_decision, unretire_task,
pause and resume. Pause/resume shares the CLI implementation. Every change is
appended to `runtime/owner-actions.jsonl` and emitted into the workflow event
history. If a control reports an uncertain failure, inspect the action log and
current state before retrying. The operating picture is a reporting record;
it grants no account or execution authority.

## Guardian and rollout

Guardian state `observing` means the last audit found no issue, `attention`
means its recorded issue list needs inspection, `paused`/`outside_window` mean
controls or timing constrain work, and `unobserved`/`unavailable` mean no current
health claim can be made. Open or held incidents retain their original lineage;
resolved history is not deleted. Numeric Mac telemetry retains observation time
and missing values; a missing reading is unavailable, never healthy zero.

Edit only in an isolated worktree. Run the full suite before deployment and
compare with the documented three pre-existing errors. Fast-forward the live
checkout only when running workflow phases equal zero and Ollama lists no
loaded model; never interrupt an in-flight model call. Reinstall the guardian
from a clean tested tree only after its existing authority digest matches.
Back up the manifest and plist first, use Python 3.14, then inspect a fresh
`issues: 0` audit. GitHub push and protection bypass follow the owner's explicit
approval gates; local deployment is not a remote merge.
