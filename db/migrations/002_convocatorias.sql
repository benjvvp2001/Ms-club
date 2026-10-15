-- HU-32: publicar convocatorias deportivas (pruebas masivas y de jugadores).
--
-- One club has many convocatorias. The effective state is computed when
-- reading, so nothing has to update rows when a date passes:
--   FINALIZADA  fecha_prueba already passed
--   CERRADA     the club closed it before that (cerrada_en is set)
--   ABIERTA     otherwise
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 001. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- CREATE TABLE needs CREATE on the schema even when the table exists.
    IF to_regclass('public.convocatoria') IS NULL THEN
        BEGIN
            CREATE TABLE convocatoria (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                club_id uuid NOT NULL REFERENCES club (id),
                deporte_id integer NOT NULL REFERENCES deporte (id),
                titulo varchar(120),
                descripcion varchar(2000),
                categoria_edad varchar(40) NOT NULL CHECK (length(btrim(categoria_edad)) >= 2),
                edad_minima smallint CHECK (edad_minima BETWEEN 4 AND 99),
                edad_maxima smallint CHECK (edad_maxima BETWEEN 4 AND 99),
                fecha_prueba timestamptz NOT NULL,
                requisitos varchar(200)[] NOT NULL
                    CHECK (cardinality(requisitos) BETWEEN 1 AND 15),
                cupos integer NOT NULL CHECK (cupos BETWEEN 1 AND 1000),
                direccion varchar(255) NOT NULL CHECK (length(btrim(direccion)) >= 5),
                comuna varchar(80),
                cerrada_en timestamptz,
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                fecha_actualizacion timestamptz NOT NULL DEFAULT now(),
                CHECK (edad_minima IS NULL OR edad_maxima IS NULL OR edad_minima <= edad_maxima)
            );
            CREATE INDEX convocatoria_fecha_idx ON convocatoria (fecha_prueba, id);
            CREATE INDEX convocatoria_club_idx ON convocatoria (club_id, fecha_prueba);
            CREATE INDEX convocatoria_deporte_idx ON convocatoria (deporte_id, fecha_prueba);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta la tabla convocatoria: ejecuta 002_convocatorias.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, UPDATE ON convocatoria TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.convocatoria', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.convocatoria', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.convocatoria', 'UPDATE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre convocatoria para sportmatch_clubs: ejecuta 002_convocatorias.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
