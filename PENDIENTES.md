# ms-club — pendientes

Lo que falta para cerrar la HU-30 (directorio y perfiles de clubes), la HU-31
(administrar perfil de club) e integrar el servicio. Lo ya implementado está en
el [README](README.md).

## 1. Puesta en marcha local

- [ ] En pgAdmin, conectado a `sportmatch_clubs` como `postgres`, ejecutar
      `db/00_create_database.sql` con el `DB_PASSWORD` de `.env`.
- [ ] En el mismo lugar, ejecutar `db/migrations/001_club_profile_admin.sql`
      (columnas del perfil y permisos de escritura).
- [ ] Arrancar con `uvicorn app.main:app --env-file .env --port 8004` y confirmar
      `Connected to PostgreSQL ...` en el log.
- [ ] Probar los endpoints con los datos reales y con un token real de Ms_Users
      con rol `club_admin`. Hasta ahora se validó con tests y con una copia de la
      estructura de las tablas, sin datos reales.

## 2. Datos del perfil que aún no existen

| Falta | Uso | Hoy |
| --- | --- | --- |
| `slug` en `club` | Perfil por URL legible (`/clubs/club-deportivo-nunoa`) | Solo por UUID |
| `region` en `club` | Filtro por región | `zona` busca en dirección, comuna y provincia |
| Tabla de fotos (galería) | Galería del perfil (HU-30) | No existe |

Cuando existan: agregar los campos a `StoredClub`, al repositorio PostgreSQL, al
mock, a los DTOs y a los tests.

## 3. Administración del club (HU-31)

- [ ] Subida de logo y portada: hoy se guardan URLs `https://`. Falta definir
      dónde se alojan los archivos (bucket/CDN) y quién los sube.
- [ ] Verificación de clubes: `estado_verificacion` existe (`pendiente` por
      defecto) pero no hay endpoint para que el equipo de SportMatch lo cambie.
      Ese endpoint sería para el rol `admin`, que no puede editar perfiles de club.
- [ ] Activar/desactivar el club (`is_active`): no hay endpoint; hoy solo se
      cambia en la base.
- [ ] El rol se lee del JWT. Si Ms_Users cambia el rol de una cuenta, el cambio
      rige cuando el token vence (30 min). `Ms_activities` consulta a Ms_Users en
      cada petición; evaluar si ms-club debe hacer lo mismo.
- [ ] Las cuentas `club_admin` se crean hoy a mano en Ms_Users: falta el flujo de
      registro/alta de representantes.
- [ ] `club.usuario_id` no tiene restricción única en la base; la regla de un club
      por representante la aplica el servicio. Si se quiere en la base, crear un
      índice único como `postgres` (antes, revisar que no haya duplicados).

## 4. Convocatorias abiertas del club

- [ ] El perfil devuelve `convocatorias_abiertas: []` (`NoConvocatorias`).
- [ ] Definir qué servicio es dueño de las convocatorias. `Ms_activities` hoy no
      relaciona actividades con clubes (no tiene `club_id`).
- [ ] Ese servicio debe exponer las convocatorias abiertas por club. Luego hay que
      implementar un cliente HTTP con el contrato `ConvocatoriasProvider`
      (`app/services/convocatorias.py`) y pasarlo en `create_app()`.

## 5. Filtro por deporte

- [x] `GET /api/v1/clubs/deportes` lista el catálogo y se puede filtrar por `deporte_id`.
- [ ] La app usa códigos (`futbol`, `tennis`) en Ms_Users y la tabla `deporte` usa
      nombres (`Fútbol`, `Tenis`): `tennis` no encuentra `Tenis`. La app debería
      usar el catálogo de ms-club para los filtros de clubes.

## 6. Integración con el resto del backend

- [ ] `Ms_gateway`: ruta proxy hacia `/api/v1/clubs` y `CLUBS_SERVICE_URL`,
      reenviando el header `Authorization`.
- [ ] `Ms_gateway/compose.yaml`: servicio `ms_club` (puerto 8004) con las variables
      `DB_*` (`DB_HOST=host.docker.internal` desde Docker) y `JWT_*`.
- [ ] Autenticación en el gateway: las rutas GET del directorio son públicas (la
      HU-30 incluye visitantes). Si el gateway exige token (`AUTH_REQUIRED=true`),
      marcarlas como públicas allí.
- [ ] Colección de Postman, como la de `Ms_Users/postman`.

## 7. Fuera de la HU-30/31 (para siguientes historias)

- `interesado_club`: existe en la base y el servicio no la usa.
- `membresia` y `pago`: según `Ms_Users/db/README.md`, pertenecen a este servicio y
  aún no existen.
