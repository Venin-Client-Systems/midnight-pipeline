# Bounded edit generation

M18 / #66 qualifies the edit phase separately from action selection. The pinned
Qwen model, model parameters, 32K context and original action/edit/chat output
caps of 1024/4096/1536 remain unchanged. Managed workers retain capability-gated
non-thinking requests. Native-provider dispatch keeps its existing provider path.

The candidate policy `bounded-structured-nonthinking-v2` changes two details:

1. Edit generation replaces the action-only system instruction with an explicit
   code replacement instruction. The complete host task, shared permissions,
   allowed files, exact current file and original checks remain present. The
   original messages are not mutated, so subsequent decisions retain their own
   instruction.
2. The provider receives the same edit-object schema except for the two string
   `maxLength` facets. Required fields, string types and additional-property
   rejection remain in that schema. Midnight validates every completed response
   against the unchanged original schema, including old_text <=4096 characters
   and new_text <=8192 characters, before any application. Truncated, malformed,
   oversized, unknown-field, noop, nonmatching and syntax-invalid results retain
   their existing rejection behavior. No output is repaired or partially used.

This distinction is limited to managed edit inference. Action/chat schemas and
unmanaged helper calls retain their original decoding constraints. The policy
version and exact source bindings distinguish the changed behavior from prior
failed attempts; they grant no coding admission by themselves.

## Evidence and limits

The preserved M10 native label case produced 25 completed noop edits and then
expired without handoff. Subsequent non-applying comparisons used its exact
first edit context, pinned model and original 4096-token cap:

| Comparison | Result |
| --- | --- |
| Explicit edit instruction, non-thinking, original schema | Noop; 22 tokens |
| Same request with thinking enabled | Intended logic, missing newlines; 202 tokens; exact match failed |
| Same thinking request with JSON mode | Newlines retained, extra path field; 187 tokens; host schema rejected |
| Original object schema without provider maxLength, thinking | Changed, exact-match, syntax-valid proposal; 182 tokens, 8.148 seconds |
| Same modified object schema, non-thinking | Same valid proposal; 57 tokens, 2.481 seconds |

The response stream already lacked newlines in the failing schema sample;
Midnight's JSON parser did not remove them. The observed local service was
Ollama 0.40.13 with its MLX runner. These samples support this bounded candidate
policy, but do not establish a unique upstream implementation defect, latency
expectation or general model quality. The prompt change alone was insufficient.

The official [Ollama structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs)
describes both JSON and schema modes. Production retains schema mode and strict
host validation. The failed JSON-mode experiment was not selected.

Private raw requests, streams, model/source hashes and independent procedure
reviews remain under `runtime/productive-operation-20261113/`, including
`m10-native-v1/` and `m18-phase-frame-v2/` through `m18-phase-frame-v6/`.
The earlier v1 diagnostic stopped at a host-model preflight before generation;
its original script and log remain retained. No diagnostic output was applied.

## Verification and rollout

```sh
python3 -m unittest discover -s tests -p test_worker_edit_phase.py -v
python3 -m unittest discover -s tests -p test_worker_inference_policy.py -v
python3 -m unittest discover -s tests -p test_recovery_edit_context.py -v
python3 -m unittest discover -s tests -v
```

The new regressions reproduce the conflicting edit instruction and provider
schema boundary, and independently verify that full host length/field checks
remain enforced. The existing fake-provider fixture identifies edit requests
by their required fields, because decoder facets now intentionally differ from
the host schema. Its completion, truncation, token-budget and thinking-policy
assertions remain in force.

Exact-source M10 native qualification with unchanged protected oracles remains
required. Passing diagnostics or a unit suite does not satisfy that gate.
Reviewed idle adoption belongs to M14, real accepted repair/feature work to M15,
and the full unattended scheduled night to M16. Old receipts stay readable;
rollback restores reviewed source without erasing evidence or spent charges.
