
# S015 remedy migration.
#
# NO PREDICATE DIFFERS FROM THE PRECEDING VERSION. Only the commentary changed,
# after a second adversarial review found four false or overstated claims in it.
# The stored constraint definitions are unchanged and their behaviour has been
# tested; see the results record.
#
# ---------------------------------------------------------------------------
# TWO SENSES OF "REMEDY". THE PRECEDING VERSION USED BOTH AND SAID NEITHER.
# ---------------------------------------------------------------------------
#
# BY RULING, migration 0020 is the remedy channel for the five findings accepted
# 26 August 2026 (Human Governor, 31 August 2026). That ruling allocates the
# NUMBER and fixes the SCOPE the number now denotes; the chain build specified at
# S015_SECTION_6_0_RECONCILIATION_AND_MIGRATION_0020_SPECIFICATION_v1 takes a
# later number.
#
# BY CONTENT, this file discharges TWO obligations, not five:
#
#   Finding 1  the genesis identity match, ADDED at a declarative locus and
#              thereby extended from INSERT-only to INSERT and UPDATE.
#              NOT a relocation — the trigger is retained and still rejects
#              what it already rejected.
#   Finding 5  the reserved-genesis row may not END A STATEMENT carrying an
#              absent or wrong bootstrap fingerprint.
#
#   Finding 2  NOTHING ENTERS. Oracle 2 decides the self-edge disjunct is
#              entailed by an existing CHECK. It expressly does not decide the
#              redundant limb must be removed.
#   Finding 3  NOTHING ENTERS as a mechanism. Its DIAGNOSTIC PRINCIPLE — that
#              one rejection path serving several obligations names none of
#              them — is applied below and is disclosed as such. A design rule
#              drawn from a finding is not the same as remedying it.
#   Finding 4  NOTHING ENTERS. The lawful Circle.state value set is unruled, so
#              the placement criterion's first limb cannot be answered; the
#              founding-gated activation rule belongs to the chain's commit-time
#              guardian, with the interim exposure accepted by the Human
#              Governor on 31 August 2026.
#
# ---------------------------------------------------------------------------
# WHAT PLAN v9 LINE 315 REQUIRES, AND WHAT THESE CONSTRAINTS DO NOT DISCHARGE
# ---------------------------------------------------------------------------
#
# Line 315 (section "AuthorityPrincipal and AuthorityDerivationEdge", heading at
# line 309) requires that a single bootstrap "may create that AuthorityPrincipal
# only while the reserved namespace is empty and only when THE REQUEST BINDS THE
# EXACT INVOCATION BYTE IDENTITY", and that "the DEFERRABLE
# namespace-empty/exact-invocation guardian remains in addition to, not instead
# of, the index and refuses a differently identified invocation, A NULL OR
# FREE-STANDING GENESIS REFERENCE, or any bootstrap outside that exact command
# subtype."
#
# THESE CONSTRAINTS DISCHARGE ONE LIMB OF THAT: they refuse a null genesis
# reference. THEY DO NOT DISCHARGE THE REST, AND CANNOT.
#
#   THEY DO NOT BIND THE REQUEST. A CHECK compares a stored column against a
#   literal in the constraint. IT ESTABLISHES EQUALITY, NOT PROVENANCE. A caller
#   supplying the expected digest passes — trial 1 in the results record does
#   exactly that. Whether the value came from the actual invocation bytes is not
#   database-decidable, so under the placement criterion's second limb this
#   sits with the application, not here.
#
#   THEY ARE NOT DEFERRABLE. These are immediate, statement-end checks. If the
#   plan's "deferrable" binds implementation, a transaction reaching a
#   temporarily invalid state and repairing it before commit would be refused
#   here and permitted there. NO SUCH TRANSACTION HAS BEEN TESTED.
#
#   THEY DO NOT TEST NAMESPACE EMPTINESS. The partial unique index from 0019
#   holds the singleton. The plan says the guardian is in addition to the index,
#   not instead of it.
#
# Two candidates against 0019 follow from this and are RECORDED, NOT PURSUED, on
# the Human Governor's ruling of 31 August 2026: that its genesis guard may be
# required to be deferrable, and that its emptiness limb may be absent.
# 0019 installs `s015_guard_genesis_principal` as a BEFORE INSERT trigger, and
# DEFERRABLE occurs once in 0019, at line 273, for founding completion.
#
# ---------------------------------------------------------------------------
# PINNING, NOT IMMUTABILITY
# ---------------------------------------------------------------------------
#
# These constraints pin the genesis fingerprint's VALUE at the end of every
# statement. They do not make the column immutable: a same-value assignment
# succeeds, and ordinary principals may change their fingerprint freely.
#
# Guardian 23 (plan v9 line 529) requires column-target triggers to reject a
# listed column "even when OLD and NEW values are equal", and states its table
# "is exhaustive for guardian 23: a model row and column are protected by this
# guardian if and only if they appear below." For AuthorityPrincipal it lists
# principal_uuid, canonical_governed_source_id, governed_source_namespace.
# bootstrap_invocation_fingerprint is NOT among them.
#
#   THE SCOPE OF THAT INFERENCE, STATED NARROWLY. It establishes that the
#   fingerprint is not protected BY GUARDIAN 23. It does not by itself establish
#   that no other immutability obligation exists anywhere in the plan. The seat
#   found none, and the plan never names the column at all; but "found none" is
#   weaker than "there is none", and the difference is stated rather than
#   glossed.
#
# ---------------------------------------------------------------------------
# THREE-VALUED LOGIC. READ BEFORE ALTERING ANY PREDICATE.
# ---------------------------------------------------------------------------
#
# A CHECK admits a row evaluating TRUE *or* UNKNOWN. A PL/pgSQL IF executes only
# on TRUE. That asymmetry made 0019's trigger bypassable: with the correct
# canonical id and a NULL fingerprint its predicate evaluated FALSE OR UNKNOWN =
# UNKNOWN, the IF did not fire, and the row was accepted. Derived on the U17
# probe.
#
# ALL THREE PREDICATES BELOW DEPEND ON governed_source_namespace BEING NOT NULL.
# Each opens with a negated equality on that column. IF THAT COLUMN WERE
# NULLABLE, ALL THREE WOULD EVALUATE UNKNOWN FOR A NULL NAMESPACE AND ADMIT —
# INCLUDING THE PRESENCE CONSTRAINT. Under 0019 it carries no null=True
# (0019:306) and is NOT NULL, so those valuations are unreachable in this schema.
#
#   A preceding version claimed the presence predicate was "null-hostile by
#   construction" and that only the other two depended on column nullability.
#   THAT WAS WRONG IN BOTH HALVES. Corrected on review. The presence predicate is
#   null-hostile with respect to the FINGERPRINT only, given a non-null
#   namespace. The canonical predicate additionally depends on
#   canonical_governed_source_id being NOT NULL (0019:305). The match predicate
#   does not reference the canonical column at all.
#
# ---------------------------------------------------------------------------
# THREE CONSTRAINTS: WHAT THE SPLIT BUYS AND WHAT IT DOES NOT
# ---------------------------------------------------------------------------
#
# A preceding version combined fingerprint PRESENCE and MATCH into one
# constraint, so both violations surfaced one name — the shape Finding 3's
# diagnostic principle objects to, reproduced inside Finding 5's remedy.
#
# THE SPLIT CHANGES WHICH CONSTRAINT NAMES THE VIOLATION. IT DOES NOT CHANGE
# WHICH ROWS ARE ADMITTED. The admitted-row set is identical to the combined
# form. This is diagnostic topology, not additional integrity coverage, and it
# is worth having for that reason alone — but not for any other.
#
# THE `IS NULL` CLAUSE IN THE MATCH CONSTRAINT ADDS NO ENFORCEMENT. Without it a
# reserved/NULL row makes that predicate UNKNOWN, which a CHECK also admits;
# with it the predicate is TRUE. Either way the presence constraint is the sole
# refuser of that row. THE CLAUSE IS DOCUMENTATION, WRITTEN INTO THE PREDICATE
# SO THE INTENT IS VISIBLE IN THE CATALOGUE RATHER THAN ONLY IN A COMMENT.
#
# THE COUPLING IS REAL AND UNENFORCED BY THE DATABASE:
#   - drop the PRESENCE constraint and the NULL bypass silently reopens;
#   - drop the MATCH constraint and every wrong non-null value is admitted;
#   - PostgreSQL records NO dependency requiring the two to be kept together;
#   - models.py divergence (below) is a concrete path by which a future
#     makemigrations could propose removing them.
#
# In a multi-row statement carrying one absent and one wrong fingerprint, only
# the first violation encountered surfaces. MULTI-ROW BEHAVIOUR IS UNTESTED.
#
# ---------------------------------------------------------------------------
# WHAT IS NOT ESTABLISHED
# ---------------------------------------------------------------------------
#
# THE CANONICAL CONSTRAINT HAS NEVER REJECTED ANYTHING. On INSERT the retained
# trigger fires first; on UPDATE the guardian-23 immutability trigger refuses
# first. No current path reaches it. Dropping the genesis trigger would expose it
# on INSERT ONLY — guardian 23 would still mask it on UPDATE. It stands as the
# declarative record of the obligation and as coverage against that partial
# future, and that is the whole of its present justification.
#
# NO TRANSITION IS SUPPLIED FOR DATA 0019 ADMITTED. A committed reserved row
# with a NULL fingerprint blocks this migration — demonstrated on a seeded probe.
# The target database held zero rows in this table by exact count when this was
# written. A successor meeting such a row needs a route this file does not
# provide.
#
# models.py IS NOT UPDATED BY THIS MIGRATION. Django's migration state and the
# model class diverge; a future makemigrations will propose a removal. The
# custom violation messages below are application-layer metadata and are NOT
# present on the live model. That edit is owed and is a separate Human
# authorisation.
#
# NOTHING WAS TESTED AS A NON-SUPERUSER. No application path was tested. Both
# were owed by U17 and neither is discharged here.

from django.db import migrations, models


# Transcribed from 0019:9-16, in the working tree at
# core/migrations/0019_s015_living_organism_foundation.py, 30,461 bytes,
# sha256 a2a754e5b346a255057c85c915b862653516eac25dfdf3f12aa8ae4d962c061a.
# Restated rather than imported so this migration does not depend on another
# migration's module constants.
GENESIS_NAMESPACE = "INTEVIA_RESERVED_GENESIS_AUTHORITY_V1"
GENESIS_SOURCE = (
    "urn:intevia:governed-source:reserved-genesis:"
    "human-governor:carmian-owen:intevia-v1.0"
)
GENESIS_INVOCATION_SHA256 = (
    "57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7"
)

NOT_GENESIS = ~models.Q(governed_source_namespace=GENESIS_NAMESPACE)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_s015_living_organism_foundation"),
    ]

    operations = [
        # 1. CANONICAL SOURCE IDENTITY.
        #    Currently masked on every path. See WHAT IS NOT ESTABLISHED.
        migrations.AddConstraint(
            model_name="authorityprincipal",
            constraint=models.CheckConstraint(
                condition=(
                    NOT_GENESIS
                    | models.Q(canonical_governed_source_id=GENESIS_SOURCE)
                ),
                name="s015_genesis_canonical_source_ck",
                violation_error_message=(
                    "S015 reserved genesis namespace requires the expected "
                    "canonical governed source id"
                ),
            ),
        ),
        # 2. FINGERPRINT PRESENCE. THIS CONSTRAINT CLOSES THE DERIVED BYPASS.
        #
        # Under 0019 a row in the reserved namespace with the correct canonical
        # id and a NULL fingerprint is ACCEPTED — derived on the U17 probe.
        # Here it is REFUSED, on INSERT and on UPDATE.
        #
        # Discharges one limb of plan v9 line 315: refuses "a null or
        # free-standing genesis reference". THE OTHER LIMBS ARE NOT DISCHARGED
        # HERE; see the header.
        #
        # The IS NOT NULL test is load-bearing. Without it the reserved/NULL
        # case evaluates FALSE OR UNKNOWN, which a CHECK admits.
        migrations.AddConstraint(
            model_name="authorityprincipal",
            constraint=models.CheckConstraint(
                condition=(
                    NOT_GENESIS
                    | models.Q(bootstrap_invocation_fingerprint__isnull=False)
                ),
                name="s015_genesis_fingerprint_present_ck",
                violation_error_message=(
                    "S015 reserved genesis namespace requires the bootstrap "
                    "invocation fingerprint to be present"
                ),
            ),
        ),
        # 3. FINGERPRINT MATCH.
        #
        # Refuses a wrong non-null value. ADMITS NULL BY DESIGN — constraint 2
        # refuses that case. The IS NULL clause is documentation, not
        # enforcement; see the header.
        #
        # THIS ESTABLISHES EQUALITY TO A STORED LITERAL, NOT PROVENANCE. A
        # caller supplying the expected digest passes.
        migrations.AddConstraint(
            model_name="authorityprincipal",
            constraint=models.CheckConstraint(
                condition=(
                    NOT_GENESIS
                    | models.Q(bootstrap_invocation_fingerprint__isnull=True)
                    | models.Q(
                        bootstrap_invocation_fingerprint=GENESIS_INVOCATION_SHA256
                    )
                ),
                name="s015_genesis_fingerprint_matches_ck",
                violation_error_message=(
                    "S015 reserved genesis namespace requires the bootstrap "
                    "invocation fingerprint to match the Human invocation"
                ),
            ),
        ),
    ]
