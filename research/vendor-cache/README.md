# Isolated vendor research cache

This directory is reserved for explicitly approved, read-only research clones.
It is not a Python package, build input, test fixture source, or production
dependency.

Before placing a repository here:

1. confirm the canonical upstream and its license from an official source;
2. record the exact commit in the competitor intake manifest;
3. clone without installing dependencies, running setup hooks, or executing
   upstream code; and
4. keep all clone contents ignored by Git.

No source was cloned for the 2026-07-23 intake. Official repository metadata,
license files, dependency manifests, documentation, and papers were sufficient
for the decision, so duplicating third-party source added risk without adding
evidence.
