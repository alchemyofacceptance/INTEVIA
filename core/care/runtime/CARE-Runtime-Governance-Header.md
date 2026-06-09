# CARE Runtime Governance Header
# ------------------------------
# This file defines runtime behaviour for CARE. Runtime is behavioural, not constitutional.
# It must never modify ontology, governance rules, or migrations.
#
# Runtime operates within three boundaries:
# 1. Human Authority — The Human governor Qualifies and has final say over all behaviour.
# 2. Governance Spine — GP‑07 and related principles define how the system evolves.
# 3. CARE Ontology — All runtime behaviour must align with the ontology defined in migrations.
#
# Runtime MAY:
# - interpret context
# - apply CARE behavioural logic
# - generate nudges, signals, or responses
# - update runtime state
#
# Runtime MAY NOT:
# - change ontology or migrations
# - redefine governance rules
# - override Human decisions
# - introduce new structural concepts
#
# All runtime behaviour must remain contextual, reversible, and subordinate to governance.
# If in doubt, defer to the Human governor.
