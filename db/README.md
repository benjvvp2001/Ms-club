# PostgreSQL database for the clubs service

`ms-club` usa la base `sportmatch_clubs` creada en pgAdmin. Las tablas
pertenecen a `postgres`; el servicio se conecta con el rol `sportmatch_clubs`.

- `club`: columnas originales `id`, `usuario_id` (cuenta del representante, no se
  expone), `nombre`, `descripcion`, `email_contacto`, `telefono`, `direccion`,
  `latitud`, `longitud`, `logo`, `is_active`, `fecha_creacion`.
  La migración 001 agrega `historia`, `comuna`, `provincia`, `sitio_web`,
  `redes_sociales` (jsonb), `portada`, `estado_verificacion` (`pendiente` por
  defecto) y `fecha_actualizacion`, más el índice `club_usuario_idx`.
- `deporte`: catálogo (`id`, `nombre`, `descripcion`, `is_active`). El servicio solo lo lee.
- `club_deporte`: deportes de cada club; el representante los agrega y quita.
- `interesado_club`: existe en la base, pero el servicio no la usa.

## Permisos del rol `sportmatch_clubs`

| Tabla | Permisos | Otorgados por |
| --- | --- | --- |
| `club` | SELECT, INSERT, UPDATE | 00 (SELECT) y 001 |
| `club_deporte` | SELECT, INSERT, DELETE | 00 (SELECT) y 001 |
| `deporte` | SELECT | 00 |

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

`python -m app.database.migrate` aplica las migraciones con el usuario de `DB_*`;
con el rol del servicio solo sirve para comprobar que ya están aplicadas.
