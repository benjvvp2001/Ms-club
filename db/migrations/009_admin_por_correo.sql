-- Administradores por correo (sprint clubes y chats, segunda vuelta, sección E).
--
-- club_admin_asignacion: el PRINCIPAL asigna un correo a su club. La asignación
--   queda pendiente hasta que una cuenta club_admin con ese correo (claim email
--   del token, ya verificado por Ms_Users) entra a GET /clubs/me: ahí se crea su
--   fila ADMIN en club_administrador y la asignación se marca aceptada. El
--   PRINCIPAL puede revocarla mientras está pendiente. Nunca se borran filas.
--   email va en minúsculas y sin espacios alrededor (lo normaliza el servicio).
-- Reemplaza a club_invitacion_admin (códigos de invitación), que se elimina.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 008. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- CREATE and DROP need ownership even when nothing changes.
    IF to_regclass('public.club_admin_asignacion') IS NULL
       OR to_regclass('public.club_invitacion_admin') IS NOT NULL THEN
        BEGIN
            CREATE TABLE IF NOT EXISTS club_admin_asignacion (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                club_id uuid NOT NULL REFERENCES club (id) ON DELETE CASCADE,
                email varchar(254) NOT NULL
                    CHECK (email = lower(btrim(email)) AND length(email) <= 254),
                -- The PRINCIPAL who assigned it (Ms_Users account, no FK).
                asignada_por uuid NOT NULL,
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                aceptada_por uuid,
                aceptada_en timestamptz,
                revocada_en timestamptz,
                CHECK ((aceptada_por IS NULL) = (aceptada_en IS NULL)),
                CHECK (aceptada_en IS NULL OR revocada_en IS NULL)
            );
            -- One pending assignment per (club, email).
            CREATE UNIQUE INDEX IF NOT EXISTS club_admin_asignacion_pendiente_uq
                ON club_admin_asignacion (club_id, email)
                WHERE aceptada_en IS NULL AND revocada_en IS NULL;
            -- The pending assignments of an email, looked up on GET /clubs/me.
            CREATE INDEX IF NOT EXISTS club_admin_asignacion_email_idx
                ON club_admin_asignacion (email, fecha_creacion)
                WHERE aceptada_en IS NULL AND revocada_en IS NULL;

            -- Invitation codes are replaced by assignments; pending codes are dropped.
            DROP TABLE IF EXISTS club_invitacion_admin;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta club_admin_asignacion o sigue club_invitacion_admin: ejecuta 009_admin_por_correo.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- Assignments are created and marked as accepted or revoked (UPDATE), never deleted.
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, UPDATE ON club_admin_asignacion TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.club_admin_asignacion', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_admin_asignacion', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_admin_asignacion', 'UPDATE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre club_admin_asignacion para sportmatch_clubs: ejecuta 009_admin_por_correo.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
