-- DEMO plans for local development and the Webpay sandbox. Not applied by
-- app.database.migrate and NOT the real catalog: names and prices below are
-- placeholders. SportMatch must define the real plans before going live.
--
-- Run as postgres (the service role cannot insert plans), after 006.
-- Idempotent: running it twice leaves the same rows.
INSERT INTO plan_suscripcion (nombre, tipo, descripcion, precio, duracion_dias) VALUES
    ('Demo Deportista Mensual', 'USUARIO', 'Plan de prueba para deportistas (30 días).', 3990, 30),
    ('Demo Club Mensual', 'CLUB', 'Plan de prueba para clubes (30 días).', 14990, 30)
ON CONFLICT (tipo, nombre) DO NOTHING;
