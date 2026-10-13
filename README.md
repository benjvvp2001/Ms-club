# ms-club — servicio de clubes de SportMatch

Directorio y perfiles públicos de clubes deportivos (HU-30) y administración del
perfil por el representante del club (HU-31). Misma arquitectura que `Ms_Users`:
FastAPI + Pydantic v2, PostgreSQL con psycopg 3 (pool, sin ORM), capas
`api → services → repositories`, JWT emitido por `Ms_Users`.

Trabaja sobre las tablas existentes de la base `sportmatch_clubs` (`club`,
`deporte`, `club_deporte`). La migración `001_club_profile_admin.sql` agrega a
`club` los campos del perfil que edita el representante.

```
app/
  main.py                 create_app(): pool PostgreSQL en el arranque, o repositorio inyectado (tests)
  api/v1/clubs.py         rutas /api/v1/clubs
  api/dependencies.py     inyección de repositorio y servicio; usuario autenticado (Bearer)
  core/config.py          DB_* y JWT_*
  core/security.py        validación del JWT de Ms_Users y roles (club_admin, admin)
  database/               comprobación de conexión al arranque, pool y migrador
  repositories/           contrato ClubRepository + PostgreSQL + mock en memoria
  schemas/club.py         DTOs: filtros, ClubCreate, ClubUpdate y respuestas
  services/               ClubService (reglas y permisos) y el contrato ConvocatoriasProvider
db/
  00_create_database.sql  rol sportmatch_clubs (una vez, en pgAdmin)
  migrations/             001: campos del perfil y permisos de escritura (como postgres)
tests/                    pruebas de la API con el repositorio mock
```

## Endpoints

| Método | Ruta | Acceso | Descripción |
| --- | --- | --- | --- |
| GET | `/api/v1/clubs` | Público | Directorio paginado de clubes activos. |
| GET | `/api/v1/clubs/deportes` | Público | Catálogo de deportes activos (para filtros y para asociar). |
| GET | `/api/v1/clubs/{id}` | Público | Perfil público del club. |
| POST | `/api/v1/clubs` | `club_admin` | Crea el club del representante (uno por cuenta). |
| GET | `/api/v1/clubs/me` | `club_admin` | Panel de edición: el club del representante, activo o no. |
| PATCH | `/api/v1/clubs/{id}` | `club_admin` dueño | Edita el perfil y los deportes ofrecidos. |
| PUT | `/api/v1/clubs/{id}/deportes/{deporte_id}` | `club_admin` dueño | Asocia un deporte (idempotente). |
| DELETE | `/api/v1/clubs/{id}/deportes/{deporte_id}` | `club_admin` dueño | Quita un deporte (idempotente). |
| GET | `/api/v1/clubs/health/ready` | Público | Health check (consulta la base). |

### Directorio (HU-30)

Filtros de `GET /api/v1/clubs`, todos opcionales y combinables:

- `nombre`: búsqueda parcial sin distinguir mayúsculas (mín. 2 caracteres). `%` y `_` se buscan literalmente.
- `deporte`: nombre del deporte, sin distinguir mayúsculas ni tildes (`futbol` encuentra `Fútbol`).
- `deporte_id`: id del deporte (ver `/api/v1/clubs/deportes`).
- `zona`: búsqueda parcial en dirección, comuna y provincia.
- `page` (desde 1) y `page_size` (1–50, por defecto 20).

Solo se muestran clubes y deportes con `is_active`. Cada tarjeta trae `id`,
`nombre`, `descripcion_corta`, `logo`, `direccion`, `comuna`, `verificado` y
`deportes`. El perfil agrega `descripcion`, `historia`, `portada`, `ubicacion`
(dirección, comuna, provincia, coordenadas), `email_contacto`, `telefono`,
`sitio_web`, `redes_sociales` y `convocatorias_abiertas`. Nunca expone
`club.usuario_id`.

### Administración del perfil (HU-31)

Requiere `Authorization: Bearer <access token de Ms_Users>`. El servicio valida
el token igual que Ms_Users (HS256, `iss`, `aud`, `token_type=access`) y lee el
rol del claim `roles`:

- Todas las rutas de administración usan la dependencia `get_club_admin`
  (`app/api/dependencies.py`): sin token → 401; token sin rol `club_admin` → 403.
- Además, `ClubService` comprueba que el club pertenezca a la cuenta del token
  (`club.usuario_id`); si no, 403 `No administras este club.`. El panel es
  exclusivo del representante dueño: ningún otro rol, ni `admin`, edita clubes.
- Un representante administra un único club (409 si intenta crear otro).

`ClubCreate` (POST) y `ClubUpdate` (PATCH) aceptan: `nombre`, `descripcion`
(500), `historia` (3000), `direccion`, `comuna`, `provincia`, `latitud` y
`longitud` (juntas), `telefono`, `email_contacto`, `sitio_web` (http/https),
`logo` y `portada` (URL https) y `redes_sociales` (`instagram`, `facebook`, `x`,
`tiktok`, `youtube`, URLs https) y `deporte_ids` (deportes ofrecidos, ids de
`/api/v1/clubs/deportes`, hasta 30, sin repetir). Cualquier otro campo se
rechaza, incluidos `usuario_id` y `estado_verificacion`, que el representante no
puede cambiar.

En PATCH solo cambian los campos enviados; `null` borra un campo opcional (no
`nombre`). `redes_sociales` y `deporte_ids` se reemplazan completos (`[]` quita
todos los deportes). Perfil y deportes se guardan en una sola transacción; un
deporte inexistente o inactivo responde 400 sin guardar nada. Logo y portada se guardan
como URL: la subida de archivos no es parte de este servicio.

Respuesta de POST, PATCH y `/me`: el perfil más `is_active`,
`estado_verificacion` (`pendiente`, `verificado`, `rechazado`; lo fija el equipo
de SportMatch), `fecha_creacion` y `fecha_actualizacion`.

### Errores

- `400` datos o parámetros inválidos (campos desconocidos, formatos, largos,
  coordenadas incompletas, id que no es UUID). A diferencia de Ms_Users, que
  responde 422, aquí toda validación de entrada responde 400 con el mismo `detail`.
- `401` sin token o con token inválido/expirado.
- `403` el rol o la cuenta no administra ese club.
- `404` club inexistente (o inactivo, en las rutas públicas) o deporte inexistente/inactivo.
- `409` el representante ya administra un club.

## Convocatorias

Las convocatorias no se guardan en este servicio. El perfil las pide a un
`ConvocatoriasProvider` (`app/services/convocatorias.py`) por id de club. Por
ahora se usa `NoConvocatorias`, que devuelve una lista vacía; cuando el servicio
dueño exponga convocatorias por club, se implementa un cliente HTTP con ese
contrato y se pasa a `create_app(convocatorias=...)`. Si ese servicio falla, el
perfil igual responde, con `convocatorias_abiertas: []`.

## Base de datos y ejecución

Variables en `.env` (ver `.env.example`): `DB_HOST`, `DB_PORT`, `DB_USER`,
`DB_PASSWORD`, `DB_NAME` (por defecto `sportmatch_clubs`) y `JWT_SECRET`,
`JWT_ISSUER`, `JWT_AUDIENCE`, que deben ser los mismos de Ms_Users. Igual que
Ms_Users, el `.env` se carga con `--env-file` de uvicorn.

Al arrancar, el servicio valida la configuración JWT, se conecta una vez a
PostgreSQL, comprueba que la base sea `DB_NAME` y registra `Connected to
PostgreSQL usuario@host:puerto/base`. Si algo falla, no arranca y muestra la
causa. `GET /api/v1/clubs/health/ready` repite la comprobación en caliente.

Una vez, en pgAdmin conectado a `sportmatch_clubs` como `postgres`, en este orden:

1. `db/00_create_database.sql` (reemplazar el password por `DB_PASSWORD` del `.env`).
2. `db/migrations/001_club_profile_admin.sql`.

```bash
pip install -e ".[dev]"
uvicorn app.main:app --env-file .env --port 8004 --reload
python -m pytest
```

Puerto 8004 (users 8001, matching 8002, activities 8003). Aún no está
registrado en el gateway ni en `Ms_gateway/compose.yaml`.

Lo que falta por hacer está en [PENDIENTES.md](PENDIENTES.md).
