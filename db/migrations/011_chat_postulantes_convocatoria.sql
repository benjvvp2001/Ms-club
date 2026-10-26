-- Chat de postulación por convocatoria: el club decide si sus postulantes
-- pueden escribir en la consulta de esa convocatoria.
--
-- chat_postulantes_habilitado: true (por defecto) conserva lo que hoy hace el
--   sistema, donde cualquier postulante activo puede escribir al club. Con
--   false, el postulante solo lee; el club siempre puede escribir. Las filas
--   existentes quedan en true, así que nada cambia para las convocatorias ya
--   publicadas.
-- Los estados de postulación no cambian: PENDIENTE ya es "en espera" (migración
-- 004 y 003), así que esta migración solo agrega la columna.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 010. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.convocatoria'::regclass
          AND attname = 'chat_postulantes_habilitado' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE convocatoria
                ADD COLUMN chat_postulantes_habilitado boolean NOT NULL DEFAULT true;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta convocatoria.chat_postulantes_habilitado: ejecuta 011_chat_postulantes_convocatoria.sql como postgres';
        END;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
