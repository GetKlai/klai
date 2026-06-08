---
paths:
  - "deploy/**"
---
# Infrastructure Boundary

Live server inventory, SSH access details, firewall procedures, tunnel topology,
DNS operations, and disaster recovery runbooks are private infrastructure
material.

Use the private `GetKlai/klai-infra` repository for:

- server inventory and roles
- SSH aliases, keys, jump-host procedures, and operator access
- production firewall, tunnel, and deployment procedures
- private DNS/provider operations
- disaster recovery instructions

The public `GetKlai/klai` repository may contain application code, public image
build workflows, and product-safe deployment templates. Do not add live host
addresses, direct SSH commands, key paths, unlock procedures, or private
operator runbooks here.
