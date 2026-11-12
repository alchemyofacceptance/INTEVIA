"""The TIMED EVENTS measures, read by INTEVIA's Technology Circle and by nobody else.

Who may read (design D7.2): a person holding a current place in the Technology Circle of
the qualified platform root. That Circle is found by its id in the root's own validated
birth batch, under the entry named "INTEVIA Technology"; a Circle of the same name in any
other LO, a former place, or a root office with no place there does not qualify. The check
is made before anything is selected or added up across LOs. Every other caller gets the
one unavailable answer, whatever the reason: no place in that Circle, no membership, a
restricted identity, an inactive credential, or no identity at all. The reason is logged
by its fixed category and is never told. A failure that is not a refusal of access (a
database error, for one) is raised as itself.

What is returned: counts, times and safe codes, added up across LOs. No LO is named, and
no source or effect reference is returned, so the read opens no LO's records. The reader
gains no right to write anything.

The labels say what is measured. ``sampled_at`` is the time the read began; the queries
that follow are separate statements, so the figures are not one snapshot. A consumer's
times are its acted time before commit. ``pace_rows_by_last_failure`` counts pace rows
(one per LO and consumer) by the class and code of their last failure; it is not a
history of every failed attempt.

Not returned: the oldest outstanding due item. Finding it means asking every consumer's
selector about every LO, which needs its own bound. Deferred by the Human Governor's
ruling of 12 Nov 2026 to before Alpha (owed list B5).
"""

import logging

from django.db import connections

from core import models
from src.intevia.services import circle_commands as circles
from src.intevia.services.circle_participation import CircleAccessRefused

LOGGER = logging.getLogger("intevia.timed_events")

TECHNOLOGY = "INTEVIA Technology"


def _technology_circle(alias, root, at):
    """The id of the root's Technology Circle, from the root's validated birth batch, or None."""
    if not root.platform_root or not circles.qualified_root(alias, root, at):
        return None
    batch = models.RootCircleBatch.objects.using(alias).filter(living_organism=root).first()
    if batch is None:
        return None
    named = [entry["circle_uuid"] for entry in batch.circles if entry.get("name") == TECHNOLOGY]
    if len(named) != 1:
        return None
    circle = models.Circle.objects.using(alias).filter(parent_organism=root, circle_uuid=named[0]).first()
    return circle.pk if circle else None


def _dicts(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def read(*, credential, alias="default"):
    """The engine's measures across all LOs, for a current member of the Technology Circle.

    Every refusal of access leaves here as the one unavailable answer, with nothing of the
    refusal that caused it attached.
    """
    try:
        return _read(credential, alias)
    except CircleAccessRefused as refused:
        # The landed refusal texts are fixed phrases with no user text in them. They are kept
        # in the log, so that the reasons stay distinguishable to an operator, and go no further.
        LOGGER.info("timed events measures read refused (%s)", refused)
    circles.unavailable()


def _read(credential, alias):
    root = models.LivingOrganism.objects.using(alias).filter(platform_root=True).first()
    if root is None:
        circles.unavailable()
    with circles.command_context(alias, credential, root.organism_id, require_member=True) as (lo, actor, at):
        circle_id = _technology_circle(alias, lo, at)
        with connections[alias].cursor() as cursor:
            cursor.execute("""SELECT EXISTS(SELECT FROM public.core_organismmembership m
                WHERE m.living_organism_id=%s AND m.identity_id=%s AND public.circle_place_at(%s,m.id,%s,%s))""",
                           [lo.pk, actor.pk, circle_id, at, at])
            if circle_id is None or cursor.fetchone()[0] is not True:
                circles.unavailable()
            # Only now is anything read across LOs.
            cursor.execute("""SELECT consumer,count(*) AS organisms,sum(probes)::bigint AS probes,
                sum(opened)::bigint AS opened,sum(found_done)::bigint AS found_done,sum(blocked)::bigint AS blocked,
                sum(failed)::bigint AS failed,max(last_ms) AS last_ms_max,max(max_ms) AS max_ms
                FROM public.core_tepace GROUP BY consumer ORDER BY consumer""")
            consumers = _dicts(cursor)
            cursor.execute("""SELECT last_failure_class AS failure_class,last_failure_code AS code,count(*) AS pace_rows
                FROM public.core_tepace WHERE last_failure_class IS NOT NULL
                GROUP BY last_failure_class,last_failure_code ORDER BY 1,2""")
            last_failures = _dicts(cursor)
            cursor.execute("""SELECT coalesce(sum(busy_lo),0)::bigint AS busy_lo,coalesce(sum(busy_inner),0)::bigint AS busy_inner,
                coalesce(sum(in_transaction),0)::bigint AS in_transaction,coalesce(sum(not_eligible),0)::bigint AS not_eligible,
                coalesce(sum(no_limits),0)::bigint AS no_limits,coalesce(sum(commit_failed),0)::bigint AS commit_failed,
                coalesce(sum(errors),0)::bigint AS errors FROM public.core_temeter""")
            skips = _dicts(cursor)[0]
            cursor.execute("""SELECT consumer,purpose,outcome,count(*) AS receipts,
                max(effect_at-due_at) AS lateness_max,avg(effect_at-due_at) AS lateness_mean
                FROM public.core_tereceipt GROUP BY consumer,purpose,outcome ORDER BY 1,2,3""")
            receipts = _dicts(cursor)
        return {"sampled_at": at, "consumers": consumers, "pace_rows_by_last_failure": last_failures,
                "skips_lower_bound": skips, "receipts": receipts}
