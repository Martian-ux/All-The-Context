# Relay operations (deferred)

V1 has no Relay or hosted Edge service. Core is the only user-facing runtime
and does not start the legacy Edge synchronization worker.

Experimental Relay code and explicit cleanup APIs remain dormant so old
engineering setups can be decommissioned safely. They are not packaged as a
supported deployment, advertised in the dashboard, or included in release
acceptance.

Even when exercised explicitly for compatibility, Relay accepts signed ordered
projections from Core and queues encrypted observations for later Core
evaluation. It never runs `automatic-v1`, changes an observation disposition,
or creates current context. A future supported synchronization service would
require a new architecture decision and threat model without weakening that
sole-authority boundary.
