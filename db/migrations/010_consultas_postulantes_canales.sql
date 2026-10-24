-- Consultas solo con postulantes y canales del club (sprint clubes y chats,
-- tercera vuelta, secciones G3 y G4).
--
-- G3: cuando una postulación pasa a RECHAZADO y la persona ya no tiene otra
--   postulación activa en el club, su consulta_hilo se elimina con sus mensajes
--   (misma transacción). consulta_mensaje pasa a ON DELETE CASCADE y el rol del
--   servicio recibe DELETE sobre las dos tablas.
-- G4: club_canal (canales de anuncios, máximo 20 por club, lo controla el
--   servicio), club_canal_miembro (postulantes del club agregados al canal, con
--   su marca de leído y si lo silenciaron) y club_canal_mensaje. ultima_actividad
--   es la fecha del último mensaje (o de creación): con ella el proceso de fondo
--   avisa a los 30 días sin mensajes (aviso_inactividad_en) y elimina el canal si
--   nadie responde en 7 días; mantener_hasta pospone el siguiente aviso.
--   Los nombres son copias (Ms_Users está en otra base): el del miembro sale del
--   perfil_deportivo de su postulación; el del club se lee de club al mostrar.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 009. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
DECLARE
    fk_name text;
    fk_action "char";
BEGIN
    SELECT conname, confdeltype INTO fk_name, fk_action
    FROM pg_constraint
    WHERE conrelid = 'public.consulta_mensaje'::regclass
      AND confrelid = 'public.consulta_hilo'::regclass
      AND contype = 'f';

    -- ALTER and CREATE need ownership even when nothing changes.
    IF fk_action IS DISTINCT FROM 'c'
       OR to_regclass('public.club_canal') IS NULL
       OR to_regclass('public.club_canal_miembro') IS NULL
       OR to_regclass('public.club_canal_mensaje') IS NULL THEN
        BEGIN
            IF fk_action IS DISTINCT FROM 'c' THEN
                IF fk_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE consulta_mensaje DROP CONSTRAINT %I', fk_name);
                END IF;
                ALTER TABLE consulta_mensaje
                    ADD CONSTRAINT consulta_mensaje_hilo_id_fkey
                    FOREIGN KEY (hilo_id) REFERENCES consulta_hilo (id) ON DELETE CASCADE;
            END IF;

            CREATE TABLE IF NOT EXISTS club_canal (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                club_id uuid NOT NULL REFERENCES club (id) ON DELETE CASCADE,
                nombre varchar(60) NOT NULL CHECK (length(btrim(nombre)) >= 1),
                miembros_escriben boolean NOT NULL DEFAULT false,
                -- Last message id read by the club (shared by its administrators).
                leido_club_id bigint NOT NULL DEFAULT 0 CHECK (leido_club_id >= 0),
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                ultima_actividad timestamptz NOT NULL DEFAULT now(),
                aviso_inactividad_en timestamptz,
                mantener_hasta timestamptz
            );
            -- The club's channels and the hourly inactivity check.
            CREATE INDEX IF NOT EXISTS club_canal_club_idx ON club_canal (club_id);
            CREATE INDEX IF NOT EXISTS club_canal_actividad_idx ON club_canal (ultima_actividad);

            CREATE TABLE IF NOT EXISTS club_canal_miembro (
                canal_id uuid NOT NULL REFERENCES club_canal (id) ON DELETE CASCADE,
                -- Ms_Users account of the athlete (no FK: another database).
                usuario_id uuid NOT NULL,
                nombre varchar(100) NOT NULL CHECK (length(btrim(nombre)) >= 1),
                agregado_en timestamptz NOT NULL DEFAULT now(),
                silenciado boolean NOT NULL DEFAULT false,
                leido_id bigint NOT NULL DEFAULT 0 CHECK (leido_id >= 0),
                PRIMARY KEY (canal_id, usuario_id)
            );
            -- The athlete's list of channels.
            CREATE INDEX IF NOT EXISTS club_canal_miembro_usuario_idx
                ON club_canal_miembro (usuario_id);

            CREATE TABLE IF NOT EXISTS club_canal_mensaje (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                canal_id uuid NOT NULL REFERENCES club_canal (id) ON DELETE CASCADE,
                autor_id uuid NOT NULL,
                de varchar(10) NOT NULL CHECK (de IN ('deportista', 'club')),
                -- The member's name when de = 'deportista'; the club's is read from club.
                autor_nombre varchar(100),
                texto varchar(1000) NOT NULL CHECK (length(btrim(texto)) >= 1),
                client_message_id uuid NOT NULL,
                enviado_en timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT club_canal_mensaje_cliente_uq UNIQUE (canal_id, autor_id, client_message_id)
            );
            CREATE INDEX IF NOT EXISTS club_canal_mensaje_canal_idx
                ON club_canal_mensaje (canal_id, id);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan los canales del club o el borrado en cascada de consultas: ejecuta 010_consultas_postulantes_canales.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT DELETE ON consulta_hilo, consulta_mensaje TO sportmatch_clubs;
            GRANT SELECT, INSERT, UPDATE, DELETE
                ON club_canal, club_canal_miembro, club_canal_mensaje TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.consulta_hilo', 'DELETE')
            AND has_table_privilege('sportmatch_clubs', 'public.consulta_mensaje', 'DELETE')
            -- has_table_privilege with a list is true when ANY is held: one per privilege.
            AND NOT EXISTS (
                SELECT 1
                FROM unnest(ARRAY['public.club_canal', 'public.club_canal_miembro',
                                  'public.club_canal_mensaje']) AS t (tabla)
                CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) AS p (privilegio)
                WHERE NOT has_table_privilege('sportmatch_clubs', t.tabla, p.privilegio)
            )
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre consultas o canales para sportmatch_clubs: ejecuta 010_consultas_postulantes_canales.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
