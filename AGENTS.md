# Midnight development

Follow the parent workspace instructions and VENIN account boundaries. Track Midnight work in GitHub and the Site / Project Map, never the personal operating picture. Midnight owns the overnight schedule and membership register. Product implementations remain in their own repositories.

The owner defines broad goals through Codex; Codex maintains `projects.json`, project briefs and backlogs; Midnight dispatches local implementation and independent review. Do not count attempts, generated patches or pending reviews as accepted achievements. Preserve session history and evidence.

Run `python3 -m unittest discover -s tests -v` for controller changes. Tests must use temporary state and fake backends, never invoke live models or mutate the installed schedule. Inspect live status read-only after installation changes. Keep the single installed LaunchAgent, project membership, shared MAP and VENIN authority record consistent.

Apply [OPEN_SOURCE.md](OPEN_SOURCE.md) across every admitted project. Treat reuse as a standing planning, implementation and review preference. Preserve policy propagation through every dispatch and future backend admission, and retain upstream provenance and required notices.

Use durability.py for critical runtime writes, deletions and recovery. Keep doctor read-only and recover free of model/cloud calls. Preserve boot-bound process identity, schedule/override expiry, accepted commit receipts and replay-safe session history. Follow RECOVERY.md and STABILITY.md for maintenance.

Optimisations belong to Midnight and apply to every registered project. Use worker_quality.py and the version-checked BACKENDS.md contract for repairs, unchanged-candidate checks and diagnostics; keep only product-specific scope/checks inside adapters. Dashboard/reporting must enumerate projects.json without hardcoded product IDs. Preserve all-project conformance tests and original failed-task history when replanning.

Use MODELS.md and the shared model manager for discovery, paired qualification
and task-boundary selection. Never benchmark or download from dashboard/status
paths. Every backend uses MIDNIGHT_LOCAL_CODE_BIN when supplied and retains the
host model descriptor. Keep tasks to one observable behaviour with concrete
examples, preferably one source and one test file; final integration gates
retain the complete contract. Model discovery, downloads and tests retain the
existing account/project boundaries and local GPU lock.

## Session debrief

At the start of every session, read `docs/session-debriefs/README.md` and the latest `docs/session-debriefs/YYYY-MM-DD.md`, including all same-day entries, before substantive work. Verify the current branch, commit and working tree. Before the final handoff of every coding, planning, review or documentation session, add a timestamped entry with completed work, the checks actually run and their results, the current revision, unfinished work, blockers and ordered next steps, and update the index's latest link. Append later sessions and corrections; preserve closed entries. Keep secrets, personal details and private records out of these files: this repository is public.
