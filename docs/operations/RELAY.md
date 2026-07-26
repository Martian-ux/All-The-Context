# Relay operations (pre-beta removal boundary)

The intended V1 beta has no Relay or hosted Edge service. Core is the only
supported user-facing runtime.

The current pre-beta baseline is not yet at that boundary. It still constructs
experimental Edge managers and exposes enrollment, connection, synchronization,
client-management, CLI, and mutation-trigger surfaces. Because callable paths
exist, they remain in the runtime and threat surface even when the ordinary
demo does not use them. B-103 is a beta blocker: every supported package must
remove or build-gate those paths and acceptance must prove that ordinary Core
operation cannot reach them.

An isolated cleanup or compatibility path may remain only so old engineering
setups can be decommissioned deliberately; it must not be reachable during
ordinary Core operation. When that narrow path is explicitly exercised, Relay
accepts signed ordered projections from Core and queues encrypted observations
for later Core evaluation. It never runs `automatic-v1`, changes an observation
disposition, or creates current context. A future supported synchronization
service requires a new product decision, architecture decision, and threat
model without weakening that sole-authority boundary.
