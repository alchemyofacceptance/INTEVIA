"""Keep voting on throughout an ordinary Board's active tenure."""

from importlib import import_module

from django.db import migrations

head = import_module("core.migrations.0084_committee_mandates")
hard, elect = head.hard, head.elect
one = elect.one
BOARD = (
    "Switching voting off was refused. Voting stays on while the Board is active. "
    "Nothing changed. Put Board stand-down to the members or the body named in the "
    "constitution. Your LO Coordinator(s) can help."
)


def previous(module, name):
    return next(row[-1] for row in module.edits() if elect.p1.name(row[-1]) == name)


def edits():
    checked = previous(elect.disc4, "cq4_checked")
    new = one(checked, "plpgsql STABLE", "plpgsql VOLATILE")
    new = one(new, "owner_uuid uuid;", "owner_uuid uuid; executed timestamptz;")
    yield (
        checked,
        one(
            new,
            " RETURN checked;",
            """
 IF (checked->'settings'->>'enabled')::boolean IS FALSE THEN
   executed:=clock_timestamp();
   IF EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=p_lo AND NOT lo.platform_root)
      AND (public.cq3_board(p_lo,p_cut,p_cut) IS NOT NULL OR
           public.cq3_board(p_lo,executed,executed) IS NOT NULL) THEN
     PERFORM public.cq_refuse('CQ_BOARD_ACTIVE');
   END IF;
 END IF;
 RETURN checked;""",
        ),
    )
    for plan in (previous(elect.disc4, "cq4_plan"), elect.settings_at_open()):
        anchor = "     WHEN 'CQ_SETTINGS_STALE' THEN"
        yield (
            plan,
            one(
                plan,
                anchor,
                "     WHEN 'CQ_BOARD_ACTIVE' THEN '" + BOARD + "'\n" + anchor,
            ),
        )
    yield from time_edit()
    yield from receipt_edits()


def replace_sql(sql):
    return (
        sql
        if "CREATE OR REPLACE FUNCTION" in sql
        else one(sql, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    )


def attributes(sql):
    header = sql.split(" AS $$")[0].upper()
    return (
        " STRICT" in header,
        "s" if " STABLE" in header else "v",
        "plpgsql" if "LANGUAGE PLPGSQL" in header else "sql",
    )


def change(editor, reverse):
    pairs = list(edits())
    with editor.connection.cursor() as cur:
        for old, new in pairs:
            sql = new if reverse else old
            sig = head.sig(sql)
            cur.execute(
                """SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text
              FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""",
                [sig],
            )
            if cur.fetchone() != (hard.body(sql), *attributes(sql)):
                raise RuntimeError(
                    "C1-108 stopped before DDL. Unexpected definition for " + sig
                )
        # No activation or settings write can cross the inventory/installation cut.
        cur.execute(
            "LOCK TABLE public.core_cqact,public.core_circlestateevent IN SHARE ROW EXCLUSIVE MODE"
        )
        if not reverse:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_livingorganism lo
              WHERE NOT lo.platform_root AND public.cq3_board(lo.id,statement_timestamp(),statement_timestamp()) IS NOT NULL
                AND (public.cq_settings_values_at(lo.id,statement_timestamp(),statement_timestamp())->>'enabled')::boolean IS FALSE)""")
            if cur.fetchone()[0]:
                raise RuntimeError(
                    "An active Board has voting off. Obtain a governed correction before upgrading."
                )
        receipt_preflight(cur, reverse)
    for old, new in reversed(pairs) if reverse else pairs:
        editor.execute(replace_sql(old if reverse else new), params=None)
    receipt_scope(editor, reverse)


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies = (("core", "0084_committee_mandates"),)
    operations = (migrations.RunPython(install, uninstall),)


def time_edit():
    old = hard.SELECTOR_SQL
    header, query = old.split(" AS $$", 1)
    query = query.rsplit("$$;", 1)[0].strip()
    # The scalar SQL expression is identical for every valid pair of cuts.
    new = (
        header.replace("LANGUAGE sql STABLE STRICT", "LANGUAGE plpgsql STABLE")
        + """ AS $$
BEGIN
 IF p_effective IS NULL OR p_known IS NULL THEN
   PERFORM public.cq_refuse('CQ_SETTINGS_TIME');
 END IF;
 IF p_lo IS NULL THEN RETURN NULL; END IF;
 RETURN ("""
        + query
        + """);
END $$;"""
    )
    yield old, new


RECEIPT = "REFUSE_VOTING_OFF"
PERSONAL = (
    "The members decide these settings by vote. Put the change forward as a Suggestion."
)


def receipt_edits():
    # Raw and service inserts take the same path. No caller may supply a receipt.
    old = previous(head, "cq_input")
    new = one(
        old,
        "v_allowed text[];",
        """v_allowed text[]; denial text; specific text;
 attempted text:=NEW.operation; nested_operation text;""",
    )
    anchor = " IF NEW.operation='ELECT_VACANCY' THEN"
    new = one(
        new,
        anchor,
        """
 IF NEW.operation='REFUSE_VOTING_OFF' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
"""
        + anchor,
    )
    start = new.index(
        "   IF NEW.request ? 'result' THEN\n     NEW.facts:=public.cq4_checked"
    )
    stop = new.index(" ELSIF NEW.operation IN ('PROCEED'", start)
    # Settings written by a result must pair with its implementation. Settlement
    # already returns NO_EFFECT if the Board became active before the vote closed.
    new = (
        new[:start]
        + "   BEGIN\n"
        + new[start:stop]
        + """
   EXCEPTION WHEN check_violation THEN
     specific:=SQLERRM;
     IF NEW.request ? 'result' THEN RAISE; END IF;
     IF specific='CQ_BOARD_ACTIVE' THEN denial:=specific;
     ELSIF specific='CQ_SETTINGS_VOTE' AND NEW.request->'changes'->'enabled'='false'::jsonb THEN
       -- Preserve admission precedence. Only record the Board rule where it
       -- independently applies to an otherwise well-formed settings patch.
       BEGIN
         PERFORM public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp);
       EXCEPTION WHEN check_violation THEN
         IF SQLERRM='CQ_BOARD_ACTIVE' THEN denial:=specific; END IF;
       END;
       IF denial IS NULL THEN RAISE; END IF;
     ELSE RAISE;
     END IF;
   END;
"""
        + new[stop:]
    )
    anchor = "   NEW.facts:=NEW.facts||public.cq3_attachment_facts(NEW,v_stamp);"
    new = one(
        new,
        anchor,
        """   BEGIN
"""
        + anchor
        + """
   EXCEPTION WHEN check_violation THEN
     IF SQLERRM<>'CQ_BOARD_ACTIVE' THEN RAISE; END IF;
     denial:='CQ_BOARD_ACTIVE';
   END;""",
    )
    anchor = " SELECT coalesce(max(a.ordinal),0)+1"
    yield (
        old,
        one(
            new,
            anchor,
            """
 IF denial IS NOT NULL THEN
   IF pg_trigger_depth()=1 THEN
     -- Reject the original INSERT (zero affected rows). A nested insert of
     -- its original inputs independently derives the immutable receipt.
     INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
       actor_id,actor_epoch,received_at,request)
     VALUES(NEW.act_uuid,NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,attempted,
       NEW.actor_id,NEW.actor_epoch,NEW.received_at,NEW.request)
     RETURNING operation INTO nested_operation;
     IF nested_operation IS DISTINCT FROM 'REFUSE_VOTING_OFF' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
     RETURN NULL;
   END IF;
   NEW.request:=jsonb_build_object('operation',attempted,'board',NEW.board_uuid,'thread',NEW.thread_uuid,
      'request_fingerprint',public.disc_fingerprint(NEW.request));
   NEW.operation:='REFUSE_VOTING_OFF'; NEW.board_uuid:=NULL; NEW.thread_uuid:=NULL;
   NEW.facts:=jsonb_build_object('code',denial,'rule','CQ_BOARD_ACTIVE','reason',
     CASE WHEN denial='CQ_SETTINGS_VOTE' THEN '"""
            + PERSONAL
            + "' ELSE '"
            + BOARD
            + """' END);
 END IF;
"""
            + anchor,
        ),
    )
    old = previous(head.head, "cq_complete")
    anchor = (
        " IF v_act.operation IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN"
    )
    yield (
        old,
        one(
            old,
            anchor,
            """ IF v_act.operation='REFUSE_VOTING_OFF' THEN
   -- The input trigger alone derives this shape, after admission. The common
   -- checks above still require the exact four-time ILC pair and active enterer.
   RETURN NULL;
 END IF;
"""
            + anchor,
        ),
    )

    old = previous(head.head, "cq_execute")
    anchor = "   IF v_old.organism_id<>v_lo OR v_old.board_uuid"
    new = one(old, "v_existing_board uuid;", "v_existing_board uuid; written text;")
    new = one(
        new,
        "p_received,p_args);\n RETURN p_key;",
        "p_received,p_args) RETURNING operation INTO written;\n"
        " IF written IS NULL OR written='REFUSE_VOTING_OFF' THEN RETURN NULL; END IF;\n RETURN p_key;",
    )
    yield (
        old,
        one(
            new,
            anchor,
            """   IF v_old.operation='REFUSE_VOTING_OFF' THEN
     IF v_old.organism_id<>v_lo OR v_old.actor_id<>p_actor
        OR v_old.request->>'board' IS DISTINCT FROM p_board::text THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     IF v_old.actor_epoch<>p_epoch OR v_old.request IS DISTINCT FROM jsonb_build_object('operation',p_op,
       'board',p_board,'thread',p_thread,'request_fingerprint',public.disc_fingerprint(p_args))
       THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
     RETURN NULL;
   END IF;
"""
            + anchor,
        ),
    )
    old = elect.function(elect.disc4.previous.HELPERS, "cq4_direct_admit")
    anchor = "   IF prior.organism_id<>p_lo"
    yield (
        old,
        one(
            old,
            anchor,
            """   IF prior.operation='REFUSE_VOTING_OFF' THEN
     IF prior.organism_id<>p_lo OR prior.actor_id<>p_actor OR prior.request->>'operation'<>'SETTINGS'
        THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     RETURN; -- cq_execute checks epoch and exact request.
   END IF;
"""
            + anchor,
        ),
    )


def receipt_preflight(cur, reverse):
    if reverse:
        cur.execute(
            "SELECT EXISTS(SELECT FROM public.core_cqact WHERE operation='REFUSE_VOTING_OFF')"
        )
        if cur.fetchone()[0]:
            raise RuntimeError("C1-108 retains its recorded refusals.")


def receipt_scope(editor, reverse):
    # 0083's two shape constraints describe the owner-level operations.
    # Keep every existing operation and admit the one new owner-level receipt.
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT c.conname FROM pg_constraint c WHERE c.conrelid='public.core_cqact'::regclass
          AND c.contype='c' AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM unnest(c.conkey) k
             JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k)=ARRAY['board_uuid','operation','thread_uuid']""")
        rows = cur.fetchall()
    if len(rows) != 1:
        raise RuntimeError("Cannot identify the CQ owner-scope constraint.")
    name = editor.quote_name(rows[0][0])
    ops = ("SETTINGS", "ELECT_VACANCY", *head.head.OPS) + (
        () if reverse else (RECEIPT,)
    )
    allowed = ",".join("'" + op + "'" for op in ops)
    editor.execute(
        f"ALTER TABLE public.core_cqact DROP CONSTRAINT {name}, ADD CONSTRAINT {name} "
        f"CHECK ((thread_uuid IS NULL AND board_uuid IS NULL)=(operation IN ({allowed})))",
        params=None,
    )
    p1 = elect.p1
    editor.execute(
        p1.votes.operations_constraint(p1.PRIOR_OPS + p1.NOMINATIONS + ops[1:]),
        params=None,
    )
