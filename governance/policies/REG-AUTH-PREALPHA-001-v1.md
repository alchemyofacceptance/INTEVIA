# REG-AUTH-PREALPHA-001 v1

## Authority and status

- Policy identity: `REG-AUTH-PREALPHA-001`
- Version: `v1`
- Human Governor: Carmien Owen
- Status: Human-authored internal pre-alpha policy instrument; not deployed or enabled by this instrument
- Environment: `internal-pre-alpha`
- Action: `register_self`

This instrument records the Human Governor's bounded authority for direct self-registration. Runtime code implements this policy but does not create, infer, or enlarge Human authority.

## Permitted request

The actor must be an authenticated, active internal-pre-alpha Identity. The actor and registration subject must be the same Identity. The Identity must be explicitly present in the policy's Identity allowlist.

The Event must be explicitly present in the policy's Event allowlist, must be owned by the acting Identity, and must be in the `published` or `active` state. Event ownership, visibility, or state alone does not grant authority.

Staff- or superuser-flagged credentials are absolutely denied, including when their Identity and Event are allowlisted and every other condition qualifies. Staff or superuser status never grants a bypass.

Eligibility is intrinsic EVENT-owned registration eligibility derived from the qualifying Event configuration. It is not an entitlement and does not independently grant authority.

## Effective binding

The runtime binding must match this policy identity, version, environment, and action. It must be explicitly enabled, have timezone-aware effective and expiry timestamps, and be evaluated at a time greater than or equal to the effective timestamp and strictly earlier than the expiry timestamp.

A revoked binding is unavailable. A binding with any superseding policy reference is unavailable. Amendment requires a new Human-authored instrument and explicit Human authority. Supersession must identify the successor; runtime configuration does not amend or supersede this instrument.

Missing, malformed, mismatched, disabled, not-yet-effective, expired, revoked, or superseded policy state fails closed. A non-qualifying account receives the existing account-refusal outcome. Other non-qualifying policy or Event state receives the existing unavailable outcome.

## References

For an authorised registration, the authority reference format is:

`reg-auth-prealpha-001:v1:<correlation-uuid>`

The self-submission evidence reference format is:

`self-submission:v1:<correlation-uuid>`

The EVENT-owned eligibility basis reference format is:

`event-configuration:registration:v1:<event-id>`

References and recorded evidence preserve bounded lineage only. Their presence, possession, replay, or discovery does not create authority.

## Deployment and enablement boundary

This instrument does not deploy the application, enable the policy, populate either allowlist, create operational data, or authorise publication. Deployment and policy enablement require separate explicit Human authority. The runtime binding remains externally supplied and disabled by default.

No authority may be inferred from a role name, staff or superuser status, authentication alone, Identity activity alone, Event ownership, Event visibility, Event state, an allowlist entry in isolation, a reference, evidence, a receipt, a route, or presentation text.

## Capability boundary

This policy creates no generic policy framework, policy engine, entitlement system, discovery mechanism, runtime authoring surface, administrative UI, or action beyond `register_self`.

Third-party registration, acknowledgement capture, invitations, approvals, attendance, check-in, capacity allocation, waitlisting, payment, reminders, automated registration, policy discovery, and policy administration remain deferred and unauthorised.

## Runtime relationship

`src/intevia/services/event_registration_policy.py` is the bounded runtime implementation of this instrument. It must preserve the explicit Identity and Event allowlists, self-only and owner-only scope, Event-state limits, effective and expiry bounds, revocation and supersession checks, absolute staff and superuser denial, fail-closed outcomes, and reference formats stated here.

The runtime implementation may enforce this Human-authored policy. It may not author, amend, supersede, discover, generalise, or enlarge it.