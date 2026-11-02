-- HU-56 (panel web): la institución de un convenio.
--
-- plan_suscripcion.institucion: con quién se firmó el convenio (opcional, hasta 80 caracteres). Solo un CONVENIO
--   puede tenerla; un plan normal la deja en NULL. Las filas existentes quedan en NULL: nada cambia para ellas.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 012. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    IF to_regclass('public.plan_suscripcion') IS NULL THEN
        RAISE EXCEPTION 'Falta plan_suscripcion: aplica 006_pagos.sql y 012_gestion_planes.sql antes de 013_plan_institucion.sql';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.plan_suscripcion'::regclass AND attname = 'institucion' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE plan_suscripcion ADD COLUMN institucion varchar(80);
            ALTER TABLE plan_suscripcion
                ADD CONSTRAINT plan_suscripcion_institucion_check
                CHECK (institucion IS NULL OR (categoria = 'CONVENIO' AND length(btrim(institucion)) >= 3));
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta plan_suscripcion.institucion: ejecuta 013_plan_institucion.sql como postgres';
        END;
    END IF;

    -- El rol del servicio ya puede insertar y actualizar plan_suscripcion (012); una columna nueva hereda ese permiso.
END $$;

COMMIT;
