-- HU-31: administrar perfil de club.
--
-- Adds to the existing club table the profile fields a club representative
-- edits, and lets the service role write club and club_deporte.
--
-- The tables belong to postgres: run this file as postgres (pgAdmin Query Tool
-- connected to sportmatch_clubs). Once applied, running it again as the
-- service role is a no-op; if something is still missing it stops with a
-- clear error instead of leaving the service half-migrated.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
DECLARE
    missing text[];
BEGIN
    SELECT array_agg(col) INTO missing
    FROM unnest(ARRAY[
        'historia', 'comuna', 'provincia', 'sitio_web', 'redes_sociales',
        'portada', 'estado_verificacion', 'fecha_actualizacion'
    ]) AS col
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.club'::regclass AND attname = col AND NOT attisdropped
    );

    IF missing IS NOT NULL THEN
        BEGIN
            ALTER TABLE club
                ADD COLUMN IF NOT EXISTS historia varchar(3000),
                ADD COLUMN IF NOT EXISTS comuna varchar(80),
                ADD COLUMN IF NOT EXISTS provincia varchar(80),
                ADD COLUMN IF NOT EXISTS sitio_web varchar(300),
                ADD COLUMN IF NOT EXISTS redes_sociales jsonb NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(redes_sociales) = 'object'),
                ADD COLUMN IF NOT EXISTS portada varchar(255),
                -- Set by SportMatch staff, never by the club representative.
                ADD COLUMN IF NOT EXISTS estado_verificacion varchar(20) NOT NULL DEFAULT 'pendiente'
                    CHECK (estado_verificacion IN ('pendiente', 'verificado', 'rechazado')),
                ADD COLUMN IF NOT EXISTS fecha_actualizacion timestamptz NOT NULL DEFAULT now();
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan columnas en club (%): ejecuta 001_club_profile_admin.sql como postgres', missing;
        END;
    END IF;

    -- "My club" lookups and the one-club-per-representative check.
    IF to_regclass('public.club_usuario_idx') IS NULL THEN
        BEGIN
            CREATE INDEX club_usuario_idx ON club (usuario_id);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE WARNING 'Falta el índice club_usuario_idx: créalo como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, UPDATE ON club TO sportmatch_clubs;
            GRANT SELECT, INSERT, DELETE ON club_deporte TO sportmatch_clubs;
            GRANT SELECT ON deporte TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.club', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.club', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.club', 'UPDATE')
            AND has_table_privilege('sportmatch_clubs', 'public.club_deporte', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_deporte', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_deporte', 'DELETE')
            AND has_table_privilege('sportmatch_clubs', 'public.deporte', 'SELECT')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos para sportmatch_clubs: ejecuta 001_club_profile_admin.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
