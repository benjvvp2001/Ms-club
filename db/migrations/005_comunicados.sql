-- HU-38: avisos y comunicados de clubes.
--
-- comunicado_club: a notice published by a club for one audience.
-- comunicado_destinatario: the athletes who received it. The audience is
--   resolved once, when the notice is published, so the athlete's feed shows
--   exactly what was notified, whatever their postulaciones become later.
--
-- The notifications themselves go through clubs_api.notification_outbox
-- (migration 004), written by the service in the same transaction.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 004. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- CREATE TABLE needs CREATE on the schema even when the tables exist.
    IF to_regclass('public.comunicado_club') IS NULL
       OR to_regclass('public.comunicado_destinatario') IS NULL THEN
        BEGIN
            CREATE TABLE IF NOT EXISTS comunicado_club (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                club_id uuid NOT NULL REFERENCES club (id),
                titulo varchar(120) NOT NULL CHECK (length(btrim(titulo)) >= 3),
                contenido varchar(2000) NOT NULL CHECK (length(btrim(contenido)) >= 5),
                audiencia varchar(20) NOT NULL
                    CHECK (audiencia IN ('TODOS', 'POSTULANTES', 'MIEMBROS')),
                prioridad varchar(10) NOT NULL DEFAULT 'NORMAL'
                    CHECK (prioridad IN ('NORMAL', 'URGENTE')),
                fecha_creacion timestamptz NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS comunicado_club_idx
                ON comunicado_club (club_id, fecha_creacion DESC, id);

            CREATE TABLE IF NOT EXISTS comunicado_destinatario (
                comunicado_id uuid NOT NULL REFERENCES comunicado_club (id),
                -- Ms_Users account of the athlete (no FK: another database).
                usuario_id uuid NOT NULL,
                PRIMARY KEY (comunicado_id, usuario_id)
            );
            -- The athlete's feed.
            CREATE INDEX IF NOT EXISTS comunicado_destinatario_usuario_idx
                ON comunicado_destinatario (usuario_id, comunicado_id);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan las tablas de comunicados: ejecuta 005_comunicados.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- Notices are never edited or deleted: SELECT and INSERT are enough.
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT ON comunicado_club, comunicado_destinatario TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.comunicado_club', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.comunicado_club', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.comunicado_destinatario', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.comunicado_destinatario', 'INSERT')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre las tablas de comunicados para sportmatch_clubs: ejecuta 005_comunicados.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
