# PostgreSQL database for the clubs service

`ms-club` usa la base `sportmatch_clubs` creada en pgAdmin. Las tablas
pertenecen a `postgres`; el servicio se conecta con el rol `sportmatch_clubs`.

- `club`: columnas originales `id`, `usuario_id` (cuenta que creó el club, no se
  expone; desde la 007 es solo un dato histórico), `nombre`, `descripcion`,
  `email_contacto`, `telefono`, `direccion`, `latitud`, `longitud`, `logo`,
  `is_active`, `fecha_creacion`.
  La migración 001 agrega `historia`, `comuna`, `provincia`, `sitio_web`,
  `redes_sociales` (jsonb), `portada`, `estado_verificacion` (`pendiente` por
  defecto) y `fecha_actualizacion`, más el índice `club_usuario_idx`.
  La 007 agrega `rut` (`varchar(12)`, formato `76123456-0`, `CHECK` de formato e
  índice único `club_rut_uq`; los clubes anteriores pueden quedar sin RUT).
- `club_administrador` (migración 007): quién gestiona cada club. PK
  `(club_id, usuario_id)`; `usuario_id` único (una cuenta administra un solo club);
  `rol` `PRINCIPAL` (quien lo creó; uno por club, índice único parcial) o `ADMIN`;
  `nombre` (copiado de Ms_Users al entrar, o `Administrador del club`),
  `agregado_por` y `fecha_alta`. La 007 la llena con los `club.usuario_id`
  actuales como `PRINCIPAL` (si una cuenta tenía varios clubes, queda el más antiguo).
- `club_admin_asignacion` (migración 009): correos que el `PRINCIPAL` asignó como
  administradores. `email` en minúsculas y sin espacios (`CHECK`, hasta 254),
  `asignada_por`, `fecha_creacion`, `aceptada_por`/`aceptada_en` (al activarse en
  `GET /clubs/me`) y `revocada_en`. Índice único parcial `(club_id, email)` mientras está
  pendiente e índice por `email` de las pendientes. Nunca se borran filas (se borran solo
  con su club, `ON DELETE CASCADE`). Reemplaza a `club_invitacion_admin` (códigos de
  invitación de la 007), que la 009 elimina.
- `consulta_hilo` (migración 008): una conversación por club y deportista
  (`UNIQUE (club_id, deportista_id)`), con `deportista_nombre` (copia de Ms_Users al
  crearla) y el último mensaje leído por cada lado (`leido_deportista_id`,
  `leido_club_id`).
- `consulta_mensaje` (008): `id` bigint identity (sirve para paginar), `hilo_id`,
  `autor_id`, `de` (`deportista` o `club`), `texto` (1–1000), `client_message_id`
  (UUID de la app; `UNIQUE (hilo_id, autor_id, client_message_id)` hace idempotente
  el reintento) y `enviado_en`. No se editan. Desde la 010, si una postulación pasa a
  `RECHAZADO` y la persona no tiene otra activa en el club, su hilo se borra y sus
  mensajes caen con él (`ON DELETE CASCADE`).
- `club_canal` (migración 010): canales de anuncios del club (`nombre` 1–60,
  `miembros_escriben`, `leido_club_id`, `ultima_actividad`, `aviso_inactividad_en`,
  `mantener_hasta`); se borran con su club. Máximo 20 por club (lo controla el servicio).
- `club_canal_miembro` (010): `(canal_id, usuario_id)`, `nombre` (copia del perfil de su
  postulación), `agregado_en`, `silenciado`, `leido_id`. Máximo 500 por canal.
- `club_canal_mensaje` (010): como `consulta_mensaje`, con `canal_id` y `autor_nombre`
  (solo de los miembros; el del club se lee de `club`).
- `deporte`: catálogo (`id`, `nombre`, `descripcion`, `is_active`). El servicio solo lo lee.
- `club_deporte`: deportes de cada club; el representante los agrega y quita.
- `convocatoria` (migración 002): pruebas que publica un club (`club_id` → `club`,
  `deporte_id` → `deporte`). Guarda `cerrada_en` cuando el club la cierra; el
  estado (`ABIERTA`, `CERRADA`, `FINALIZADA`) se calcula al leer con `now()`.
  `requisitos` es `varchar(200)[]`. La tabla repite como `CHECK` los límites del
  DTO (cupos 1–1000, edades 4–99, 1 a 15 requisitos).
- `postulacion_convocatoria` (migración 003): postulaciones de deportistas
  (`convocatoria_id` → `convocatoria`; `usuario_id` es la cuenta de Ms_Users, sin FK
  por ser otra base). `UNIQUE (convocatoria_id, usuario_id)`. `estado` en
  `PENDIENTE`, `PRESELECCIONADO`, `ACEPTADO`, `RECHAZADO` (la 004 reemplazó
  `EN_REVISION` por `PRESELECCIONADO`). `antecedentes` (jsonb, arreglo),
  `perfil_deportivo` (jsonb, copia de la tarjeta de Ms_Users al postular) y
  `comentario_club` (004, retroalimentación del club).
- `comunicado_club` (migración 005): comunicados de un club (`club_id` → `club`,
  `titulo`, `contenido`, `audiencia` en `TODOS`/`POSTULANTES`/`MIEMBROS`, `prioridad`
  en `NORMAL`/`URGENTE`, `fecha_creacion`).
- `comunicado_destinatario` (migración 005): los deportistas que recibieron cada
  comunicado (`usuario_id` es la cuenta de Ms_Users, sin FK). Se llena al publicar y
  alimenta la bandeja del deportista.
- `plan_suscripcion` (migración 006): catálogo de planes (`tipo` `USUARIO` o `CLUB`,
  `precio` en pesos enteros, `duracion_dias`). El servicio solo lo lee; los planes y
  precios reales los define SportMatch (`seeds/demo_planes.sql` trae dos de PRUEBA).
- `transaccion_pago` (006): un intento de pago, desde que se crea la transacción en
  Webpay (`PENDING`) hasta `APPROVED`, `REJECTED` o `CANCELLED`. Comprador: `usuario_id`
  (cuenta de Ms_Users, sin FK) o `club_id`, exactamente uno. `iniciada_por` es la cuenta
  que puede confirmarla. `buy_order` y `token_ws` son únicos. De la tarjeta solo se
  guardan los últimos 4 dígitos.
- `suscripcion` (006): una fila por deportista o club (índices únicos parciales); renovar
  la extiende. No guarda estado: `ACTIVE`/`EXPIRED` se calcula con `fecha_fin`.
- `clubs_api.notification_outbox` (migración 004): eventos para el worker de
  notificaciones de Ms_gateway, con el mismo contrato que `matching_api` y
  `activities_api` (`id`, `user_id`, `kind`, `title`, `body`, `data`,
  `created_at`, `published_at`). Esquema propio para que el gateway lo
  identifique como origen `clubs_api`.
- `interesado_club`: existe en la base, pero el servicio no la usa.

## Permisos del rol `sportmatch_clubs`

| Tabla | Permisos | Otorgados por |
| --- | --- | --- |
| `club` | SELECT, INSERT, UPDATE | 00 (SELECT) y 001 |
| `club_deporte` | SELECT, INSERT, DELETE | 00 (SELECT) y 001 |
| `deporte` | SELECT | 00 |
| `convocatoria` | SELECT, INSERT, UPDATE | 002 (UPDATE también permite el `FOR UPDATE` al postular) |
| `postulacion_convocatoria` | SELECT, INSERT, UPDATE | 003 |
| `comunicado_club`, `comunicado_destinatario` | SELECT, INSERT (no se editan ni borran) | 005 |
| `plan_suscripcion` | SELECT | 006 |
| `transaccion_pago`, `suscripcion` | SELECT, INSERT, UPDATE (nunca se borran) | 006 |
| `clubs_api.notification_outbox` | USAGE del esquema; SELECT, INSERT, UPDATE, DELETE | 004 (el servicio inserta; el worker del gateway, con este mismo rol, lee, marca y purga) |
| `club_administrador` | SELECT, INSERT, DELETE (quitar o salir) | 007 |
| `club_admin_asignacion` | SELECT, INSERT, UPDATE (marcar aceptada o revocada; nunca se borra) | 009 |
| `consulta_hilo` | SELECT, INSERT, UPDATE (marcas de leído), DELETE (rechazo) | 008, 010 |
| `consulta_mensaje` | SELECT, INSERT, DELETE (en cascada con su hilo; la columna identity no requiere permisos sobre secuencias) | 008, 010 |
| `club_canal`, `club_canal_miembro`, `club_canal_mensaje` | SELECT, INSERT, UPDATE, DELETE | 010 |

No tiene DELETE sobre `club` ni acceso a `interesado_club`.

## Pasos

En pgAdmin, conectado a `sportmatch_clubs` como `postgres`, una vez y en orden:

1. `db/00_create_database.sql`: reemplazar el password por `DB_PASSWORD` del
   `.env`. Crea el rol con permiso de conexión y lectura.
2. `db/migrations/001_club_profile_admin.sql`: agrega las columnas y los permisos
   de escritura. Es idempotente. Si se ejecuta con un rol que no es dueño de
   `club` y aún faltan columnas, se detiene con un error claro sin cambiar nada.
   Si el rol `sportmatch_clubs` todavía no existe, avisa y hay que volver a
   ejecutarla después del paso 1.
3. `db/migrations/002_convocatorias.sql`: crea `convocatoria` y da permisos al
   rol. Mismas garantías que la 001.
4. `db/migrations/003_postulaciones.sql`: crea `postulacion_convocatoria` y da
   permisos al rol. Mismas garantías. Sin ella fallan también las rutas de
   convocatorias, porque informan `cupos_disponibles`.
5. `db/migrations/004_revision_postulaciones.sql`: estados de la HU-34,
   `comentario_club` y el outbox de notificaciones. Mismas garantías; requiere
   la 003. Sin ella fallan las rutas de postulaciones (leen `comentario_club`).
6. `db/migrations/005_comunicados.sql`: crea las dos tablas de comunicados y da
   permisos al rol. Mismas garantías; requiere la 004 (usa su outbox).
7. `db/migrations/006_pagos.sql`: planes, transacciones de pago y suscripciones. Mismas
   garantías. Después, solo en desarrollo, `db/seeds/demo_planes.sql` (como `postgres`)
   para tener dos planes de prueba.
8. `db/migrations/007_club_rut_administradores.sql`: `club.rut` y `club_administrador`
   (con los dueños actuales como `PRINCIPAL`). Mismas garantías. Ya no crea
   `club_invitacion_admin`. Sin ella fallan todas las rutas de administración (la
   autorización lee `club_administrador`).
9. `db/migrations/008_consultas_club.sql`: `consulta_hilo` y `consulta_mensaje`. Mismas
   garantías; requiere la 007 y el outbox de la 004.
10. `db/migrations/009_admin_por_correo.sql`: crea `club_admin_asignacion` y elimina
   `club_invitacion_admin` (las invitaciones pendientes se pierden: hay que asignar los
   correos). Mismas garantías; requiere la 007.
11. `db/migrations/010_consultas_postulantes_canales.sql`: `ON DELETE CASCADE` de
   `consulta_mensaje` a su hilo, DELETE sobre las consultas y las tablas de canales
   (`club_canal`, `club_canal_miembro`, `club_canal_mensaje`). Mismas garantías;
   requiere la 008.

`python -m app.database.migrate` aplica las migraciones con el usuario de `DB_*`;
con el rol del servicio solo sirve para comprobar que ya están aplicadas.
