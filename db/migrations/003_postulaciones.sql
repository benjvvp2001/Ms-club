-- HU-33: postular a convocatoria de club.
--
-- One convocatoria has many postulaciones; an athlete applies once to each.
-- perfil_deportivo is a snapshot of the athlete's public card in Ms_Users at
-- the moment of applying: the club sees what the athlete sent, and club
-- representatives cannot read athlete cards from Ms_Users themselves.
-- Every postulación except RECHAZADO takes one of the convocatoria's cupos.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 002. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- CREATE TABLE needs CREATE on the schema even when the table exists.
    IF to_regclass('public.postulacion_convocatoria') IS NULL THEN
        BEGIN
            CREATE TABLE postulacion_convocatoria (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                convocatoria_id uuid NOT NULL REFERENCES convocatoria (id),
                -- Ms_Users account of the athlete (no FK: another database).
                usuario_id uuid NOT NULL,
                estado varchar(20) NOT NULL DEFAULT 'PENDIENTE'
                    CHECK (estado IN ('PENDIENTE', 'EN_REVISION', 'ACEPTADO', 'RECHAZADO')),
                mensaje varchar(1000),
                antecedentes jsonb NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(antecedentes) = 'array'),
                perfil_deportivo jsonb NOT NULL
                    CHECK (jsonb_typeof(perfil_deportivo) = 'object'),
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                fecha_actualizacion timestamptz NOT NULL DEFAULT now(),
                UNIQUE (convocatoria_id, usuario_id)
            );
            CREATE INDEX postulacion_usuario_idx
                ON postulacion_convocatoria (usuario_id, fecha_creacion DESC, id);
            -- Counting the cupos taken by a convocatoria.
            CREATE INDEX postulacion_activas_idx
                ON postulacion_convocatoria (convocatoria_id) WHERE estado <> 'RECHAZADO';
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Falta la tabla postulacion_convocatoria: ejecuta 003_postulaciones.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, UPDATE ON postulacion_convocatoria TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.postulacion_convocatoria', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.postulacion_convocatoria', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.postulacion_convocatoria', 'UPDATE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre postulacion_convocatoria para sportmatch_clubs: ejecuta 003_postulaciones.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
