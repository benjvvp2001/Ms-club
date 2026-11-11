-- HU-56: gestionar los planes y convenios disponibles.
--
-- plan_suscripcion
--   * categoria: PLAN (por defecto) o CONVENIO. Un convenio es un plan acordado con una institucion o un club:
--     se compra igual (tipo USUARIO o CLUB dice QUIEN lo compra) pero el equipo lo distingue en el catalogo.
--   * beneficios: lista de textos (jsonb, hasta 10).
--   * precio puede ser 0 (plan o convenio gratuito, que no se paga con Webpay) y tiene un tope.
--   * el nombre es unico entre los planes ACTIVOS (sin distinguir mayusculas): uno desactivado libera su nombre.
--     Reemplaza a UNIQUE (tipo, nombre), que bloqueaba reutilizar el nombre de un plan retirado.
--   * el servicio puede crear y editar planes (INSERT, UPDATE) pero nunca borrarlos: desactivar es is_active = false.
-- transaccion_pago y suscripcion guardan una COPIA de lo que el comprador pago (nombre, duracion, beneficios y,
--   en la suscripcion, el precio pagado), asi un cambio posterior en el plan no altera lo ya pagado.
--   Las filas existentes se rellenan con los valores actuales del plan y con el monto de su ultima transaccion:
--   su comportamiento no cambia.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 011. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    IF to_regclass('public.plan_suscripcion') IS NULL
       OR to_regclass('public.transaccion_pago') IS NULL
       OR to_regclass('public.suscripcion') IS NULL THEN
        RAISE EXCEPTION 'Faltan las tablas de pagos: aplica 006_pagos.sql antes de 012_gestion_planes.sql';
    END IF;

    -- plan_suscripcion: categoria, beneficios, precio >= 0, nombre unico entre activos.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.plan_suscripcion'::regclass AND attname = 'beneficios' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE plan_suscripcion
                ADD COLUMN categoria varchar(10) NOT NULL DEFAULT 'PLAN',
                ADD COLUMN beneficios jsonb NOT NULL DEFAULT '[]'::jsonb;
            ALTER TABLE plan_suscripcion
                ADD CONSTRAINT plan_suscripcion_categoria_check CHECK (categoria IN ('PLAN', 'CONVENIO')),
                ADD CONSTRAINT plan_suscripcion_beneficios_check
                    CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 10);
            ALTER TABLE plan_suscripcion DROP CONSTRAINT IF EXISTS plan_suscripcion_precio_check;
            ALTER TABLE plan_suscripcion
                ADD CONSTRAINT plan_suscripcion_precio_check CHECK (precio >= 0 AND precio <= 10000000);
            ALTER TABLE plan_suscripcion DROP CONSTRAINT IF EXISTS plan_suscripcion_tipo_nombre_key;
            CREATE UNIQUE INDEX IF NOT EXISTS plan_suscripcion_nombre_activo_uq
                ON plan_suscripcion (lower(btrim(nombre))) WHERE is_active;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan columnas de plan_suscripcion: ejecuta 012_gestion_planes.sql como postgres';
        END;
    END IF;

    -- transaccion_pago: copia del plan al iniciar el pago (el monto ya se copiaba en amount).
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.transaccion_pago'::regclass AND attname = 'beneficios' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE transaccion_pago
                ADD COLUMN plan_nombre varchar(60),
                ADD COLUMN duracion_dias integer,
                ADD COLUMN beneficios jsonb;
            UPDATE transaccion_pago t
            SET plan_nombre = p.nombre, duracion_dias = p.duracion_dias, beneficios = p.beneficios
            FROM plan_suscripcion p WHERE p.id = t.plan_id;
            ALTER TABLE transaccion_pago
                ALTER COLUMN plan_nombre SET NOT NULL,
                ALTER COLUMN duracion_dias SET NOT NULL,
                ALTER COLUMN beneficios SET NOT NULL;
            ALTER TABLE transaccion_pago
                ADD CONSTRAINT transaccion_pago_duracion_check CHECK (duracion_dias BETWEEN 1 AND 366),
                ADD CONSTRAINT transaccion_pago_beneficios_check
                    CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 10);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan columnas de transaccion_pago: ejecuta 012_gestion_planes.sql como postgres';
        END;
    END IF;

    -- suscripcion: copia de lo pagado en el ultimo pago (precio, duracion, beneficios).
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.suscripcion'::regclass AND attname = 'beneficios' AND NOT attisdropped
    ) THEN
        BEGIN
            ALTER TABLE suscripcion
                ADD COLUMN plan_nombre varchar(60),
                ADD COLUMN precio_pagado integer,
                ADD COLUMN duracion_dias integer,
                ADD COLUMN beneficios jsonb;
            -- Primero con los valores actuales del plan; donde se conoce el ultimo pago, con lo que se pago en el.
            UPDATE suscripcion s
            SET plan_nombre = p.nombre, precio_pagado = p.precio, duracion_dias = p.duracion_dias, beneficios = p.beneficios
            FROM plan_suscripcion p WHERE p.id = s.plan_id;
            UPDATE suscripcion s
            SET plan_nombre = t.plan_nombre, precio_pagado = t.amount, duracion_dias = t.duracion_dias, beneficios = t.beneficios
            FROM transaccion_pago t WHERE t.id = s.ultima_transaccion_id;
            ALTER TABLE suscripcion
                ALTER COLUMN plan_nombre SET NOT NULL,
                ALTER COLUMN precio_pagado SET NOT NULL,
                ALTER COLUMN duracion_dias SET NOT NULL,
                ALTER COLUMN beneficios SET NOT NULL;
            ALTER TABLE suscripcion
                ADD CONSTRAINT suscripcion_precio_pagado_check CHECK (precio_pagado >= 0),
                ADD CONSTRAINT suscripcion_duracion_check CHECK (duracion_dias BETWEEN 1 AND 366),
                ADD CONSTRAINT suscripcion_beneficios_check
                    CHECK (jsonb_typeof(beneficios) = 'array' AND jsonb_array_length(beneficios) <= 10);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan columnas de suscripcion: ejecuta 012_gestion_planes.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- Plans: create and edit, never delete. A GRANT by someone who is not the owner may only warn, so check afterwards.
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.plan_suscripcion', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.plan_suscripcion', 'UPDATE')
        ) THEN
            BEGIN
                GRANT SELECT, INSERT, UPDATE ON plan_suscripcion TO sportmatch_clubs;
            EXCEPTION WHEN insufficient_privilege THEN
                NULL;
            END;
            IF NOT (
                has_table_privilege('sportmatch_clubs', 'public.plan_suscripcion', 'INSERT')
                AND has_table_privilege('sportmatch_clubs', 'public.plan_suscripcion', 'UPDATE')
            ) THEN
                RAISE EXCEPTION 'Faltan permisos sobre plan_suscripcion para sportmatch_clubs: ejecuta 012_gestion_planes.sql como postgres';
            END IF;
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
