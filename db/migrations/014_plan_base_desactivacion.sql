-- HU-56 (panel web): lo que el panel de planes y convenios ya muestra y el backend no guardaba.
--
-- plan_suscripcion
--   * es_base: el plan base (el que toda cuenta nueva usa, p. ej. Gratuito). No se puede desactivar. Solo puede
--     haber uno por tipo (USUARIO o CLUB) y debe estar activo. Ninguna fila existente se marca: lo decide el equipo.
--   * fecha_desactivacion y motivo_desactivacion: cuándo y por qué se retiró del catálogo. NULL mientras está
--     activo (y NULL en los planes que ya estaban desactivados antes de esta migración: no se inventa la fecha).
--   * duracion_dias puede ser NULL = "sin vencimiento", solo si el precio es 0 (un plan de pago siempre tiene
--     duración). transaccion_pago y suscripcion no cambian: un pago exige precio > 0, así que siempre tienen duración.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 013. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    IF to_regclass('public.plan_suscripcion') IS NULL THEN
        RAISE EXCEPTION 'Falta plan_suscripcion: aplica 006_pagos.sql y 012_gestion_planes.sql antes de 014_plan_base_desactivacion.sql';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.plan_suscripcion'::regclass AND attname = 'es_base' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE plan_suscripcion
                ADD COLUMN es_base boolean NOT NULL DEFAULT false,
                ADD COLUMN fecha_desactivacion timestamptz,
                ADD COLUMN motivo_desactivacion varchar(300);
            ALTER TABLE plan_suscripcion
                ADD CONSTRAINT plan_suscripcion_base_activo_check CHECK (NOT es_base OR is_active),
                ADD CONSTRAINT plan_suscripcion_desactivacion_check
                    CHECK (NOT is_active OR (fecha_desactivacion IS NULL AND motivo_desactivacion IS NULL)),
                ADD CONSTRAINT plan_suscripcion_motivo_desactivacion_check
                    CHECK (motivo_desactivacion IS NULL OR length(btrim(motivo_desactivacion)) >= 3);
            CREATE UNIQUE INDEX IF NOT EXISTS plan_suscripcion_base_uq ON plan_suscripcion (tipo) WHERE es_base;
            -- Duración vacía = sin vencimiento, solo si es gratis.
            ALTER TABLE plan_suscripcion ALTER COLUMN duracion_dias DROP NOT NULL;
            ALTER TABLE plan_suscripcion DROP CONSTRAINT IF EXISTS plan_suscripcion_duracion_dias_check;
            ALTER TABLE plan_suscripcion
                ADD CONSTRAINT plan_suscripcion_duracion_dias_check
                    CHECK (duracion_dias IS NULL OR duracion_dias BETWEEN 1 AND 366),
                ADD CONSTRAINT plan_suscripcion_sin_vencimiento_check
                    CHECK (duracion_dias IS NOT NULL OR precio = 0);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan columnas de plan_suscripcion: ejecuta 014_plan_base_desactivacion.sql como postgres';
        END;
    END IF;

    -- El rol del servicio ya puede insertar y actualizar plan_suscripcion (012); las columnas nuevas heredan ese permiso.
END $$;

COMMIT;
