-- Club como entidad con RUT y varios administradores (sprint clubes y chats, sección A).
--
-- club.rut: RUT de la persona jurídica, formato 76123456-0 (sin puntos, DV en
--   mayúscula). Único; los clubes anteriores pueden quedar sin RUT. El servicio
--   valida el dígito verificador y que el cuerpo sea >= 50.000.000.
-- club_administrador: quién gestiona cada club. PRINCIPAL es quien lo creó (uno
--   por club); ADMIN, quien entró con una invitación. Una cuenta administra un
--   solo club (usuario_id único). club.usuario_id queda solo como dato histórico.
--   Se llena con los dueños actuales (club.usuario_id) como PRINCIPAL.
-- club_invitacion_admin: códigos de un solo uso para sumar administradores.
--   Solo se guarda el hash SHA-256 del código; vence a las 72 h.
--
-- Run as postgres (pgAdmin Query Tool connected to sportmatch_clubs), after
-- 006. Running it again, or as the service role once applied, is a no-op.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('sportmatch-clubs-schema', 0));

DO $$
BEGIN
    -- ALTER and CREATE need ownership even when everything already exists.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.club'::regclass AND attname = 'rut' AND NOT attisdropped
    )
       OR to_regclass('public.club_rut_uq') IS NULL
       OR to_regclass('public.club_administrador') IS NULL
       OR to_regclass('public.club_invitacion_admin') IS NULL THEN
        BEGIN
            ALTER TABLE club ADD COLUMN IF NOT EXISTS rut varchar(12);
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'public.club'::regclass AND conname = 'club_rut_formato_check'
            ) THEN
                ALTER TABLE club ADD CONSTRAINT club_rut_formato_check
                    CHECK (rut ~ '^[0-9]{7,8}-[0-9K]$');
            END IF;
            -- Several NULLs are allowed: old clubs without RUT.
            CREATE UNIQUE INDEX IF NOT EXISTS club_rut_uq ON club (rut);

            CREATE TABLE IF NOT EXISTS club_administrador (
                club_id uuid NOT NULL REFERENCES club (id),
                -- Ms_Users account (no FK: another database).
                usuario_id uuid NOT NULL,
                rol varchar(10) NOT NULL DEFAULT 'ADMIN' CHECK (rol IN ('PRINCIPAL', 'ADMIN')),
                -- Name shown in the club's admin list, copied from Ms_Users when joining.
                nombre varchar(100) NOT NULL CHECK (length(btrim(nombre)) >= 1),
                -- Who invited them; NULL for the PRINCIPAL.
                agregado_por uuid,
                fecha_alta timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (club_id, usuario_id),
                CONSTRAINT club_administrador_usuario_uq UNIQUE (usuario_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS club_administrador_principal_uq
                ON club_administrador (club_id) WHERE rol = 'PRINCIPAL';

            -- The current owners become PRINCIPAL. If an account owned several
            -- clubs, only the oldest is kept (one club per account).
            INSERT INTO club_administrador (club_id, usuario_id, rol, nombre, agregado_por, fecha_alta)
            SELECT DISTINCT ON (c.usuario_id)
                   c.id, c.usuario_id, 'PRINCIPAL', 'Administrador del club', NULL,
                   COALESCE(c.fecha_creacion, now())
            FROM club c
            WHERE c.usuario_id IS NOT NULL
            ORDER BY c.usuario_id, c.fecha_creacion, c.id
            ON CONFLICT DO NOTHING;

            CREATE TABLE IF NOT EXISTS club_invitacion_admin (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                club_id uuid NOT NULL REFERENCES club (id),
                -- SHA-256 (hex) of the code; the code itself is shown only once.
                codigo_hash char(64) NOT NULL,
                creada_por uuid NOT NULL,
                fecha_creacion timestamptz NOT NULL DEFAULT now(),
                expira_en timestamptz NOT NULL,
                usada_por uuid,
                usada_en timestamptz,
                revocada_en timestamptz,
                CONSTRAINT club_invitacion_admin_codigo_uq UNIQUE (codigo_hash),
                CHECK (expira_en > fecha_creacion),
                CHECK ((usada_por IS NULL) = (usada_en IS NULL))
            );
            -- Pending invitations of a club (list and the maximum of admins).
            CREATE INDEX IF NOT EXISTS club_invitacion_admin_vigente_idx
                ON club_invitacion_admin (club_id, expira_en)
                WHERE usada_en IS NULL AND revocada_en IS NULL;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Faltan club.rut o las tablas de administradores: ejecuta 007_club_rut_administradores.sql como postgres';
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') THEN
        -- Admins are added and removed (DELETE); invitations are only marked as
        -- used or revoked (UPDATE), never deleted.
        -- A GRANT by someone who is not the owner may only warn, so check afterwards.
        BEGIN
            GRANT SELECT, INSERT, DELETE ON club_administrador TO sportmatch_clubs;
            GRANT SELECT, INSERT, UPDATE ON club_invitacion_admin TO sportmatch_clubs;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        IF NOT (
            has_table_privilege('sportmatch_clubs', 'public.club_administrador', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_administrador', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_administrador', 'DELETE')
            AND has_table_privilege('sportmatch_clubs', 'public.club_invitacion_admin', 'SELECT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_invitacion_admin', 'INSERT')
            AND has_table_privilege('sportmatch_clubs', 'public.club_invitacion_admin', 'UPDATE')
        ) THEN
            RAISE EXCEPTION 'Faltan permisos sobre las tablas de administradores para sportmatch_clubs: ejecuta 007_club_rut_administradores.sql como postgres';
        END IF;
    ELSE
        RAISE WARNING 'El rol sportmatch_clubs no existe: ejecuta db/00_create_database.sql y luego vuelve a ejecutar esta migración';
    END IF;
END $$;

COMMIT;
