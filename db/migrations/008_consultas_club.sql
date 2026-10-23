-- HU-51: consultas deportista <-> club (sprint clubes y chats, sección B).
--
-- consulta_hilo: one conversation per (club, athlete). On the club side it is a
--   shared inbox: any administrator reads and answers. It keeps a snapshot of
--   the athlete's name (from Ms_Users, when the thread is created) and the last
--   message id each side has read, to count unread messages.
-- consulta_mensaje: the messages, numbered by an identity column so the app
--   pages with after_id / before_id. de says which side wrote it; autor_id is
--   the account (an administrator when de = 'club'). client_message_id makes a
--   retry return the same message instead of a duplicate.
--
-- Notifications go through clubs_api.notification_outbox (migration 004),
-- written by the service in the same transaction, never with the message text.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 007. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- CREATE TABLE needs CREATE on the schema even when the tables exist.
    IF to_regclass('public.consulta_hilo') IS NULL
       OR to_regclass('public.consulta_mensaje') IS NULL THEN
        BEGIN
            CREATE TABLE IF NOT EXISTS consulta_hilo (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                club_id uuid NOT NULL REFERENCES club (id),
                -- Ms_Users account of the athlete (no FK: another database).
                deportista_id uuid NOT NULL,
                deportista_nombre varchar(100) NOT NULL CHECK (length(btrim(deportista_nombre)) >= 1),
                -- Last message id read by each side (0: none).
                leido_deportista_id bigint NOT NULL DEFAULT 0 CHECK (leido_deportista_id >= 0),
                leido_club_id bigint NOT NULL DEFAULT 0 CHECK (leido_club_id >= 0),
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT consulta_hilo_club_deportista_uq UNIQUE (club_id, deportista_id)
            );
            -- The athlete's list of threads.
            CREATE INDEX IF NOT EXISTS consulta_hilo_deportista_idx
                ON consulta_hilo (deportista_id);

            CREATE TABLE IF NOT EXISTS consulta_mensaje (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                hilo_id uuid NOT NULL REFERENCES consulta_hilo (id),
                autor_id uuid NOT NULL,
                de varchar(10) NOT NULL CHECK (de IN ('deportista', 'club')),
                texto varchar(1000) NOT NULL CHECK (length(btrim(texto)) >= 1),
                client_message_id uuid NOT NULL,
                enviado_en timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT consulta_mensaje_cliente_uq UNIQUE (hilo_id, autor_id, client_message_id)
            );
            -- Paging inside a thread, last message and unread counts.
            CREATE INDEX IF NOT EXISTS consulta_mensaje_hilo_idx
                ON consulta_mensaje (hilo_id, id);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan las tablas de consultas: ejecuta 008_consultas_club.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- Threads are created and their read marks updated; messages are never
        -- edited or deleted. The identity column needs no sequence privilege.
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, UPDATE ON consulta_hilo TO sportmatch_clubs;
            GRANT SELECT, INSERT ON consulta_mensaje TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.consulta_hilo', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.consulta_hilo', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.consulta_hilo', 'UPDATE')
            AND has_table_privilege('sportmatch_clubs', 'public.consulta_mensaje', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.consulta_mensaje', 'INSERT')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre las tablas de consultas para sportmatch_clubs: ejecuta 008_consultas_club.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
