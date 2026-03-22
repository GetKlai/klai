# LibreChat tenant template directory

This directory is intentionally empty in the repository.

At runtime, the portal-api service writes per-tenant LibreChat configuration
files here (librechat.yaml per tenant). These files are mounted into the
librechat containers and are NOT version-controlled.

To provision a new tenant, use the portal API or run the provisioning flow
via the portal frontend.
