# Productive progress observations

Issue M13 #59 adds an
observational `productivity` field to each project in status, dashboard and
oversight responses. The backend's status and morning report use the same
projection. Guardian observations retain it under
`productive_progress[project_id]`, separately from scheduler health and incident
counts. None of these fields authorizes a launch or proves commissioning.

`productive_status.py` reuses the existing `HistoryStore`, retained host
acceptance receipts, indexed recovery results and the recovery snapshot. It is
small application-specific projection code using Python's standard library;
another monitoring framework would duplicate the existing authoritative stores.
No external code or dependency is introduced.

## What the fields establish

- `last_accepted` and `seconds_since_accepted` use the latest complete retained
  host receipt from the hot acceptance list and the terminal acceptance index.
  Indexed run/task/time keys must match the payload, and every bounded hot/index
  overlap is compared even when a different indexed run is newest. Numeric epoch
  seconds and offset-aware string timestamps project to UTC ISO strings so all
  consumers use the same units without changing the retained receipt.
  A GitHub-delivered receipt also needs `remote_merge_confirmed: true`. Task
  completion markers, attempts, private checkpoints and running processes do
  not establish acceptance. This is an observation of retained host evidence,
  not a fresh remote verification of the commit.
- `failure_groups` groups retained failures by their saved fingerprint. Version
  2 harness identities must have their required fields and matching digest.
  Older identities are explicitly labelled legacy; observation does not upgrade
  them into admission evidence. Counts cover distinct indexed run records in a
  bounded sample, not lifetime totals or necessarily unresolved current failures.
- `unfinished_handoffs` retains incomplete inference and worker-contract failures,
  including failed publication with an earlier saved finish flag. Current
  disposition comes from the existing admission/control projection.
- `admission` copies the existing recovery decision and reason for the latest
  observed scope of each task. It is marked `observational_only`. New launches
  still require fresh reviewed admission through the existing host path.
- `responsibility` and `control_reason` explain current controls separately from
  failure responsibility. An owner pause can coexist with a harness correction
  that belongs to Codex. Unknown evidence remains unknown. Guardian and backend
  report projections use existing control explanations without claiming process
  liveness; the full project snapshot also has its verified process observation.
- `activity_observed` is separate from acceptance, and
  `activity_is_acceptance` is always false. A responsive guardian, active model or
  recent source installation is not productive acceptance.

## Bounds and unavailable evidence

The added reader inspects at most 500 hot acceptances, one latest indexed
acceptance plus their bounded indexed overlaps, and 100 latest indexed results
for this project, with one extra row
to identify truncation. SQL bounds each fetched payload to 128 KiB plus a
sentinel byte. Its read connection has a 50 ms lock timeout and a SQLite progress
handler with a 100 ms query budget. Existing history readers and all writers
retain their original timeout and durability configuration. Existing upstream
project/control collection has its own bounds and failure handling.

`coverage` states the sample limits, truncation and whether an admission snapshot
was supplied. A partial recovery schema, orphan result or explicitly unavailable
admission snapshot cannot become verified empty history. Inconsistent, malformed,
oversized, locked or unavailable evidence sets `available: false` and records
the reason. Conflicting acceptance receipts and future or timezone-free receipt
times cannot produce a precise acceptance age. There is no zero-age fallback.
An empty verified observation has no acceptance receipt and no elapsed value.

The read path does not create a database, scan run directories, update schema,
change application rows, call a model or repair evidence. SQLite may maintain
its own WAL/shared-memory files as for the existing reader; do not use immutable
mode to hide live changes. Missing new fields remain compatible with older
dashboard records. Status freshness and HTTP unavailable handling remain the
existing dashboard cache's responsibility.

## Verification and adoption

Run:

```sh
python3 -m unittest discover -s tests -p test_productive_status.py -v
python3 -m unittest discover -s tests -p test_operation_status.py -v
python3 -m unittest discover -s tests -p test_oversight.py -v
python3 -m unittest discover -s tests -p test_dashboard.py -v
python3 -m unittest discover -s tests -p test_guardian.py -v
python3 -m unittest discover -s tests -p test_history_store.py -v
node --check dashboard/app.js
python3 -m unittest discover -s tests -v
```

The new integration cases exercise backend reports, guardian audit metadata and
project snapshot through dashboard/oversight using disposable state, with
process/model requests forbidden. Visual verification uses the actual dashboard
assets served against saved synthetic running-but-unproductive, held, stale and
accepted-progress documents; POST routes are disabled. The screenshots and full
logs stay in ignored runtime evidence, with exact source hashes.

Adoption remains M14's reviewed idle-boundary installation. Rollback restores
reviewed source without reverting runtime history. This additive observation
requires no history migration, schedule change, admission change or new worker
run. Real repair/feature acceptance and the complete unattended scheduled night
remain separate M15 and M16 gates.

Independent review of the first M13 source found malformed-input, overlapping
acceptance provenance, incomplete-history and timestamp-unit gaps. Linked
M28 #90 records those
findings before their corrections. Eight additional regression methods reproduce
all four findings and preserve every original behavioural assertion. The earlier
passing suite and revision review remain evidence of the first source, not the
corrected source's acceptance.
