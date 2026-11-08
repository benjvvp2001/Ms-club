-- HU-68: editar una convocatoria. Historial de ediciones.
--
-- Una fila por edición que cambió algo: quién la hizo (usuario_id, cuenta de Ms_Users, sin FK: otra base), cuándo,
-- qué cambió (cambios: {"campo": {"antes": ..., "despues": ...}}) y a cuántos postulantes se les avisó. Una petición
-- que no cambia nada no deja fila. La fila se escribe en la misma transacción que la edición.
--
-- La edición de actividades (HU-24) no guarda historial; este es el de las convocatorias.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 014. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    IF to_regclass('public.convocatoria') IS NULL THEN
        RAISE EXCEPTION 'Falta convocatoria: aplica 002_convocatorias.sql antes de 015_convocatoria_historial.sql';
    END IF;

    -- CREATE TABLE needs CREATE on the schema even when the table exists.
    IF to_regclass('public.convocatoria_historial') IS NULL THEN
        BEGIN
            CREATE TABLE convocatoria_historial (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                convocatoria_id uuid NOT NULL REFERENCES convocatoria (id),
                usuario_id uuid NOT NULL,
                fecha timestamptz NOT NULL DEFAULT now(),
                cambios jsonb NOT NULL CHECK (jsonb_typeof(cambios) = 'object' AND cambios <> '{}'::jsonb),
                avisados integer NOT NULL DEFAULT 0 CHECK (avisados >= 0)
            );
            CREATE INDEX convocatoria_historial_idx ON convocatoria_historial (convocatoria_id, fecha DESC, id DESC);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta la tabla convocatoria_historial: ejecuta 015_convocatoria_historial.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, UPDATE ON convocatoria_historial TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.convocatoria_historial', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.convocatoria_historial', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.convocatoria_historial', 'UPDATE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre convocatoria_historial para sportmatch_clubs: ejecuta 015_convocatoria_historial.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
