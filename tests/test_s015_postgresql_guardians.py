from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from datetime import timedelta
from uuid import UUID
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from core.identity import canonical_username_v1
from core.models import (
    AuthorityBasis,
    AuthorityDerivationEdge,
    AuthorityPrincipal,
    AuthorityPrincipalAliasReview,
    ContextualRoleAssignment,
    Circle,
    EmergencyAuthorityEnvelope,
    GovernedDetermination,
    GovernedVisibilityGrant,
    Identity,
    LivingOrganism,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismRoleDefinition,
    ProfileEffectProposalLineage,
    ProfileEffectProposalTransition,
)
from s015_0021_support import T0, add_remaining_aggregates, append_event, empty_fp, found_nonroot, fresh, refresh


class S015PostgreSQLGuardianTests(TransactionTestCase):
    runtime_counts = {
        "a": 0,
        "b": 0,
        "c": 0,
        "d": 0,
        "e": 0,
        "f": 0,
        "g": 0,
        "h": 0,
        "i": 0,
        "j": 0,
        "positive_fresh": 0,
        "positive_lifecycle": 0,
    }

    @classmethod
    def tearDownClass(cls):
        print(
            "S015_P47_RUNTIME_RECEIPT="
            + json.dumps(cls.runtime_counts, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
        super().tearDownClass()

    def _assert_postgresql(self):
        self.assertEqual(connection.vendor, "postgresql")

    def _snapshot(self, instance, field_names):
        return {field_name: getattr(instance, field_name) for field_name in field_names}

    def _assert_preserved(self, instance, snapshot):
        instance.refresh_from_db()
        for field_name, expected in snapshot.items():
            self.assertEqual(
                getattr(instance, field_name),
                expected,
                msg=f"{type(instance).__name__}.{field_name} changed unexpectedly",
            )

    def _identity(self, label):
        username = f"{label}_{uuid4().hex[:12]}"
        user = User.objects.create_user(username=username, password="password123")
        return Identity.objects.create(
            credential=user,
            canonical_username=canonical_username_v1(username),
            display_name=f"{label} identity",
        )

    def _principal(self, label, namespace="INTEVIA_TEST_AUTHORITY_V1"):
        return AuthorityPrincipal.objects.create(
            canonical_governed_source_id=f"urn:intevia:test:s015:{label}:{uuid4().hex}",
            governed_source_namespace=namespace,
            display_label=f"{label} authority principal",
        )

    def _basis(self, *, principal, root_principal, issuer, label):
        now = timezone.now()
        return AuthorityBasis.objects.create(
            authority_principal=principal,
            derivation_root_principal=root_principal,
            issuer=issuer,
            authority_class=f"CLASS_{label.upper()}",
            scope_fingerprint=uuid4().hex + uuid4().hex,
            instrument_reference=f"urn:intevia:test:instrument:{label}:{uuid4().hex}",
            evidence_reference=f"urn:intevia:test:evidence:{label}:{uuid4().hex}",
            issued_at=now,
            effective_at=now,
            received_at=now,
        )

    def _determination(self, *, determiner, principal, label):
        now = timezone.now()
        return GovernedDetermination.objects.create(
            determination_type=
            GovernedDetermination.DeterminationType.CONSTITUTIONAL_AUTHORITY_QUALIFICATION,
            determiner=determiner,
            relied_upon_authority_principal=principal,
            result=f"RESULT_{label.upper()}",
            evidence_reference=f"urn:intevia:test:evidence:{label}:{uuid4().hex}",
            method_reference=f"urn:intevia:test:method:{label}:{uuid4().hex}",
            scope_fingerprint=uuid4().hex + uuid4().hex,
            independence_state="INDEPENDENT",
            occurred_at=now,
            effective_at=now,
            received_at=now,
        )

    def _organism(self, label):
        now = timezone.now()
        return LivingOrganism.objects.create(
            slug=f"{label}-{uuid4().hex[:16]}",
            current_state_effective_at=now,
            state_known_at=now,
            state_source_event_set_fingerprint=uuid4().hex + uuid4().hex,
            constitutional_spine_reference=f"urn:intevia:test:spine:{label}:{uuid4().hex}",
        )

    def _circle(self, organism, label):
        now = timezone.now()
        return Circle.objects.create(
            parent_organism=organism,
            state=Circle.State.ACTIVE,
            current_state_effective_at=now,
            state_known_at=now,
            state_source_event_set_fingerprint=uuid4().hex + uuid4().hex,
            founding_reference=f"urn:intevia:test:circle:{label}:{uuid4().hex}",
        )

    def _membership(self, *, identity, organism, label):
        now = timezone.now()
        return OrganismMembership.objects.create(
            identity=identity,
            living_organism=organism,
            state=OrganismMembership.State.ACTIVE,
            current_state_effective_at=now,
            state_known_at=now,
            state_source_event_set_fingerprint=uuid4().hex + uuid4().hex,
        )

    def _role_definition(self, organism, label):
        return OrganismRoleDefinition.objects.create(
            living_organism=organism,
            code="INTEVIA_FOUNDER_STEWARD",
            scope=OrganismRoleDefinition.Scope.LIVING_ORGANISM,
            definition_version=1,
        )

    def _assignment(self, *, membership, role_definition, circle, label):
        now = timezone.now()
        return ContextualRoleAssignment.objects.create(
            membership=membership,
            role_definition=role_definition,
            circle=circle,
            state="ACTIVE",
            current_state_effective_at=now,
            state_known_at=now,
            state_source_event_set_fingerprint=uuid4().hex + uuid4().hex,
        )

    def _alias_review(self, *, principal, label):
        return AuthorityPrincipalAliasReview.objects.create(
            candidate_governed_source_id=f"urn:intevia:test:candidate:{label}:{uuid4().hex}",
            candidate_namespace=f"INTEVIA_TEST_ALIAS_{label.upper()}",
            possible_principal=principal,
            state=AuthorityPrincipalAliasReview.State.BOUND_TO_EXISTING_PRINCIPAL,
            authority_reference=f"urn:intevia:test:authority:{label}:{uuid4().hex}",
            evidence_reference=f"urn:intevia:test:evidence:{label}:{uuid4().hex}",
        )

    def _derivation_edge(self, *, principal, root_principal, label):
        return AuthorityDerivationEdge.objects.create(
            principal=principal,
            root_principal=root_principal,
            authority_reference=f"urn:intevia:test:edge-authority:{label}:{uuid4().hex}",
            evidence_reference=f"urn:intevia:test:edge-evidence:{label}:{uuid4().hex}",
        )

    def _envelope(self, *, beneficiary, principal, root_principal, grant, label):
        return EmergencyAuthorityEnvelope.objects.create(
            beneficiary=beneficiary,
            authority_principal=principal,
            derivation_root_principal=root_principal,
            qualifying_constitutional_grant=grant,
            permitted_command_set={"actions": [f"{label}:found"]},
            target_scope_set={"scope": [f"{label}:organism"]},
            proportionality_bounds={"max": 1},
            hard_expiry=timezone.now() + timedelta(days=1),
        )

    def _complete_founding(self, *, label):
        with transaction.atomic():
            with connection.cursor() as cur:
                ids = add_remaining_aggregates(cur, found_nonroot(cur, label), f"{label}-aggregates")
                role_uuid = str(uuid4())
                cur.execute(
                    "INSERT INTO core_organismroledefinition "
                    "(role_uuid, code, scope, definition_version, active, created_at, living_organism_id) "
                    "VALUES (%s, %s, 'CIRCLE', 1, TRUE, now(), %s) RETURNING id",
                    (role_uuid, f"CIRCLE_ROLE_{label.upper()}", ids["lo"]),
                )
                role_definition_id = cur.fetchone()[0]
                assignment_uuid = str(uuid4())
                cur.execute(
                    "INSERT INTO core_contextualroleassignment "
                    "(assignment_uuid, state, created_at, current_state_effective_at, state_known_at, "
                    "state_source_event_set_fingerprint, membership_id, role_definition_id, circle_id) "
                    "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        assignment_uuid,
                        T0,
                        T0,
                        empty_fp(cur, "core_contextualroleassignment", assignment_uuid),
                        ids["membership"],
                        role_definition_id,
                        ids["circle"],
                    ),
                )
                assignment_id = cur.fetchone()[0]
                append_event(
                    cur,
                    "core_contextualroleassignmentstateevent",
                    assignment_id,
                    1,
                    "ASSIGN",
                    None,
                    "ACTIVE",
                    ids["identity"],
                    ids["basis"],
                    f"{label}-circle-assignment",
                )
                refresh(cur, "core_contextualroleassignment", assignment_id)

            founder = Identity.objects.get(pk=ids["identity"])
            principal = AuthorityPrincipal.objects.get(pk=ids["principal"])
            authority_basis = AuthorityBasis.objects.get(pk=ids["basis"])
            organism = LivingOrganism.objects.get(pk=ids["lo"])
            membership = OrganismMembership.objects.get(pk=ids["membership"])
            circle = Circle.objects.get(pk=ids["circle"])
            role_definition = OrganismRoleDefinition.objects.get(pk=role_definition_id)
            assignment = ContextualRoleAssignment.objects.get(pk=assignment_id)
            return {
                "founder": founder,
                "root_principal": principal,
                "principal": principal,
                "authority_basis": authority_basis,
                "organism": organism,
                "membership": membership,
                "circle": circle,
                "role_definition": role_definition,
                "assignment": assignment,
            }

    def test_immutable_anchor_update_delete_matrix_refused(self):
        self._assert_postgresql()
        guarded_models = (
            AuthorityPrincipal,
            AuthorityBasis,
            AuthorityDerivationEdge,
            AuthorityPrincipalAliasReview,
            GovernedDetermination,
            OrganismMembership,
            ContextualRoleAssignment,
            EmergencyAuthorityEnvelope,
        )
        self.assertEqual(len(guarded_models), 8)
        issuer = self._identity("guardian-issuer")
        principal = self._principal("guardian-principal")
        root_principal = self._principal("guardian-root")
        basis = self._basis(
            principal=principal,
            root_principal=root_principal,
            issuer=issuer,
            label="basis",
        )
        derivation_edge = self._derivation_edge(
            principal=principal,
            root_principal=root_principal,
            label="edge",
        )
        alias_review_delete = self._alias_review(principal=principal, label="delete")
        determination = self._determination(
            determiner=issuer,
            principal=principal,
            label="determination",
        )
        alias_review_truncate = self._alias_review(principal=principal, label="truncate")
        founding = self._complete_founding(label="organism")
        organism = founding["organism"]
        membership = founding["membership"]
        circle = founding["circle"]
        role_definition = founding["role_definition"]
        assignment = founding["assignment"]
        grant = self._determination(
            determiner=issuer,
            principal=principal,
            label="grant",
        )
        envelope = self._envelope(
            beneficiary=issuer,
            principal=principal,
            root_principal=root_principal,
            grant=grant,
            label="envelope",
        )

        cases = (
            {
                "limb": "a",
                "instance": principal,
                "sql": "UPDATE core_authorityprincipal SET principal_uuid = principal_uuid WHERE id = %s",
                "params": [principal.pk],
                "fields": (
                    "principal_uuid",
                    "canonical_governed_source_id",
                    "governed_source_namespace",
                    "display_label",
                ),
            },
            {
                "limb": "b",
                "instance": basis,
                "sql": "UPDATE core_authoritybasis SET basis_uuid = basis_uuid WHERE id = %s",
                "params": [basis.pk],
                "fields": (
                    "basis_uuid",
                    "authority_principal_id",
                    "derivation_root_principal_id",
                    "issuer_id",
                    "authority_class",
                    "scope_fingerprint",
                    "instrument_reference",
                    "evidence_reference",
                    "issued_at",
                    "effective_at",
                    "received_at",
                    "superseded_basis_id",
                ),
            },
            {
                "limb": "c",
                "instance": derivation_edge,
                "sql": "DELETE FROM core_authorityderivationedge WHERE id = %s",
                "params": [derivation_edge.pk],
                "fields": (
                    "edge_uuid",
                    "principal_id",
                    "root_principal_id",
                    "authority_reference",
                    "evidence_reference",
                ),
            },
            {
                "limb": "d",
                "instance": alias_review_delete,
                "sql": "DELETE FROM core_authorityprincipalaliasreview WHERE id = %s",
                "params": [alias_review_delete.pk],
                "fields": (
                    "review_uuid",
                    "candidate_governed_source_id",
                    "candidate_namespace",
                    "possible_principal_id",
                    "state",
                    "authority_reference",
                    "evidence_reference",
                ),
            },
            {
                "limb": "e",
                "instance": determination,
                "sql": "DELETE FROM core_governeddetermination WHERE id = %s",
                "params": [determination.pk],
                "fields": (
                    "determination_uuid",
                    "determination_type",
                    "determiner_id",
                    "relied_upon_authority_principal_id",
                    "result",
                    "evidence_reference",
                    "method_reference",
                    "scope_fingerprint",
                    "independence_state",
                    "occurred_at",
                    "effective_at",
                    "received_at",
                ),
            },
            {
                "limb": "f",
                "instance": membership,
                "sql": "UPDATE core_organismmembership SET membership_uuid = membership_uuid WHERE id = %s",
                "params": [membership.pk],
                "fields": (
                    "membership_uuid",
                    "identity_id",
                    "living_organism_id",
                    "state",
                    "current_state_effective_at",
                    "state_known_at",
                    "state_source_event_set_fingerprint",
                ),
            },
            {
                "limb": "g",
                "instance": assignment,
                "sql": "UPDATE core_contextualroleassignment SET assignment_uuid = assignment_uuid WHERE id = %s",
                "params": [assignment.pk],
                "fields": (
                    "assignment_uuid",
                    "membership_id",
                    "role_definition_id",
                    "circle_id",
                    "state",
                    "current_state_effective_at",
                    "state_known_at",
                    "state_source_event_set_fingerprint",
                ),
            },
            {
                "limb": "h",
                "instance": envelope,
                "sql": "UPDATE core_emergencyauthorityenvelope SET envelope_uuid = envelope_uuid WHERE id = %s",
                "params": [envelope.pk],
                "fields": (
                    "envelope_uuid",
                    "beneficiary_id",
                    "authority_principal_id",
                    "derivation_root_principal_id",
                    "qualifying_constitutional_grant_id",
                    "permitted_command_set",
                    "target_scope_set",
                    "proportionality_bounds",
                    "hard_expiry",
                ),
            },
            {
                "limb": "i",
                "instance": organism,
                "sql": "UPDATE core_livingorganism SET founding_insert_xid = founding_insert_xid WHERE id = %s",
                "params": [organism.pk],
                "fields": (
                    "organism_id",
                    "slug",
                    "constitutional_spine_reference",
                    "founding_insert_xid",
                    "founding_authority_basis_id",
                    "founder_identity_id",
                    "state",
                ),
            },
            {
                "limb": "j",
                "instance": alias_review_truncate,
                "sql": "TRUNCATE TABLE core_authorityprincipalaliasreview",
                "params": [],
                "fields": (
                    "review_uuid",
                    "candidate_governed_source_id",
                    "candidate_namespace",
                    "possible_principal_id",
                    "state",
                    "authority_reference",
                    "evidence_reference",
                ),
            },
        )

        for case in cases:
            with self.subTest(limb=case["limb"], model=type(case["instance"]).__name__):
                snapshot = self._snapshot(case["instance"], case["fields"])
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(case["sql"], case["params"])
                case["instance"].refresh_from_db()
                self.assertEqual(
                    self._snapshot(case["instance"], case["fields"]),
                    snapshot,
                )
                self.runtime_counts[case["limb"]] += 1

    def test_fresh_governed_row_supersedes_without_mutating_prior_identity(self):
        self._assert_postgresql()
        route_models = (
            AuthorityPrincipal,
            OrganismMembership,
            ContextualRoleAssignment,
        )
        self.assertEqual(len(route_models), 3)
        with transaction.atomic():
            prior = self._principal("fresh-prior")
            prior_snapshot = self._snapshot(
                prior,
                (
                    "principal_uuid",
                    "canonical_governed_source_id",
                    "governed_source_namespace",
                    "display_label",
                ),
            )
            successor = self._principal("fresh-successor")
            edge = self._derivation_edge(
                principal=successor,
                root_principal=prior,
                label="fresh-edge",
            )
            review = self._alias_review(principal=successor, label="fresh-review")

            self.assertNotEqual(prior.pk, successor.pk)
            self.assertNotEqual(
                prior.canonical_governed_source_id,
                successor.canonical_governed_source_id,
            )
            prior.refresh_from_db()
            successor.refresh_from_db()
            self.assertEqual(
                self._snapshot(
                    prior,
                    (
                        "principal_uuid",
                        "canonical_governed_source_id",
                        "governed_source_namespace",
                        "display_label",
                    ),
                ),
                prior_snapshot,
            )
            self.assertEqual(edge.principal_id, successor.pk)
            self.assertEqual(edge.root_principal_id, prior.pk)
            self.assertEqual(review.possible_principal_id, successor.pk)
            self.runtime_counts["positive_fresh"] += 4

    def test_lawful_anchor_projection_head_and_lifecycle_routes_accepted(self):
        self._assert_postgresql()
        with transaction.atomic():
            founding = self._complete_founding(label="head-organism")
            governing_identity = founding["founder"]
            authority_basis = founding["authority_basis"]
            organism = founding["organism"]
            membership = founding["membership"]
            circle = founding["circle"]
            role_definition = founding["role_definition"]
            assignment = founding["assignment"]
            before_xid = organism.founding_insert_xid
            self.assertIsNotNone(before_xid)
            self.assertEqual(organism.founding_authority_basis_id, authority_basis.pk)
            self.assertEqual(organism.founder_identity_id, governing_identity.pk)
            self.assertEqual(organism.state, LivingOrganism.State.ACTIVE)
            self.assertEqual(organism.founding_insert_xid, before_xid)
            self.assertEqual(membership.living_organism_id, organism.pk)
            self.assertEqual(assignment.membership_id, membership.pk)
            self.assertEqual(assignment.circle_id, circle.pk)
            self.runtime_counts["positive_lifecycle"] += 6

    def _canonical_snapshot_value(self, value):
        if isinstance(value, dict):
            return {
                key: self._canonical_snapshot_value(value[key])
                for key in sorted(value)
            }
        if isinstance(value, (list, tuple)):
            return [self._canonical_snapshot_value(item) for item in value]
        if isinstance(value, set):
            return [
                self._canonical_snapshot_value(item)
                for item in sorted(value, key=repr)
            ]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        return value

    def _encode_snapshot(self, payload):
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _capture_row_state(self, instance):
        payload = {
            field.attname: self._canonical_snapshot_value(
                getattr(instance, field.attname)
            )
            for field in instance._meta.concrete_fields
        }
        return self._encode_snapshot(
            {
                "kind": "row_state",
                "model": instance._meta.label,
                "pk": instance.pk,
                "columns": payload,
            }
        )

    def _capture_graph_edges(self, *instances):
        records = []
        for instance in instances:
            record = {
                "model": instance._meta.label,
                "pk": instance.pk,
            }
            for field in instance._meta.concrete_fields:
                if field.is_relation:
                    record[field.attname] = self._canonical_snapshot_value(
                        getattr(instance, field.attname)
                    )
            records.append(record)
        records.sort(key=lambda record: (record["model"], record["pk"] or 0))
        return self._encode_snapshot({"kind": "graph_edges", "records": records})

    def _capture_receipts(self):
        receipts = []
        for receipt in OrganismCommandReceipt.objects.order_by("pk"):
            receipts.append(
                {
                    "model": receipt._meta.label,
                    "pk": receipt.pk,
                    "columns": {
                        field.attname: self._canonical_snapshot_value(
                            getattr(receipt, field.attname)
                        )
                        for field in receipt._meta.concrete_fields
                    },
                }
            )
        return self._encode_snapshot({"kind": "receipts", "rows": receipts})

    def _capture_profile_effect_chain(self, lineage):
        transitions = []
        for transition in lineage.proposal_transitions.order_by("sequence", "pk"):
            transitions.append(
                {
                    "model": transition._meta.label,
                    "pk": transition.pk,
                    "columns": {
                        field.attname: self._canonical_snapshot_value(
                            getattr(transition, field.attname)
                        )
                        for field in transition._meta.concrete_fields
                    },
                    "previous_transition_id": transition.previous_transition_id,
                }
            )
        lineage.refresh_from_db()
        return self._encode_snapshot(
            {
                "kind": "recorded_chain",
                "lineage": {
                    "model": lineage._meta.label,
                    "pk": lineage.pk,
                    "columns": {
                        field.attname: self._canonical_snapshot_value(
                            getattr(lineage, field.attname)
                        )
                        for field in lineage._meta.concrete_fields
                    },
                    "head_proposal_transition_id": lineage.head_proposal_transition_id,
                },
                "transitions": transitions,
            }
        )

    def _capture_cached_projections(self, *instances):
        rows = []
        for instance in instances:
            rows.append(
                {
                    "model": instance._meta.label,
                    "pk": instance.pk,
                    "state": getattr(instance, "state", None),
                    "coordinates": {
                        "state_at": self._canonical_snapshot_value(
                            getattr(instance, "current_state_effective_at", None)
                        ),
                        "known_at": self._canonical_snapshot_value(
                            getattr(instance, "state_known_at", None)
                        ),
                        "source_event_set_fingerprint": getattr(
                            instance,
                            "state_source_event_set_fingerprint",
                            None,
                        ),
                    },
                }
            )
        rows.sort(key=lambda row: (row["model"], row["pk"] or 0))
        return self._encode_snapshot({"kind": "cached_projections", "rows": rows})

    def _profile_effect_probe(self, label):
        now = timezone.now()
        user = User.objects.create_user(
            username=f"{label}_{uuid4().hex[:12]}",
            password="unused",
        )
        identity = Identity.objects.create(
            credential=user,
            canonical_username=canonical_username_v1(user.username),
            display_name=f"{label} profile effect identity",
        )
        lineage = ProfileEffectProposalLineage.objects.create(
            subject=identity,
            proposer=identity,
            source_database_alias="default",
            source_activity_id=uuid4(),
            source_transition_pk=11,
            source_transition_sequence=1,
            source_transition_lineage_reference="s012l1:" + ("a" * 64),
            source_occurred_at=now,
            source_actor_access_epoch=identity.access_epoch,
            source_authority_reference=f"AUTH-{label}-001",
            source_qualification_reference="s012sq1:" + ("b" * 64),
            subject_relation=
            ProfileEffectProposalLineage.SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE,
            effect_type=
            ProfileEffectProposalLineage.EffectType.
            SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED,
            contract_version=1,
            has_current_survivor=True,
            created_at=now,
            updated_at=now,
        )
        transition = ProfileEffectProposalTransition.objects.create(
            lineage=lineage,
            sequence=1,
            previous_transition=None,
            action=ProfileEffectProposalTransition.Action.CREATE_PROPOSAL,
            from_state=None,
            to_state=ProfileEffectProposalTransition.State.ACTIVE,
            actor=identity,
            actor_access_epoch=identity.access_epoch,
            authority_reference=f"AUTH-{label}-002",
            authority_decision_reference="s013pa1:" + ("c" * 64),
            authority_evaluated_at=now,
            request_reference=f"REQ-{label}-001",
            idempotency_key=f"IDEM-{label}-001",
            payload_fingerprint="d" * 64,
            occurred_at=now,
            lineage_reference="s013pl1:" + ("e" * 64),
        )
        lineage.head_proposal_transition = transition
        lineage.save()
        return identity, lineage, transition

    def _append_profile_effect_transition(self, *, lineage, predecessor, label):
        now = timezone.now()
        transition = ProfileEffectProposalTransition.objects.create(
            lineage=lineage,
            sequence=predecessor.sequence + 1,
            previous_transition=predecessor,
            action=ProfileEffectProposalTransition.Action.SUPERSEDE_PROPOSAL,
            from_state=ProfileEffectProposalTransition.State.ACTIVE,
            to_state=ProfileEffectProposalTransition.State.ACTIVE,
            actor=lineage.subject,
            actor_access_epoch=lineage.subject.access_epoch,
            authority_reference=f"AUTH-{label}-003",
            authority_decision_reference="s013pa1:" + uuid4().hex + uuid4().hex,
            authority_evaluated_at=now,
            request_reference=f"REQ-{label}-002",
            idempotency_key=f"IDEM-{label}-002",
            payload_fingerprint=uuid4().hex + uuid4().hex,
            occurred_at=now,
            lineage_reference="s013pl1:" + uuid4().hex + uuid4().hex,
        )
        ProfileEffectProposalLineage.objects.filter(pk=lineage.pk).update(
            head_proposal_transition=transition,
            updated_at=transition.occurred_at,
        )
        lineage.refresh_from_db()
        return transition

    def test_harness_row_state_snapshot_detects_fresh_row_change(self):
        self._assert_postgresql()
        with transaction.atomic():
            first = self._principal("row-state-first")
            second = self._principal("row-state-second")
            first_snapshot = self._capture_row_state(first)
            second_snapshot = self._capture_row_state(second)
            self.assertNotEqual(first_snapshot[1], second_snapshot[1])

    def test_harness_graph_edge_snapshot_detects_new_relationship(self):
        self._assert_postgresql()
        with transaction.atomic():
            issuer = self._identity("graph-issuer")
            root_principal = self._principal("graph-root")
            principal = self._principal("graph-principal")
            basis = self._basis(
                principal=principal,
                root_principal=root_principal,
                issuer=issuer,
                label="graph-basis",
            )
            edge = self._derivation_edge(
                principal=principal,
                root_principal=root_principal,
                label="graph-edge",
            )
            grant = self._identity("graph-grant")
            visibility = GovernedVisibilityGrant.objects.create(
                subject_type="Identity",
                subject_id=grant.identity_id,
                audience_type="Identity",
                audience_id=issuer.identity_id,
                capacity_binding="GRAPH_READ",
                fields_permitted={"fields": ["display_name"]},
                purpose_reference="urn:intevia:test:purpose:graph",
                granting_authority=basis,
                expiry=timezone.now() + timedelta(days=1),
                lineage_reference="s015g1:" + uuid4().hex + uuid4().hex,
            )
            with connection.cursor() as cur:
                append_event(
                    cur,
                    "core_visibilitygrantstateevent",
                    visibility.pk,
                    1,
                    "ISSUE",
                    None,
                    "ISSUED",
                    issuer.pk,
                    basis.pk,
                    f"graph-visibility-{fresh()}",
                )
            before = self._capture_graph_edges(
                issuer,
                root_principal,
                principal,
                basis,
                edge,
                visibility,
            )
            successor_principal = self._principal("graph-successor")
            successor_edge = self._derivation_edge(
                principal=successor_principal,
                root_principal=principal,
                label="graph-edge-successor",
            )
            after = self._capture_graph_edges(
                issuer,
                root_principal,
                principal,
                basis,
                edge,
                visibility,
                successor_principal,
                successor_edge,
            )
            self.assertNotEqual(before[1], after[1])

    def test_harness_receipt_snapshot_detects_appended_receipt(self):
        self._assert_postgresql()
        with transaction.atomic():
            actor = self._identity("receipt-actor")
            before = self._capture_receipts()
            OrganismCommandReceipt.objects.create(
                actor=actor,
                action="SUBMIT_WORK",
                idempotency_key="receipt-idem-1",
                request_reference="urn:intevia:test:request:receipt-1",
                payload_fingerprint="a" * 64,
                authority_decision_reference="s015d1:" + uuid4().hex + uuid4().hex,
                lineage_reference="s015r1:" + uuid4().hex + uuid4().hex,
                result_payload={"accepted": True},
            )
            after_first = self._capture_receipts()
            OrganismCommandReceipt.objects.create(
                actor=actor,
                action="SUBMIT_WORK",
                idempotency_key="receipt-idem-2",
                request_reference="urn:intevia:test:request:receipt-2",
                payload_fingerprint="b" * 64,
                authority_decision_reference="s015d1:" + uuid4().hex + uuid4().hex,
                lineage_reference="s015r1:" + uuid4().hex + uuid4().hex,
                result_payload={"accepted": False},
            )
            after_second = self._capture_receipts()
            self.assertNotEqual(before[1], after_first[1])
            self.assertNotEqual(after_first[1], after_second[1])

    def test_harness_recorded_chain_snapshot_detects_head_advancement(self):
        self._assert_postgresql()
        with transaction.atomic():
            _, lineage, transition = self._profile_effect_probe("chain")
            before = self._capture_profile_effect_chain(lineage)
            successor = self._append_profile_effect_transition(
                lineage=lineage,
                predecessor=transition,
                label="chain",
            )
            after = self._capture_profile_effect_chain(lineage)
            self.assertNotEqual(before[1], after[1])
            self.assertEqual(lineage.head_proposal_transition_id, successor.pk)

    def test_harness_cached_projection_snapshot_detects_coordinate_change(self):
        self._assert_postgresql()
        with transaction.atomic():
            founding = self._complete_founding(label="cached-projection")
            before = self._capture_cached_projections(
                founding["organism"],
                founding["circle"],
                founding["membership"],
                founding["assignment"],
            )
            other = self._complete_founding(label="cached-projection-second")
            after = self._capture_cached_projections(
                founding["organism"],
                founding["circle"],
                founding["membership"],
                founding["assignment"],
                other["organism"],
            )
            self.assertNotEqual(before[1], after[1])
