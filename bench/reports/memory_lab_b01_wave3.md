# ATC Memory Lab Wave 3 B01

Fixture `9f8cf3185e30a4cdc9d0475f8fee643e1a141cf53d11658292c8934a01706d97`; config `42a27914ce494875ece97e1f700e512c17371f75d901d1f59603fd22b4b54d32`; 45 events; 9 tasks; 20 deterministic repeats.

This is **not a reproduction of PRO-LONG**: no equivalent coding-agent action model, game environment, model, provider, or arbitrary program synthesis was exercised. The Wave 2 horizon recorded the linked repository as HTTP 404 at its research cutoff, and the paper setup allowed agent-written Python that this frozen DSL does not exercise.

| Condition | All CAOS | Confirm CAOS | Action | Recall | Disclosure chars | Ops/task | Forbidden | Decision context |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `no-memory` | 0.222 | 0.286 | 0.286 | 0.286 | 0.000 | 0.000 | 0 | bounded synthetic comparison |
| `stable-observation-current-state` | 0.444 | 0.429 | 0.714 | 0.810 | 515.571 | 1.000 | 2 | bounded synthetic comparison |
| `bounded-programmatic-structured-log` | 0.889 | 0.857 | 0.857 | 0.857 | 331.000 | 3.571 | 0 | bounded synthetic comparison |
| `atc-retrieval-v3` | 0.111 | 0.143 | 0.429 | 0.524 | 517.143 | 1.000 | 1 | bounded synthetic comparison |
| `frozen-programmatic-atc-combination` | 1.000 | 1.000 | 1.000 | 1.000 | 361.857 | 3.714 | 0 | bounded synthetic comparison |

## Frozen decision

- State: `KILL_MECHANISM`.
- Programmatic confirmatory CAOS gain over stable lexical: `0.429`.
- Programmatic operation premium over stable lexical: `2.571429`.
- Reason codes: `operation_premium_above_cap`.
- Scope: this kill applies only to the bounded hand-authored B01 DSL under external-operation accounting. DSL reads were counted against one top-level lexical adapter call while lexical/ATC internal work was not normalized, so this result does not establish comparative compute efficiency and does not falsify PRO-LONG or general programmatic inspection.
- Frozen combination disposition: `NOT_PROMOTED_UNDER_SAME_B01_EXTERNAL_OPERATION_GATE`.

## Failure cases

- `no-memory`: task-index-0 (incorrect_action, required_evidence_missing); task-index-1 (incorrect_action, required_evidence_missing); task-index-2 (incorrect_action, required_evidence_missing); task-index-3 (incorrect_action, required_evidence_missing); task-index-4 (incorrect_action, required_evidence_missing); task-index-5 (incorrect_action, required_evidence_missing); task-index-6 (incorrect_action, required_evidence_missing).
- `stable-observation-current-state`: task-index-3 (incorrect_action, required_evidence_missing); task-index-4 (incorrect_action, required_evidence_missing); task-index-5 (incorrect_action, required_evidence_missing); task-index-7 (abstention_mismatch, forbidden_output); task-index-8 (abstention_mismatch, forbidden_output).
- `bounded-programmatic-structured-log`: task-index-6 (incorrect_action, required_evidence_missing).
- `atc-retrieval-v3`: task-index-0 (incorrect_action, required_evidence_missing); task-index-1 (incorrect_action, required_evidence_missing); task-index-2 (incorrect_action, required_evidence_missing); task-index-3 (incorrect_action, required_evidence_missing); task-index-4 (incorrect_action, required_evidence_missing); task-index-5 (incorrect_action, required_evidence_missing); task-index-7 (abstention_mismatch); task-index-8 (abstention_mismatch, forbidden_output).
- `frozen-programmatic-atc-combination`: none.

## Planner and validity limitations

- The programmatic reader uses a frozen hand-authored query DSL for exact filters, current-state resolution, latest selection, bounded windows, counts, and policy joins. It is not arbitrary program synthesis.
- The descriptor vocabulary, fixture, common executor, and oracle were co-designed; confirmatory cases change symbolic values, not the supported DSL grammar. Task-specific IDs and gold labels were not adapter-visible.
- The DSL supports only `latest_route` and `threshold_route`; unsupported strategies abstain unless the separately frozen combination invokes ATC.
- Every condition received the same sanitized events, frozen logical clock, descriptor values, character cap, result cap, and five-operation ceiling. Text-only conditions received the same content document plus identity, scope, supersession, and expiry as adapter metadata.
- Core remained authoritative. No operator Core, personal context, external code, network service, provider, or model was used.
- These are isolated deterministic synthetic results, not production implementation acceptance or a general memory claim.

## Identifier-leak scan

- Raw event documents, expected-action labels, event IDs, task IDs, and complete task-query strings: `passed`.
