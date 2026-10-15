-- HU-34: revisar candidatos y postulaciones de club.
--
-- 1. Estados: PENDIENTE, PRESELECCIONADO, ACEPTADO, RECHAZADO. PRESELECCIONADO
--    replaces HU-33's EN_REVISION; existing EN_REVISION rows become PRESELECCIONADO.
-- 2. comentario_club: optional feedback the athlete reads.
-- 3. clubs_api.notification_outbox: same contract as matching_api and
--    activities_api. The service writes the event in the same transaction as
--    the status change; the Ms_gateway worker moves it to the athletes' inbox
--    and push. Its own schema gives the gateway a distinct source name.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 003. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
DECLARE
    old_check text;
BEGIN
    SELECT conname INTO old_check
    FROM pg_constraint
    WHERE conrelid = 'public.postulacion_convocatoria'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%EN_REVISION%';

    IF old_check IS NOT NULL THEN
        BEGIN
            EXECUTE format('ALTER TABLE postulacion_convocatoria DROP CONSTRAINT %I', old_check);
            UPDATE postulacion_convocatoria
            SET estado = 'PRESELECCIONADO', fecha_actualizacion = now()
            WHERE estado = 'EN_REVISION';
            ALTER TABLE postulacion_convocatoria
                ADD CONSTRAINT postulacion_convocatoria_estado_check
                CHECK (estado IN ('PENDIENTE', 'PRESELECCIONADO', 'ACEPTADO', 'RECHAZADO'));
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan los estados de la HU-34 en postulacion_convocatoria: ejecuta 004_revision_postulaciones.sql como postgres';
        END;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.postulacion_convocatoria'::regclass
          AND attname = 'comentario_club' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE postulacion_convocatoria ADD COLUMN comentario_club varchar(1000);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta postulacion_convocatoria.comentario_club: ejecuta 004_revision_postulaciones.sql como postgres';
        END;
    END IF;

    IF to_regclass('clubs_api.notification_outbox') IS NULL THEN
        BEGIN
            CREATE SCHEMA IF NOT EXISTS clubs_api;
            CREATE TABLE clubs_api.notification_outbox (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id uuid NOT NULL,
                kind text NOT NULL,
                title text NOT NULL,
                body text NOT NULL,
                data jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                published_at timestamptz
            );
            CREATE INDEX clubs_outbox_pending
                ON clubs_api.notification_outbox (id) WHERE published_at IS NULL;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta clubs_api.notification_outbox: ejecuta 004_revision_postulaciones.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- The service inserts events; the gateway worker, which connects with
        -- this same role, reads, acknowledges and purges them.
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT USAGE ON SCHEMA clubs_api TO sportmatch_clubs;
            GRANT SELECT, INSERT, UPDATE, DELETE ON clubs_api.notification_outbox TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_schema_privilege('sportmatch_clubs', 'clubs_api', 'USAGE')
            AND has_table_privilege('sportmatch_clubs', 'clubs_api.notification_outbox', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'clubs_api.notification_outbox', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'clubs_api.notification_outbox', 'UPDATE')
            AND has_table_privilege('sportmatch_clubs', 'clubs_api.notification_outbox', 'DELETE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre clubs_api.notification_outbox para sportmatch_clubs: ejecuta 004_revision_postulaciones.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
