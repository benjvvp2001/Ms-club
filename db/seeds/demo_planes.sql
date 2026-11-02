-- DEMO plans for local development and the Webpay sandbox. Not applied by
-- app.database.migrate and NOT the real catalog: names and prices below are
-- placeholders. SportMatch must define the real plans before going live.
--
-- Run as postgres (the service role cannot insert plans), after 006.
-- Idempotent: running it twice leaves the same rows.
-- No usa ON CONFLICT (tipo, nombre): desde 012 el nombre es unico solo entre planes ACTIVOS, y un plan demo
-- que el equipo desactivo no debe volver a crearse cada vez que se reaplica este archivo.
INSERT INTO plan_suscripcion (nombre, tipo, descripcion, precio, duracion_dias)
SELECT v.nombre, v.tipo, v.descripcion, v.precio, v.duracion_dias
FROM (VALUES
    ('Demo Deportista Mensual', 'USUARIO', 'Plan de prueba para deportistas (30 días).', 3990, 30),
    ('Demo Club Mensual', 'CLUB', 'Plan de prueba para clubes (30 días).', 14990, 30)
) AS v (nombre, tipo, descripcion, precio, duracion_dias)
WHERE NOT EXISTS (
    SELECT 1 FROM plan_suscripcion p WHERE p.tipo = v.tipo AND p.nombre = v.nombre
);
