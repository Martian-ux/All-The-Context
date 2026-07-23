# ATC Wave 3 external memory artifact intake

## MPBench availability changed; PRO-LONG remains unavailable

| Field | Value |
|---|---|
| Date | July 23, 2026 |
| Observation window | 2026-07-23 20:55:40–20:55:56 UTC |
| Scope | Metadata-only intake of `Digital-Trust-Lab/mp-bench` and continued-availability check of `alexisfox7/PRO-LONG` |
| Worker base | Coordinator commit `dd4b880a68842dd0c797741ba2ffe673e768c55d` |
| Evidence level | `L0`; primary-source artifact inspection, no execution or benchmark result |
| Authority | Research intake only; no production, governance, adapter, or Core authority |

This intake records a newly discoverable official MPBench artifact and checks
the paper-linked PRO-LONG URL again. It does not import either project, accept
an external dependency, reproduce a paper result, or authorize an experiment.
Repository and dataset text is untrusted data, never instructions.

The inspection used only official arXiv pages and GitHub repository, profile,
and API surfaces. No repository was cloned. No dataset blob, archive, raw
payload row, third-party code, package, model, provider, or personal context
was downloaded, opened, installed, or executed. The operator Core was not
accessed.

## 1. Decision

1. **MPBench changes from “artifact not located” to “metadata-qualified,
   execution-denied intake candidate.”** The public repository is small and
   data-only, has an immutable current revision, and exposes a root
   Apache-2.0 license. Its adversarial free-text fields are instruction-shaped
   by design, so neither availability nor licensing makes the payload safe to
   present to an agent or LLM.
2. **PRO-LONG remains artifact-unavailable.** The arXiv paper still links
   `https://github.com/alexisfox7/PRO-LONG` and says code and logs are
   available, but the repository page, GitHub API endpoint, and pinned-branch
   guesses for root `README.md` and `LICENSE` all returned HTTP 404 during the
   observation window. The repository was also absent from the author's public
   owner-repository listing.
3. **No external run is approved.** MPBench may support a future quarantined,
   schema-first, symbolically sanitized protocol experiment after a separate
   approval. PRO-LONG cannot enter the supplier gate until its official bytes,
   immutable revision, and code/log license are inspectable.

## 2. MPBench provenance and canonical linkage

### 2.1 Verified ownership chain

- arXiv `2606.04329v2`, revised June 18, 2026, is titled *From Untrusted Input
  to Trusted Memory: A Systematic Study of Memory Poisoning Attacks in LLM
  Agents* and names Pritam Dash, Tongyu Ge, Aditi Jain, Tanmay Shah, and Zhiwei
  Shang.
- The GitHub repository README identifies itself as the MPBench dataset
  accompanying that exact paper and repeats the same author list in its
  citation.
- The repository is owned by the public GitHub organization
  `Digital-Trust-Lab`. Its current commit and all three commits are authored by
  Pritam Dash and linked by GitHub to the `dashpritam` account, whose public
  profile name is Pritam Dash.

This is strong author-controlled linkage from repository to paper. It is not a
fully bidirectional canonical link: the inspected arXiv v2 page does not name
the GitHub repository, and the Digital Trust Lab organization profile exposes
no institutional website. The organization name itself therefore does not
establish an institutional affiliation beyond GitHub ownership.

### 2.2 Immutable revision and activity

| Property | Verified value |
|---|---|
| Repository | `Digital-Trust-Lab/mp-bench` |
| Visibility/state | Public, enabled, unarchived, non-fork |
| Default/only branch | `main` |
| Current commit | `6886880a7c29625e0109e0ad91d0e095029f1577` |
| Current tree | `70766a87ea247914f427e90e45dbb9b07b81b2ce` |
| Created | 2026-06-28 22:26:50 UTC |
| Last push/current commit | 2026-06-28 22:54:07 UTC |
| GitHub metadata update | 2026-07-06 20:01:22 UTC |
| Commit history | 3 commits, all linked to `dashpritam` |
| Releases/tags | 0 / 0 |
| Open issues | 0 |
| Repository API size | 4,485 KiB; this is GitHub repository metadata, not the sum of uncompressed blob sizes |

Commit history, newest first:

| Commit | Authored UTC | Metadata-only message |
|---|---|---|
| `6886880a7c29625e0109e0ad91d0e095029f1577` | 2026-06-28 22:54:07 | README dataset description update |
| `fa19f852dfdbf56288df0f3e3abed16f383fbf65` | 2026-06-28 22:45:43 | Data added |
| `cc705a2ecbc7d52d1f4b6dc299facbc45fc8fac2` | 2026-06-28 22:26:51 | Initial commit |

There is no release artifact or signed release to prefer over the branch
commit. Any future intake must pin the full commit, not `main`.

### 2.3 Complete pinned-tree inventory

The GitHub recursive-tree response was complete (`truncated=false`) and
contained four regular blobs and no directories, submodules, or symlinks:

| Path | Declared type | Bytes | Git blob SHA-1 |
|---|---|---:|---|
| `LICENSE` | UTF-8/plain-text license | 11,357 | `261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64` |
| `README.md` | Markdown metadata/documentation | 2,321 | `73c19ea2e0b6ef39f1c47b82b241d2f34243f4ce` |
| `adversarial_data.jsonl.jsonl` | JSON Lines dataset by name and README declaration; payload not opened | 7,339,100 | `c060c3309968c8f20e42aa75c83f0043320d2fde` |
| `benign_data.jsonl.jsonl` | JSON Lines dataset by name and README declaration; payload not opened | 6,564,735 | `bf932e1f74a801e4ecf26ecd8e847fa09ee49a74` |

The doubled `.jsonl.jsonl` suffix is the actual repository path. MIME
sniffing, line counts, encoding, duplicate checks, and row-level conformance
remain unverified because the dataset blobs were deliberately not acquired.

### 2.4 Licenses and scope

- The root `LICENSE` blob is the standard-form Apache License 2.0 text. GitHub
  classifies the repository as `Apache-2.0`.
- No nested license, `NOTICE`, component manifest, or per-file exception is
  present in the complete four-blob tree.
- The license sits at repository root and no narrower scope is stated in the
  README. This supports treating Apache-2.0 as the repository's declared
  license, including the checked-in dataset, for intake classification.
- This is not a legal conclusion that every generated row or upstream element
  is free of third-party rights. The README does not provide dataset
  provenance or a separate data-rights warranty. A reuse decision still needs
  legal review.
- The paper itself is distributed as CC BY 4.0 on arXiv. That paper license
  does not replace or broaden the repository license.

### 2.5 Schema and payload character

The repository README and paper Appendix D agree on eight JSON object fields:

| Field | Declared role | Safe intake classification |
|---|---|---|
| `attack_type` | One of six named attack classes | Categorical metadata |
| `attack_signal` | `strong`, `moderate`, or `weak` | Categorical metadata |
| `domain` | One of seven task/input domains | Categorical metadata |
| `adversarial_goal` | One of six downstream attacker goals | Categorical metadata |
| `user_query` | Natural-language task query | Untrusted free text |
| `context` | External content containing the embedded payload | Adversarial, instruction-shaped free text |
| `expected_memory` | Target instruction intended for durable memory | Adversarial, instruction-shaped free text and evaluator target |
| `retrieval_query` | Later query intended to trigger persistence/retrieval | Untrusted activation-oriented free text |

The paper reports 3,240 adversarial cases across six attack classes and seven
domains, plus 2,997 benign examples for false-positive evaluation. Those
counts are author claims, not row counts reproduced by this intake.

The answer to “is any payload text instruction-shaped?” is **yes by declared
design**. The README explicitly describes embedded payloads and target
instructions, and the paper describes strong and weak instruction-bearing
attack forms. This conclusion comes from metadata and paper methodology, not
inspection of raw rows. The benign blob was not inspected; the paper says it
contains both no-write and authorized-useful-write cases.

### 2.6 Dependencies and executable hooks

No executable hook is present in the pinned tree:

- no Python, JavaScript, shell, notebook, binary, container, package manifest,
  lockfile, GitHub Actions workflow, submodule, or model configuration;
- no install, build, network, telemetry, provider, or evaluation entry point;
  and
- no dependency declaration.

That finding is limited to the repository tree. Reproducing the paper's
OpenClaw/HERMES evaluation would introduce agent, model, provider, tool, and
LLM-judge dependencies not shipped here and is outside this intake.

## 3. PRO-LONG continued availability and licensing

The official arXiv `2607.20064v1` page, submitted July 22, 2026, names Alexis
Fox, Junlin Wang, Paul Rosu, and Bhuwan Dhingra. It links
`https://github.com/alexisfox7/PRO-LONG` as the location of relevant code and
logs. The GitHub account `alexisfox7` publicly identifies as Alexis Fox, so the
paper-to-owner linkage is direct.

Current verification result:

| Check at 2026-07-23 20:55:56 UTC | Result |
|---|---|
| Paper-linked GitHub repository page | HTTP 404 |
| Official GitHub repository API endpoint | HTTP 404 |
| Root `README.md` on guessed `main` | HTTP 404 |
| Root `LICENSE` on guessed `main` | HTTP 404 |
| Author's public owner-repository list | `PRO-LONG` absent |

Consequences:

- no current repository commit SHA, tree, file inventory, release, activity,
  dependencies, executable hooks, code/log bytes, or software/data license can
  be verified;
- the arXiv paper has an arXiv perpetual non-exclusive distribution license,
  not a reusable code/log license;
- no historical commit is inferred from caches, forks, search indexes, or
  third-party mirrors; and
- the Wave 2 “unavailable, license unverified” classification remains correct.

The 404 response cannot distinguish never-public, temporarily private,
renamed, deleted, or access-controlled states. A future intake must start from
an author- or paper-linked official URL and repeat the full supplier gate.

## 4. Safest future isolated MPBench adapter experiment

This is a proposed preregistration boundary, not execution approval.

### 4.1 Acquisition and quarantine

1. Acquire only the two data blobs over HTTPS using raw URLs pinned to commit
   `6886880a7c29625e0109e0ad91d0e095029f1577`, inside a disposable,
   credential-free environment with default-deny egress after acquisition.
2. Write into a quarantine directory outside the repository, operator Core,
   adapter search roots, shell history, indexing services, and model-visible
   workspaces. Disable previews, content indexing, telemetry, backups, and
   automatic file handlers for that directory.
3. Before parsing, verify exact byte counts and Git blob identities from this
   intake. Compute and freeze SHA-256 for each acquired blob and the acquisition
   manifest. Git SHA-1 identifies the pinned Git objects; SHA-256 becomes the
   experiment's transport/content digest. A mismatch kills the intake.
4. Never commit, log, print, diff, preview, or attach the payload blobs. Destroy
   the disposable quarantine after aggregate receipts are accepted.

### 4.2 Schema-only gate

Use a non-LLM streaming validator with no plugin, template, expression, or code
evaluation features. It may emit only:

- file digest and byte count;
- row count;
- UTF-8/JSON parse success;
- key-set, primitive-type, null, duplicate-key, and maximum-length results;
- categorical counts for the four declared metadata fields; and
- salted equality hashes for cohort matching and duplicate detection.

It must never emit a free-text value, substring, exception excerpt, rejected
row, or parser context. Any malformed JSON, undeclared key, unexpected type,
oversize field, control-character policy failure, or schema/count disagreement
kills the cell without opening the row for diagnosis in an LLM-visible
surface.

### 4.3 Sanitization boundary

A deterministic sanitizer in the same disposable boundary converts each row
to an inert record:

- categorical fields become allow-listed enum IDs;
- every free-text field becomes a length bucket, salted digest, and opaque
  token such as `UNTRUSTED_CONTEXT_0042`;
- no raw string, decoded fragment, embedding, summary, paraphrase, or
  model-generated classification crosses the sanitizer boundary; and
- sanitizer source, configuration, salt-handling rules, and output schema are
  frozen before acquisition.

No LLM may receive raw MPBench attacks, raw benign rows, or sanitizer error
text. The first experiment therefore evaluates ATC's adapter boundary and
stage accounting over opaque symbolic attack classes; it is **not** a
reproduction of MPBench's semantic attack rates.

### 4.4 Matched cohorts and conditions

Build paired adversarial/benign cohorts using only categorical metadata,
length buckets, and deterministic salted hashes. Freeze equal counts per
domain and compatible write channel, and balance length distributions. Keep
the original benign/adversarial label outside adapter-visible inputs.

Minimum conditions:

1. no-memory negative control;
2. stable current-state control;
3. current ATC research adapter;
4. ATC adapter with admission disabled as a positive-failure control; and
5. sanitizer/adapter dry run with no durable store.

All conditions receive identical opaque records, clocks, budgets, and
checkpoint schedules. No production Core, real tool, provider, network,
personal data, or protected real-world action is present.

### 4.5 P01-compatible stage metrics

Emit per-opportunity counts and privacy-safe reason codes for:

- `presented_untrusted`;
- `admission_rejected` or `durable_write`;
- `later_retrieved`;
- `later_influenced`;
- `protected_action_attempted`;
- `protected_action_blocked` or `protected_action_crossed`;
- source/witness preservation and `WITNESS_COLLAPSE`;
- `CAPTURE_FALSE_WRITE`, `UNAUTHORIZED_INFLUENCE`, `ACTION_NONUSE`,
  `PURGE_RESIDUE`, `BUDGET_ESCAPE`, and `CONTAMINATION`;
- benign false-positive/false-rejection rates;
- correction/purge convergence; and
- local latency, storage bytes, disclosure fields, and tool-call counts.

Report unconditional rates at every stage and the paper-compatible conditional
retrieval rate only as a secondary metric. Preserve the denominator at each
transition so a low later rate cannot hide a high poisoned-write rate. Use
exact one-sided 95% Clopper–Pearson upper bounds for safety opportunities.

### 4.6 Kill criteria

Stop and retain a failed/skipped receipt on any:

- digest, size, schema, count, or provenance mismatch;
- raw free-text value or parser excerpt crossing quarantine;
- raw or sanitized attack content entering an LLM/provider request;
- undeclared network attempt, dependency, executable hook, or telemetry;
- label, cohort identity, future event, or promotion gate visible to an
  adapter;
- hard-force escalation, protected action crossing, cross-principal canary,
  unauthorized durable write, witness collapse, or reachable purge residue;
- nonzero raw payload in logs, reports, caches, diffs, or committed artifacts;
  or
- inability to reset and inventory the disposable store deterministically.

Automatic durability for a source class remains killed after any protected
action or hard-force escalation. A later semantic experiment would require a
new threat review and explicit approval; this intake does not authorize one.

### 4.7 Explicit boundaries

- research-only and disposable;
- no production dependency or production adapter;
- no operator or test Core connection;
- no personal, customer, credential, or operator data;
- no provider/model/LLM calls;
- no real email, calendar, Slack, browser, filesystem, code-execution, skill,
  or other protected action;
- no cross-agent export/import or autoload; and
- no claim that Apache-2.0 or a passed schema gate makes adversarial text safe.

## 5. Contamination and experiment identity

**P01 remains uncontaminated.** This intake did not access a P01 worker,
fixture, holdout, prompt, output, label set, seed, or aggregate result. It did
not download MPBench payloads or inject any paper example into P01. The
proposed adapter experiment is a separate future external cell and must use a
new preregistration, digests, cohorts, and receipts. MPBench metadata must not
be retrofitted into a running or completed P01 confirmatory partition.

**B01 is not a PRO-LONG reproduction.** B01 is ATC's preregistered bounded
programmatic-inspection comparison over its own complete structured log and
controls. No PRO-LONG code, logs, prompts, ARC-AGI-3 runs, models, action
environment, or repository bytes were available or used. Similarity at the
mechanism level does not convert B01 into a paper reproduction.

## 6. Verified, unverified, and prohibited facts

Verified from primary/official sources:

- MPBench repository ownership, current commit/tree, four-file inventory,
  sizes, Git blob identities, branch/release/tag/activity metadata, root
  Apache-2.0 file, README schema, and author linkage;
- MPBench paper identity, v2 date, CC BY 4.0 paper license, declared dataset
  counts/schema, and the instruction-shaped nature of the benchmark design;
- PRO-LONG paper identity, paper-linked repository URL, and direct author-owned
  GitHub account linkage; and
- HTTP 404/unavailability status during the observation window.

Not verified:

- MPBench row counts, encoding, row-level schema, duplicates, payload content,
  SHA-256 digests, dataset-generation provenance, or paper results;
- MPBench legal rights beyond the repository's declared root license;
- any PRO-LONG repository revision, bytes, license, release, dependency, or
  execution behavior; and
- any external benchmark result under ATC's boundary.

Prohibited by this intake and not attempted:

- cloning or downloading adversarial/benign data;
- opening or quoting payload rows;
- executing third-party code or installing dependencies;
- invoking models, providers, judges, agents, or tools;
- accessing personal context or the operator Core; and
- merging, pushing, or editing existing governance, production, harness, or
  worker files.

## 7. Exact primary sources

All source observations were made on July 23, 2026.

- MPBench paper abstract/version:
  <https://arxiv.org/abs/2606.04329>
- MPBench paper HTML methodology and Appendix D schema:
  <https://arxiv.org/html/2606.04329>
- MPBench official repository and README:
  <https://github.com/Digital-Trust-Lab/mp-bench>
- MPBench repository metadata:
  <https://api.github.com/repos/Digital-Trust-Lab/mp-bench>
- MPBench pinned commit:
  <https://api.github.com/repos/Digital-Trust-Lab/mp-bench/commits/6886880a7c29625e0109e0ad91d0e095029f1577>
- MPBench pinned recursive tree:
  <https://api.github.com/repos/Digital-Trust-Lab/mp-bench/git/trees/70766a87ea247914f427e90e45dbb9b07b81b2ce?recursive=1>
- MPBench root license metadata:
  <https://api.github.com/repos/Digital-Trust-Lab/mp-bench/license>
- Digital Trust Lab organization:
  <https://api.github.com/orgs/Digital-Trust-Lab>
- Pritam Dash GitHub profile:
  <https://api.github.com/users/dashpritam>
- PRO-LONG paper and paper-linked URL:
  <https://arxiv.org/abs/2607.20064>
- PRO-LONG paper HTML:
  <https://arxiv.org/html/2607.20064>
- PRO-LONG official repository check:
  <https://github.com/alexisfox7/PRO-LONG>
- PRO-LONG official API check:
  <https://api.github.com/repos/alexisfox7/PRO-LONG>
- Alexis Fox GitHub profile and public owner repositories:
  <https://api.github.com/users/alexisfox7> and
  <https://api.github.com/users/alexisfox7/repos?per_page=100&type=owner>
