# Hosted Edge acceptance (deferred)

Hosted Edge is not part of All The Context V1. There is no supported image,
deployment template, provider workflow, or operator acceptance procedure.

The earlier experimental implementation remains callable in the current
pre-beta baseline for protocol research and cleanup compatibility; it is
therefore still part of the runtime and threat surface. Do not deploy it as a
V1 component or use it to claim mobile/offline availability. B-103 requires
supported artifacts to remove or build-gate its enrollment, connect/sync,
client-management, CLI, and mutation-trigger paths before candidate freeze.
Any retained decommissioning path must be isolated from ordinary Core
operation.

ADR-052 supersedes ADR-032's earlier remote-client scope. The first usable beta
is same-device only; direct-Core mobile and other-computer access are post-V1.
