# Shared cloud model and rescue policy

Every Midnight Codex management, planning, guidance, review and exceptional implementation invocation starts from **gpt-6-astra with ultra reasoning**. A stronger qualified successor uses its own highest supported reasoning effort, which can have a different label such as `max`. `cloud_models.json` is the versioned baseline. `cloud_policy.py` owns selection and invocation receipts; it never silently substitutes another model or effort. Local workers retain their own local descriptor. Cloud descriptors identify `kind=cloud`, `backend=codex`, provider, requested model, reasoning effort and profile hashes; successor descriptors also bind their catalog-supported effort levels. They contain no fabricated Ollama fields.

## Invocation and delegation

`CloudPolicy(runtime).descriptor()` and `.snapshot()` are read-only. `config_descriptor()` supplies the static baseline; use the instance descriptor when pinning the current qualified selection for a task. `policy()` is a static backend-admission contract.

`validate_descriptor()` verifies the complete descriptor hash, mandatory Astra-ultra baseline and the successor's highest supported effort. The same exact descriptor controls parent and child invocations, requested-settings receipts and provider-mismatch checks. A model that supports `max` but not `ultra` is evaluated at `max`; the baseline is never lowered to accommodate missing ultra access.

`prepare_invocation(cwd, purpose, output_path, schema_path=None, descriptor=None)` returns the command, environment, pinned descriptor, invocation ID and durable requested-settings receipt. The host sends its prompt on stdin, retains its boot-bound process lease and cancellation rules, and calls `record_result(id, stdout, returncode)` afterward. The helper uses the installed Codex CLI adapter with `--ignore-user-config`, `--strict-config`, explicit per-invocation settings, and a fixed project working directory. It does not change global configuration, copy credentials or change persistent project trust. Invocation-only untrusted project overrides suppress project `.codex` configuration layers; existing project instructions still apply.

- Implementation uses the `workspace-write` sandbox with shell/exec tools enabled, command network disabled, approvals set to `never`, no extra writable roots and no writable temporary-directory expansion.
- Management, planning, guidance, review and qualification use read-only sandboxes and disable shell/exec tools; the host supplies bounded evidence and an output schema.
- Host-scheduled analysis uses a read-only sandbox with local read tools. `prepare_analysis_child(parent_id, cwd, output_path, schema_path=None)` preserves the exact parent descriptor, reserves at most two children durably and forbids grandchildren and child implementations.
- Native subagent tools are disabled. This makes the one-writer, read-only-child and depth-one limits enforceable rather than relying on instructions inside a shared writable sandbox. Apps, plugins, web search, browser/computer use, hooks and permission-request tools are disabled for worker invocations.

A requested profile is not proof of the provider's internal routing. Receipts keep requested settings separate from client session metadata and provider metadata. Provider settings are recorded only when a provider response exposes them; otherwise `provider_confirmed` remains null and the status is `not_exposed`. A provider-exposed model or effort mismatch rejects the result. Nonzero exits and mismatches leave a visible failure reason and never trigger fallback.

Text-only hosts call `validate_supervisor_events(raw)` before using a result. It rejects tool activity except the installed CLI's built-in `codex` inventories `list_mcp_resources` and `list_mcp_resource_templates`, each with empty arguments and a matching successful start/completion pair whose sole result is an empty inventory. Resource reads, connected-app calls, nonempty inventories, additional result fields, incomplete calls and other action tools are rejected. These built-in inventories can remain available with integrations disabled; the documented per-server tool filters do not control them. The returned `empty_inventory_calls` count records the compatibility exception without treating it as account access. This validator supplements the isolated invocation settings; it does not grant tools permission to execute.

`run(...)` is the optional synchronous subprocess adapter for management/qualification. Production implementation backends should use `prepare_invocation` with their existing process lease, recovery and acceptance pipeline. A cloud implementation still needs all existing project scope checks, host acceptance, independent review, canonical-head checks and an accepted commit receipt.

## Discovery, qualification and promotion

The controller explicitly calls `maintenance(runtime, cutoff=None, task_boundary=True)` at a task boundary. Cutoff accepts epoch seconds or a datetime. Status and dashboard reads never discover models, contact providers or qualify candidates.

Discovery is due once every 24 hours. It fetches the configured official OpenAI release/model pages and the installed CLI's current model catalog (`codex debug models`). A candidate must appear in an official source and expose supported reasoning levels in the available catalog. It is qualified at the highest supported known level: `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, then `ultra`. These labels select effort within a model; they do not rank model quality across different models. Unknown labels are held for compatibility assessment instead of guessing their order. A model name, numeric version or catalog ordering alone is not evidence that it is stronger. Official source URLs, content digests, comparative excerpts and catalog availability are retained. A discovery/catalog error or unavailable incumbent keeps the incumbent selected with an explicit reason; its subsequent invocation can fail visibly rather than silently using something else.

The fixed `management-v1` qualification suite covers priority, failure diagnosis, bounded decomposition, corrective guidance, deduplication into a stable episode, evidence-grounded review, scope/account boundaries and rescue-budget decisions. Every case is mandatory. The complete descriptor/configuration and exact suite fixtures key the cache. Promotion requires every challenger case to pass without regression against the incumbent. Equal results require verified official text identifying the challenger as stronger than the incumbent; reverse comparisons are not accepted. Quality takes precedence over cost and speed.

A qualifying candidate becomes pending. `promote(task_boundary=True)` saves the previous descriptor and starts a ten-decision canary; an invocation already pinned to its descriptor stays pinned. The host calls `record_decision(decision_id, quality_ok=..., evidence=..., descriptor=pinned)` for observed management/review decisions. Duplicate receipts and older-profile decisions do not advance the canary. A quality regression restores the previous profile, records the failed profile and prevents daily reproposal of that same configuration. A new suite/configuration can subsequently justify a new qualification.

Qualification is a small mandatory decision suite, not a comprehensive claim that a model is universally better. Official comparison evidence and the canary supply separate checks. Synthetic tests exercise the control paths without making live model calls.

## Exceptional cloud implementation rescue

`cloud_rescue.RescueStore(runtime / 'history.sqlite3')` owns its `cr_` tables in the shared indexed history database. SQLite immediate transactions serialize reservations. `cr_` and cloud invocation `cp_` tables coexist with quality/history `qc_` tables; read-only status does not create a database. Writes use synchronous/extra durability and directory synchronization.

The same unresolved episode must have:

1. Two distinct, substantive, host-observed local failures.
2. A saved, concrete Codex guidance receipt.
3. Two further substantive local failures whose invocations received that exact guidance receipt.
4. Host verification of any saved but previously unverified draft.

Infrastructure, authentication, unavailable-model, process-loss and other unrecognized failures do not count. The accepted substantive kinds are `acceptance`, `review`, `substantive`, `worker_no_change` and `implementation`; host verification and evidence are still required. `record_local_failure(..., guidance_id=...)` checks delivery of the guidance rather than merely counting attempts later in time. Duplicate event IDs cannot count twice; conflicting replays are rejected.

`bind_episode(project_id, original_behaviour_fingerprint, episode_id=None)` preserves the original episode across task renames. Adapters also retain the original task/retry/finding lineage. A rename or an exhausted local task is never a new rescue allowance.

`eligibility(project_id, episode_id)` returns an explicit status. `reserve(project_id, episode_id, task_id)` rechecks the full gate under an immediate transaction and commits the reservation **before launch**. The limit is three cloud implementation reservations per project in the preceding 168 hours and one automatic attempt per unresolved episode. The window includes its exact lower boundary. A reservation with an unknown launch or a crash still counts; it is never refunded automatically. Weekly quota expiry does not reset an episode's one-attempt limit.

`record_saved_draft(...)` blocks rescue pending `record_host_verification(...)` for that exact draft. Passing host checks alone does not close an episode because independent review can still reject it. After final accepted local work, call `resolve_episode(project_id, episode_id, evidence)`. After a reserved cloud attempt reaches final acceptance or rejection, call `finish(token, success, evidence)`. A failed rescue returns `manager_required`; it does not launch another automatic cloud implementation. An accepted result must include the backend's full acceptance and review evidence.

## Integration and verification

The modules reuse Python's standard-library SQLite, subprocess, JSON, hashing and HTTPS support and the existing `durability.py` helper. The optional Codex CLI is an adapter, not vendored upstream code. No new package dependency is needed.

The implementation was checked against installed `codex-cli 0.166.0` help and the local model catalog exposing `gpt-6-astra` with `ultra`. Official references explain [reasoning and subagent inheritance](https://learn.chatgpt.com/docs/agent-configuration/subagents), [configuration precedence and project trust](https://learn.chatgpt.com/docs/config-file/config-basic), [configuration keys](https://learn.chatgpt.com/docs/config-file/config-reference), and [sandbox and approval behavior](https://learn.chatgpt.com/docs/agent-approvals-security). These references do not establish account access or confirm a provider's actual inference settings.

Run `python3 -m unittest discover -s tests -p 'test_cloud*.py' -v`. Tests use temporary databases, synthetic catalog/release evidence and fake runners. They cover strict profile arguments, separate tool modes, child bounds, requested-versus-provider receipts, mandatory qualification, cache identity, task-boundary promotion, ten-decision rollback, stronger successors with a different maximum-effort label, refusal of unknown or downgraded efforts, explicit unavailability, stable episode guidance, draft verification, unknown launches, exact rolling limits, concurrent reservation races and replay conflicts. No live model qualification, implementation or production mutation is performed by the suite.
