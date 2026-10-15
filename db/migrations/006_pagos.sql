-- HU-40: contratar plan con Webpay Plus.
--
-- plan_suscripcion: catalog of plans a user or a club can buy. The service only
--   reads it; plans and prices are managed by SportMatch staff (SQL for now).
-- transaccion_pago: one row per payment attempt, from the moment the Webpay
--   transaction is created (PENDING) until it is settled.
-- suscripcion: the buyer's current subscription, one row per user or club.
--   Its state (ACTIVE / EXPIRED) is computed from fecha_fin when read, so
--   nothing has to run when a subscription expires.
--
-- These tables are new and independent from the plan / membresia / pago tables
-- that exist in the sportmatch_users database (another database).
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 005. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- CREATE TABLE needs CREATE on the schema even when the tables exist.
    IF to_regclass('public.plan_suscripcion') IS NULL
       OR to_regclass('public.transaccion_pago') IS NULL
       OR to_regclass('public.suscripcion') IS NULL THEN
        BEGIN
            CREATE TABLE IF NOT EXISTS plan_suscripcion (
                id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                nombre varchar(60) NOT NULL CHECK (length(btrim(nombre)) >= 3),
                -- Who can buy it: an athlete (USUARIO) or a club representative (CLUB).
                tipo varchar(10) NOT NULL CHECK (tipo IN ('USUARIO', 'CLUB')),
                descripcion varchar(255),
                -- Whole Chilean pesos: Webpay Plus charges integer CLP amounts.
                precio integer NOT NULL CHECK (precio > 0),
                duracion_dias integer NOT NULL DEFAULT 30 CHECK (duracion_dias BETWEEN 1 AND 366),
                is_active boolean NOT NULL DEFAULT true,
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                UNIQUE (tipo, nombre)
            );

            CREATE TABLE IF NOT EXISTS transaccion_pago (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                -- The buyer: an athlete's Ms_Users account (no FK, another
                -- database) or a club. Exactly one of them.
                usuario_id uuid,
                club_id uuid REFERENCES club (id),
                -- Account that started the payment and may confirm it.
                iniciada_por uuid NOT NULL,
                plan_id integer NOT NULL REFERENCES plan_suscripcion (id),
                buy_order varchar(26) NOT NULL UNIQUE,
                session_id varchar(61) NOT NULL,
                token_ws varchar(64) NOT NULL UNIQUE,
                -- Copied from the plan when the payment starts, never from the client.
                amount integer NOT NULL CHECK (amount > 0),
                status varchar(10) NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
                -- Result of the commit at Transbank. Only the last 4 digits of
                -- the card are kept.
                response_code integer,
                authorization_code varchar(6),
                payment_type_code varchar(3),
                card_last4 varchar(4),
                installments_number smallint,
                transaction_date timestamptz,
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                fecha_actualizacion timestamptz NOT NULL DEFAULT now(),
                CHECK ((usuario_id IS NULL) <> (club_id IS NULL))
            );
            CREATE INDEX IF NOT EXISTS transaccion_pago_iniciada_idx
                ON transaccion_pago (iniciada_por, fecha_creacion DESC);

            CREATE TABLE IF NOT EXISTS suscripcion (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                usuario_id uuid,
                club_id uuid REFERENCES club (id),
                plan_id integer NOT NULL REFERENCES plan_suscripcion (id),
                fecha_inicio timestamptz NOT NULL,
                fecha_fin timestamptz NOT NULL,
                ultima_transaccion_id uuid REFERENCES transaccion_pago (id),
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                fecha_actualizacion timestamptz NOT NULL DEFAULT now(),
                CHECK ((usuario_id IS NULL) <> (club_id IS NULL)),
                CHECK (fecha_fin > fecha_inicio)
            );
            -- One subscription per buyer: renewing extends it (upsert).
            CREATE UNIQUE INDEX IF NOT EXISTS suscripcion_usuario_uq
                ON suscripcion (usuario_id) WHERE usuario_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS suscripcion_club_uq
                ON suscripcion (club_id) WHERE club_id IS NOT NULL;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan las tablas de pagos: ejecuta 006_pagos.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- Plans are read-only for the service. Payments and subscriptions are
        -- never deleted: a payment record is kept for accounting.
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT ON plan_suscripcion TO sportmatch_clubs;
            GRANT SELECT, INSERT, UPDATE ON transaccion_pago, suscripcion TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.plan_suscripcion', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.transaccion_pago', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.transaccion_pago', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.transaccion_pago', 'UPDATE')
            AND has_table_privilege('sportmatch_clubs', 'public.suscripcion', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.suscripcion', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.suscripcion', 'UPDATE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre las tablas de pagos para sportmatch_clubs: ejecuta 006_pagos.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
