# Relay operations (deferred)

V1 has no Relay or hosted Edge service. Core is the only user-facing runtime
and does not start the legacy Edge synchronization worker.

Experimental Relay code and explicit cleanup APIs remain dormant so old
engineering setups can be decommissioned safely. They are not packaged as a
supported deployment, advertised in the dashboard, or included in release
acceptance. A future synchronization service would require a new architecture
decision and threat model.
