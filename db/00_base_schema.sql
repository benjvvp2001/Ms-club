-- Tablas base de la base sportmatch_clubs, para entornos NUEVOS (Docker local, pruebas).
--
-- En el servidor del equipo estas tablas ya existen: se crearon a mano en pgAdmin y
-- pertenecen a postgres. Este archivo las reconstruye a partir de lo documentado en
-- DUDAS_TABLAS_E_INFORMACION.md (sección 0) y de cómo las usan las migraciones 001-006.
-- Si el modelo original tiene otros tipos, largos o restricciones, hay que alinear este
-- archivo con él (ver docs/sprint-clubes-chat/INFORME.md).
--
-- Ejecutar como postgres, antes de 00_create_database.sql y de db/migrations. Es idempotente.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS deporte (
    id serial PRIMARY KEY,
    nombre varchar(50) NOT NULL UNIQUE,
    descripcion varchar(255),
    is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS club (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Cuenta de Ms_Users que creó el club (otra base: sin FK).
    usuario_id uuid NOT NULL,
    nombre varchar(100) NOT NULL,
    descripcion varchar(500),
    email_contacto varchar(100),
    telefono varchar(20),
    direccion varchar(255),
    latitud double precision CHECK (latitud BETWEEN -90 AND 90),
    longitud double precision CHECK (longitud BETWEEN -180 AND 180),
    logo varchar(255),
    is_active boolean NOT NULL DEFAULT true,
    fecha_creacion timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS club_deporte (
    club_id uuid NOT NULL REFERENCES club (id),
    deporte_id integer NOT NULL REFERENCES deporte (id),
    PRIMARY KEY (club_id, deporte_id)
);

-- Existe en la base original pero ningún servicio la usa todavía (DUDAS 2.4).
CREATE TABLE IF NOT EXISTS interesado_club (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id uuid NOT NULL REFERENCES club (id),
    usuario_id uuid NOT NULL,
    fecha_creacion timestamptz NOT NULL DEFAULT now(),
    UNIQUE (club_id, usuario_id)
);

-- Catálogo inicial, con los deportes que ofrece la app.
INSERT INTO deporte (nombre, descripcion) VALUES
    ('Fútbol', NULL), ('Tenis', NULL), ('Running', NULL), ('Ciclismo', NULL),
    ('Natación', NULL), ('Básquetbol', NULL), ('Vóleibol', NULL), ('Atletismo', NULL),
    ('Yoga', NULL), ('Trekking', NULL)
ON CONFLICT (nombre) DO NOTHING;

COMMIT;
