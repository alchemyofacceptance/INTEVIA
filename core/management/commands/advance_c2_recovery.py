"""Notice governed C2 due dates. Each organisation advances in its own transaction."""
from django.core.management.base import BaseCommand
from django.db import connections

from src.intevia.services.organism_recovery import advance


class Command(BaseCommand):
    help = "Record due coverage assessments and K-2 transitions."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        alias = options["database"]
        with connections[alias].cursor() as cursor:
            cursor.execute("""
                SELECT lo.id FROM public.core_livingorganism lo
                JOIN public.core_livingorganismevent h ON h.id=lo.head_event_id
                WHERE NOT lo.platform_root AND h.resulting_state<>'CLOSED'
                AND (EXISTS (SELECT FROM public.c2_phase_at(lo.id,statement_timestamp(),statement_timestamp()) phase
                       WHERE phase.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE') AND phase.effective_until<=statement_timestamp())
                  OR EXISTS (SELECT FROM public.c2_timed_input i WHERE i.lo_id=lo.id AND i.due<=statement_timestamp()
                    AND NOT EXISTS (SELECT FROM public.core_coverageassessment a
                      JOIN public.core_essentialcoveragecase c ON c.id=a.case_id WHERE c.living_organism_id=lo.id
                      AND a.evidence_reference='c2-boundary:'||i.source AND a.effective_at=i.due)))
                ORDER BY lo.organism_id""")
            ids = [row[0] for row in cursor.fetchall()]
        count = sum(advance(alias, pk) for pk in ids)
        self.stdout.write(f"Checked {len(ids)} organisations and recorded {count} countdown transitions.")
