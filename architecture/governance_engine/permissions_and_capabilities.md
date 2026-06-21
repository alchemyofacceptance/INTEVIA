# Permissions & Capabilities v0.1

## Purpose

This file defines roles, capabilities, and permission evaluation logic for the INTEVIA Governance Engine.

Permissions answer:

> Is this actor allowed to perform this action?

Capabilities answer:

> Is this actor recognised as having the authority, status, or functional capacity required for this action?

Together, permissions and capabilities ensure that INTEVIA actions are not merely available because a user can click a button. They must be allowed by the organism’s governance rules.

---

## v0.1 Status

This is a placeholder architecture file.

Full content will be expanded in v0.2.

---

## v0.2 Expansion Targets

The next version should define:

* role model;
* capability model;
* permission evaluation flow;
* relationship between roles and capabilities;
* module-level permissions;
* state-transition permissions;
* evidence and audit permissions;
* boundary rule enforcement;
* governance warnings and denial states;
* v1.0 implementation constraints.

---

## Boundary Note

For v1.0, permissions and capabilities should remain minimal, explicit, and executable.

The priority is not to model every future role in the organism. The priority is to ensure that Events, Services, Library, evidence, audit, and CARE flows respect clear governance boundaries from the beginning.
