# Local model maintenance

## Explicit Colibri / GLM owner default

The owner can select the already acquired GLM-5.3 checkpoint as Midnight's
persistent coding default. This selection has no expiry and stays active until
explicitly changed. It is an owner choice for real registered work, not a passed
quality benchmark or automatic promotion. The exact stored Ollama incumbent
remains available for rollback; neither its descriptor nor qualification history
is replaced. GLM selection suspends discovery, downloads and paired benchmarks.

After installing and verifying the reviewed provider configuration:

```
midnight models --default glm
midnight models --json
midnight models --restore-incumbent
```

`--default glm` records an explicit `owner_default` mode with `expires_at:null`.
No other selection mode accepts a missing expiry. Each change archives the prior
overlay under `runtime/models/colibri/selections/`. The legacy `--end-trial`
command also restores the retained incumbent for new tasks. Neither rollback nor
selecting GLM renews a queued or running action's pinned descriptor, original
absolute deadline, invocation charge or receipt.

The saved schedule is 00:00–09:00 Adelaide, Monday–Friday. An explicit
`midnight mode 24x7` remains available as a separate owner control. Model
selection leaves the schedule and manual pauses unchanged. Individual actions and phases retain their existing finite profile
and owner bounds; persistent selection adds no shorter deadline. An independent
native session takes one finite allowance from the verified current profile at
session creation. Its deadline does not slide forward as turns complete.

## Optional finite real-work trial

An owner-requested Colibri trial can temporarily select the already acquired
GLM-5.3 checkpoint for real registered project work, without running the paired
benchmark or replacing the stored Ollama incumbent. This is an experimental
selection with unknown model quality, not an automatic qualification/promotion.
It expires within 24 hours. New task selection then returns to the incumbent;
in-flight tasks retain their pinned descriptor and absolute job deadline.

After the operator installs a reviewed local provider configuration:

```
midnight models --trial glm --provider-config /absolute/path/to/config.json --until "2026-11-07 19:32:35+10:30"
midnight models --json
midnight models --end-trial
```

The trial flag does not change the saved schedule, continuous-mode override,
manual pauses, project membership, cloud budgets, or project acceptance rules.
Pause/resume retain their normal global semantics. Ending the trial changes new
selection; use `midnight pause` to cancel current work immediately. The local
model maintenance step skips discovery/acquisition/benchmarks during the trial.
Changing the pinned configuration additionally requires draining all existing
Colibri actions. Native startup retains and rechecks the verified launch profile
across Ollama unloading; monitoring uses that same snapshot.

`colibri_provider.py` attests the checkpoint manifest/revision, acquired-file
size/mtime/inode records, native binary, gateway/template, launch script, provider
and worker source hashes and exact runtime profile. Weight bytes remain the
actual checkpoint size; they are not reported as resident RAM. The provider uses
the existing shared local GPU lock, drains cached Ollama models before native
startup, and stops its birth-matched native children before releasing the lock.
The reviewed version-two profile observes charged process footprint including
compressed-page charges, with a 26 GB limit and cancellation after sustained
critical system pressure. Native `--ram 24` and `RSS_GUARD_GB=24` guards remain
enabled; startup overcommit is disabled. Version one retains its historical
22 GB/overcommit configuration and is not silently converted. Neither profile
is a certification of production fit or model quality.

Version two retains full weights/routing, a 32K context, CPU prefill with
`COLI_PREFILL_CHUNK=1024`, Metal expert compute and a single expert-cache slot
per layer. It changes no checkpoint weights or native engine code. Typed decisions
use low reasoning and at most 1,024 output tokens; edits/reviews use medium and
at most 4,096. The host validates exact JSON/schema/EOS/usage before acting.
Truncation, native tool calls and missing usage are incomplete, never a valid
typed decision. No model-selected shell commands are introduced.

Before a version-two GLM launch, the quiet-host gate requires 120 continuous
seconds of normal pressure, at least 24,000,000,000 free-plus-inactive bytes,
and at most 64 MiB of swap-out over that interval. It samples every five seconds;
unavailable observations, gaps or renewed pressure restart the quiet window.
This is a resource wait, not generated tokens or a coding-quality failure.
The existing local GPU lock and a shared heavy-memory lease also exclude the
coordinated backup runner; ownership survives uncertainty through durable receipts.

Host pressure or native startup-memory refusal permits one reserved recovery
after proven drainage, a fresh quiet window and backoff of 30 minutes, doubling
to at most four hours after further failures. A charged-footprint limit,
unavailable telemetry, other startup failure or unconfirmed cleanup parks the
same exact request/profile. Its later observation checks do not authorise another
model launch. A changed, verified workload/profile is required; restart, elapsed
time and renamed actions do not erase the original hold or charges. Backup
coordination changes only scheduling and owned-process cleanup, preserving the
existing repository, credentials, exclusions, retention and restore checks.

The real-work profile permits implementation and local supervision to use the
remaining owner action window, up to 24 hours per configured phase. The initial
six-hour/two-hour defaults were agent implementation choices and are unsuitable
as early stop clocks for large CPU prefill. New GLM project actions and every
phase remain bounded by the current window and owning action's absolute deadline;
finite trials also retain their original expiry. Persistent default selection
does not renew in-flight deadlines. Ordinary Qwen actions retain their ninety-minute ceiling. Only
a manifest bound to the exact provider profile can exceed the existing 1,800s
manifest ceiling; ordinary Qwen manifests keep their existing rules. Fixed
syntax checks retain their own short execution bound. Complete semantic host
checks, independent review, audit, delivery, original attempt charges and frozen
scope remain mandatory. Source installation and real accepted product work are
separate observations. All private provider state and native session evidence
remain under `runtime/models/colibri/`, excluded from Git.

Only an exactly pinned GLM implementation worker receives a 30-minute
no-progress allowance for slow prefill; other workers keep the existing
four-minute stall guard. Actual native prefill and content progress is relayed,
not fabricated from keepalives. Trial termination prevents new selection;
already dispatched owned actions retain their pinned model and original cutoff.

GLM agent-chat and scrum calls use that same pinned supervision profile, clipped
to the original job and window deadlines, plus expiry for a finite trial. Their local-resource admission
can wait within the saved job deadline; waiting does not renew the action or
spend a model-call charge. This applies only to the exact model of a live owned
action. Qwen keeps its existing auxiliary and admission limits.

Status separates the next selected model from each running action's pinned
model and from observed native processes. Native Colibri observations require
birth-matched processes and report bounded receipt evidence; missing or stale
observations stay explicit. Empty streaming keepalives are not generated output.
The Ollama allocation list alone cannot establish whether Colibri is running.
The guardian service uses standard macOS scheduling; controller and dashboard
definitions require a separately reviewed, paused installation migration.

The pinned full-GLM engine already reuses exact common token prefixes and persists
its conversation KV cache in the model directory's `.coli_kv` file across engine
restarts. A worker keeps one native session across its turns. This is not a
separate retained cache for every project or phase, and cache existence does not
prove useful reuse for a particular request. Native prefix/prefill receipts report
the actual reuse. No additional prompt/cache optimization or automatic personal
GPU-priority handoff is implied by persistent selection; initial uncached prefill
can still be slow.

Weekday closure stops and drains ordinary work at 09:00 even during prefill.
The separately authorised one-action commissioning grant has its own fixed
deadline, no more than nine hours, and grants no general off-hours dispatch.
The existing seven-day real-work campaign is amended in place: original daily
windows, old profile/source attribution, reports and seven paid-attempt slots
remain intact. Quiet-host waiting and reduced memory pressure do not by themselves
establish a throughput improvement, a completed project change or model quality.

The [agent workspace](AGENT_WORKSPACE.md) uses actual session/model provenance.
Worker, planner and reviewer names are logical local roles, not evidence of
different model instances. Routine replies and daily scrums share the existing
single GPU lane and create no automatic cloud attendance. Read-only conversation
inspection never runs a model. Model changes remain a separate qualified
task-boundary decision.

Midnight checks its trusted model catalogue and publisher release feeds daily.
The shared controller runs one resumable maintenance step between project tasks,
using the same exclusive GPU lock as local-code. Status and the dashboard only
read saved evidence; neither can start inference or downloads.

`models.json` is the Codex-maintained compatibility catalogue. Releases from the
official Qwen, Mistral and OpenAI Hugging Face feeds appear in the dashboard.
Unknown architectures/releases require Codex compatibility assessment and a
catalogue entry; public popularity or a model's own instructions grant no trust.
Approved catalogue entries require their publisher's recorded licence and local
Ollama weights. Discovery records the registry manifest digest and licence-card
hash. Models whose weights exceed 60% of physical RAM are held. Downloads use
Ollama's content-addressed resumption, with disk headroom; no remote Python,
package installation, paid inference or external target testing is involved.

An acquired candidate receives a Midnight-specific alias with 32K context and
explicit sampling settings. Configuration, parameters and exact manifest digest
are attested again at every dispatch. Interactive local-code defaults stay as
configured. Benchmarking uses a bounded local Ollama tool loop with read_file, write_file and run_tests, six exercises
(three behaviours repeated twice) for each of incumbent and challenger, with
identical prompts, alternating order, separate disposable Git repositories and
hidden assertions executed by the manager. The worker can edit only two named files. Generated Python runs without network, fork or signal privileges, with a CPU limit, monitored RSS, bounded output and a process lease. Only data crosses its JSON interface; authoritative assertions never execute in its interpreter. Production uses Ralphy and Midnight's typed file/test worker; this small-tool benchmark remains a screening result rather than qualification of the exact production worker. Every candidate must pass all six, remain within
memory checks, and either pass more than the incumbent or finish at least 15%
faster on a perfect tie. This establishes a bounded local result, not a universal
ranking or proof about large project work. Production acceptance/review remains
unchanged. A new benchmark suite invalidates unfinished comparisons.

Each maintenance cycle runs one probe, then lets project work proceed. Probe
deadlines and memory guards detect stuck work; these are safety/recovery bounds,
not token usage quotas. Runtime model allocation above 80% of RAM, swap growth
above 4 GiB in one probe, or sustained critical pressure stops a probe. macOS
may retain old swap after work finishes; total swap alone is not a verdict.
Interrupted comparisons keep completed probes and retry the interrupted probe
in a fresh repository. A previously rejected digest/suite is not rebenchmarked
daily. New upstream digests receive new qualification. Existing downloaded model
weights and incumbent aliases are retained for rollback rather than deleted.

Qualified winners are selected at a task boundary for every admitted backend.
The former model remains available. In the first 20 completed runs of a promotion generation, three consecutive unfinished tool-call
diagnostics during the promoted model's canary cause automatic rollback. Task
correctness failures still go through ordinary host checks and review; they do
not establish a model regression by themselves. Codex can inspect receipts and
roll back an unsuitable promotion during idle maintenance.

Observe with `midnight models --json` or the dashboard's Local model
qualification panel. Private durable state, evidence and history live under
`runtime/models/`; model blobs stay in Ollama's existing local model store.
Daily discovery requires network access. Discovery/acquisition failures retain
the incumbent and are visible; there is no claim that every possible model on
the internet has been tested. Codex remains responsible for assessing untracked
new releases when checking Midnight.

Implementation uses Python's standard library and the already installed
Ollama/local-code/GPU-lock stack. Memory sampling and scoped detached-process
cleanup adapt the owner's existing local-agent evaluation harness. Original
controller/state-machine code is small and specific to Midnight's durability and
backend contract; introducing another agent framework would duplicate them.

Primary sources: https://docs.ollama.com/api/pull,
https://docs.ollama.com/api/create,
https://ollama.com/library/devstral-small-2,
https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512,
https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct.

The qualifier enforces a small pure-solution syntax subset: only copy/math
imports and approved ordinary attributes, with restricted builtins and module
facades. This keeps generated solution code away from the trusted RPC bridge.
Generated unit tests remain untrusted; the manager's own assertions determine
correctness. Runtime bounds apply to every test call and final validation.
Downloads have a ten-minute resumable acquisition window with an independent
liveness/deadline signal, including stalled or partial-line reads.

The controller supplies a live, boot-bound maintenance token and deadline.
Direct invocation of the maintenance script is rejected without that lease.
Project dispatch holds the shared selection mutex for the entire cycle; the
worker verifies the exact pinned descriptor from its environment. Canary
observations are bound to a promotion generation and require actual response
evidence; pre-execution infrastructure failures cannot count as successes.

Alias names alone do not define a different configuration. The manager skips a challenger whose immutable manifest digest and context match the incumbent, while keeping each alias's exact parameter attestation for dispatch. The read-only dashboard shows current and archived comparison receipts, paired pass counts, timings, memory observations and model checkpoint health. These inspection routes never discover, download or run a model.
