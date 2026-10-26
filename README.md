# ms-club — servicio de clubes de SportMatch

Directorio y perfiles públicos de clubes deportivos (HU-30), administración del
perfil por el representante del club (HU-31), convocatorias a pruebas de
jugadores (HU-32), postulación de deportistas a esas convocatorias (HU-33) y
revisión de candidatos por el club, con aviso al deportista (HU-34) y comunicados
de los clubes a sus postulantes y miembros (HU-38), pago de planes con Webpay Plus
(HU-40), club con RUT y varios administradores, y consultas entre deportistas y
clubes (HU-51). Misma arquitectura que `Ms_Users`:
FastAPI + Pydantic v2, PostgreSQL con psycopg 3 (pool, sin ORM), capas
`api → services → repositories`, JWT emitido por `Ms_Users`.

Trabaja sobre las tablas existentes de la base `sportmatch_clubs` (`club`,
`deporte`, `club_deporte`). La migración `001_club_profile_admin.sql` agrega a
`club` los campos del perfil que edita el representante y
`002_convocatorias.sql` crea la tabla `convocatoria` (un club, muchas convocatorias)
`003_postulaciones.sql` la tabla `postulacion_convocatoria` y
`004_revision_postulaciones.sql` los estados de revisión y el outbox de notificaciones y
`005_comunicados.sql` las tablas de comunicados, `006_pagos.sql` planes, pagos y
suscripciones, `007_club_rut_administradores.sql` el RUT del club y sus administradores,
`008_consultas_club.sql` las consultas deportista ↔ club, `009_admin_por_correo.sql`
las asignaciones de administradores por correo (reemplazan a las invitaciones) y
`010_consultas_postulantes_canales.sql` el borrado de consultas al rechazar y los canales
del club, y `011_chat_postulantes_convocatoria.sql` el interruptor del chat de cada
convocatoria.

```
app/
  main.py                 create_app(): pool PostgreSQL en el arranque, o repositorio inyectado (tests)
  api/v1/clubs.py         rutas de clubes (/api/v1/clubs)
  api/v1/convocatorias.py rutas de convocatorias (/api/v1/clubs/announcements, /{id}/announcements)
  api/v1/postulaciones.py rutas de postulaciones y candidatos (/announcements/{id}/applications,
                          /announcements/applications/me, /announcements/applications/{id})
  api/v1/pagos.py         pagos con Webpay Plus (/api/v1/payments)
  api/v1/administradores.py administradores y asignaciones por correo (/{id}/admins,
                          /{id}/admins/assignments)
  api/v1/consultas.py     consultas deportista ↔ club (/consultas/me, /{id}/consultas)
  api/v1/canales.py       canales del club (/canales/me, /{id}/canales)
  api/dependencies.py     inyección de repositorio y servicio; usuario autenticado (Bearer)
  core/config.py          DB_*, JWT_*, USERS_*, CLUB_MAX_ADMINS y WEBPAY_*
  core/security.py        validación del JWT de Ms_Users, roles (club_admin, admin) y correo
  database/               comprobación de conexión al arranque, pool y migrador
  repositories/           contrato ClubRepository + PostgreSQL + mock en memoria
  schemas/                common.py (compartido), club.py (ClubCreate, ClubUpdate...),
                          convocatoria.py (ConvocatoriaCreate, filtros, respuestas),
                          postulacion.py (PostulacionCreate, PostulacionEstadoUpdate, candidatos),
                          pago.py (PagoIniciar, PagoConfirmar, comprobante, suscripción),
                          administrador.py (asignaciones) y consulta.py (mensajes e hilos)
  services/               ClubService, ConvocatoriaService, PostulacionService,
                          PagoService, AdministradorService, ConsultaService,
                          authorization.py (administradores del club), rut.py (RUT y DV),
                          users_client.py (Ms_Users) y webpay.py (Transbank y modo simulado)
db/
  00_create_database.sql  rol sportmatch_clubs (una vez, en pgAdmin)
  migrations/             001: perfil; 002: convocatoria; 003: postulaciones; 004: revisión y outbox;
                          005: comunicados; 006: planes, pagos y suscripciones;
                          007: RUT y administradores; 008: consultas;
                          009: asignaciones de administradores por correo;
                          010: consultas solo con postulantes y canales del club
  seeds/demo_planes.sql   planes de PRUEBA para desarrollo (no son el catálogo real)
                          (todas como postgres)
tests/                    pruebas de la API con el repositorio mock
```

## Endpoints

| Método | Ruta | Acceso | Descripción |
| --- | --- | --- | --- |
| GET | `/api/v1/clubs` | Público | Directorio paginado de clubes activos. |
| GET | `/api/v1/clubs/deportes` | Público | Catálogo de deportes activos (para filtros y para asociar). |
| GET | `/api/v1/clubs/{id}` | Público | Perfil público del club. |
| POST | `/api/v1/clubs` | `club_admin` sin club | Crea el club con su RUT; quien lo crea queda como `PRINCIPAL`. |
| GET | `/api/v1/clubs/me` | `club_admin` | Panel de edición: el club que administro, activo o no, con `mi_rol`. Activa una asignación pendiente de mi correo. |
| PATCH | `/api/v1/clubs/{id}` | admin del club | Edita el perfil y los deportes ofrecidos. |
| PUT | `/api/v1/clubs/{id}/deportes/{deporte_id}` | admin del club | Asocia un deporte (idempotente). |
| DELETE | `/api/v1/clubs/{id}/deportes/{deporte_id}` | admin del club | Quita un deporte (idempotente). |
| GET | `/api/v1/clubs/{id}/admins` | admin del club | Administradores del club. |
| DELETE | `/api/v1/clubs/{id}/admins/{usuario_id}` | principal o el mismo admin | Quita a un administrador o sale del club. |
| GET | `/api/v1/clubs/{id}/admins/assignments` | admin del club | Correos asignados pendientes. |
| POST | `/api/v1/clubs/{id}/admins/assignments` | principal | Asigna un correo como administrador (queda pendiente). |
| DELETE | `/api/v1/clubs/{id}/admins/assignments/{asignacion_id}` | principal | Revoca una asignación pendiente. |
| GET | `/api/v1/clubs/consultas/me` | Deportista | Mis conversaciones con clubes. |
| GET, POST | `/api/v1/clubs/{id}/consultas/me/messages` | Deportista | Leer y escribir mi consulta a un club. |
| POST | `/api/v1/clubs/{id}/consultas/me/read` | Deportista | Marcar como leído. |
| GET | `/api/v1/clubs/{id}/consultas` | admin del club | Bandeja compartida de consultas del club. |
| GET, POST | `/api/v1/clubs/{id}/consultas/{hilo_id}/messages` | admin del club | Leer y responder una consulta. |
| POST | `/api/v1/clubs/{id}/consultas/{hilo_id}/read` | admin del club | Marcar como leído. |
| POST | `/api/v1/clubs/{id}/consultas/postulantes/{usuario_id}/messages` | admin del club | Escribirle primero a un postulante activo. |
| GET, POST | `/api/v1/clubs/{id}/canales` | admin del club | Canales del club; crear uno (entran los aceptados). |
| PATCH, DELETE | `/api/v1/clubs/{id}/canales/{canal_id}` | admin del club | Editar o eliminar un canal. |
| GET | `/api/v1/clubs/{id}/canales/{canal_id}/miembros`, `/candidatos` | admin del club | Miembros y postulantes que se pueden agregar. |
| PUT, DELETE | `/api/v1/clubs/{id}/canales/{canal_id}/miembros/{usuario_id}` | admin del club | Agregar o sacar a un postulante. |
| GET, POST | `/api/v1/clubs/{id}/canales/{canal_id}/messages`, POST `/read` | admin del club | Mensajes del canal como club. |
| POST | `/api/v1/clubs/{id}/canales/{canal_id}/inactividad` | admin del club | Responder al aviso de inactividad (eliminar o mantener). |
| GET | `/api/v1/clubs/canales/me` | Deportista | Mis canales. |
| GET, POST | `/api/v1/clubs/canales/{canal_id}/messages`, POST `/read`, PUT `/mute` | Deportista (miembro) | Leer, escribir (si el club lo permite), marcar leído y silenciar. |
| GET | `/api/v1/clubs/announcements` | Público | Sección de convocatorias (por defecto, abiertas). |
| GET | `/api/v1/clubs/announcements/{id}` | Público | Detalle de una convocatoria. |
| POST | `/api/v1/clubs/{id}/announcements` | admin del club | Publica una convocatoria del club. |
| POST | `/api/v1/clubs/{id}/announcements/{convocatoria_id}/close` | admin del club | Cierra una convocatoria antes de su fecha. |
| POST | `/api/v1/clubs/announcements/{convocatoria_id}/applications` | Deportista | Postula a una convocatoria abierta. |
| GET | `/api/v1/clubs/announcements/applications/me` | Deportista | Historial y estado de mis postulaciones. |
| GET | `/api/v1/clubs/announcements/{convocatoria_id}/applications` | admin del club | Candidatos de la convocatoria. |
| PATCH | `/api/v1/clubs/announcements/applications/{postulacion_id}` | admin del club | Cambia el estado de un candidato y avisa al deportista. |
| POST | `/api/v1/clubs/{id}/notices` | admin del club | Publica un comunicado y notifica a su audiencia. |
| GET | `/api/v1/clubs/{id}/notices` | admin del club | Comunicados que publicó el club, con a cuántos llegó cada uno. |
| GET | `/api/v1/clubs/notices/me` | Deportista | Mi bandeja de comunicados. |
| GET | `/api/v1/payments/plans` | Público | Planes disponibles. |
| POST | `/api/v1/payments/webpay/initiate` | Autenticado | Inicia el pago de un plan. |
| POST | `/api/v1/payments/webpay/commit` | Autenticado | Confirma el pago al volver de Webpay. |
| GET | `/api/v1/payments/subscription/me` | Autenticado | Mi suscripción (deportista o club). |
| GET, POST | `/api/v1/payments/webpay/return` | Navegador | Retorno de Webpay: reenvía a la app. |
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
- Además, el servicio comprueba que la cuenta del token sea administradora del
  club (`club_administrador`, rol `PRINCIPAL` o `ADMIN`); si no, 403 `No administras
  este club.`. Ningún otro rol, ni `admin`, edita clubes. `club.usuario_id` queda
  solo como dato histórico de quién lo creó.
- Una cuenta administra un único club (409 si intenta crear otro; sus asignaciones de
  otros clubes quedan pendientes).

`ClubCreate` (POST) exige además `rut`, el RUT del club como persona jurídica. Acepta
puntos y `k` minúscula (`76.123.456-k`) y se guarda normalizado (`76123456-K`). Se
valida el formato `^\d{7,8}-[\dK]$`, el dígito verificador (módulo 11) y que el
cuerpo sea ≥ 50.000.000; si no, `422` (único caso de validación con 422, como pide el
contrato). `409` si otro club ya tiene ese RUT. El RUT no se edita, aparece en
`ClubManaged` y en el perfil público, y no se valida contra el SII.

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

Respuesta de POST, PATCH y `/me`: el perfil (con `rut`) más `is_active`,
`estado_verificacion` (`pendiente`, `verificado`, `rechazado`; lo fija el equipo
de SportMatch), `fecha_creacion` y `fecha_actualizacion`. `/me` agrega `mi_rol`
(`PRINCIPAL` o `ADMIN`).

### Errores

- `400` datos o parámetros inválidos (campos desconocidos, formatos, largos,
  coordenadas incompletas, id que no es UUID). A diferencia de Ms_Users, que
  responde 422, aquí toda validación de entrada responde 400 con el mismo `detail`.
- `401` sin token o con token inválido/expirado.
- `403` el rol o la cuenta no administra ese club.
- `404` club inexistente (o inactivo, en las rutas públicas) o deporte inexistente/inactivo.
- `409` la cuenta ya administra un club, o el RUT ya existe.
- `422` RUT inválido al crear un club.

## Convocatorias (HU-32)

Cualquier administrador del club publica pruebas masivas o de jugadores con
`POST /api/v1/clubs/{club_id}/announcements` (mismo control de acceso que la
HU-31). `ConvocatoriaCreate`:

| Campo | Obligatorio | Validación |
| --- | --- | --- |
| `deporte_id` | Sí | Deporte activo del catálogo que el club ofrece |
| `categoria_edad` | Sí | Texto 2–40 (`Sub-17`, `Adulto`, `+35`, `14 a 18 años`) |
| `edad_minima`, `edad_maxima` | No | 4–99, mínima ≤ máxima; las usa el filtro `edad` |
| `fecha_prueba` | Sí | Fecha y hora con zona horaria (`2026-11-07T10:00:00-03:00`), futura y a no más de 365 días |
| `requisitos` | Sí | Lista de 1 a 15 textos (2–200) |
| `cupos` | Sí | Entero 1–1000 |
| `direccion` | Sí | Texto 5–255 |
| `comuna` | No | Mismo formato que en el club |
| `titulo` | No | Texto 3–120 |
| `descripcion` | No | Hasta 2000 |

Estado, calculado al leer (no se guarda, así cambia solo al pasar la fecha):

- `FINALIZADA`: la fecha de la prueba ya pasó.
- `CERRADA`: el club la cerró antes de esa fecha (`.../close`, idempotente; sobre una finalizada responde 409).
- `ABIERTA`: en otro caso.

`GET /api/v1/clubs/announcements` es la sección pública. Por defecto lista las
`ABIERTA`, la más próxima primero (las `FINALIZADA`, la más reciente primero).
Filtros opcionales y combinables: `deporte` (nombre, sin mayúsculas ni tildes),
`deporte_id`, `categoria` (parcial), `edad` (convocatorias cuyo rango la admite o
sin rango), `club_id`, `fecha_desde` y `fecha_hasta` (días en hora de Chile,
inclusive), `estado`, `page` y `page_size`. El perfil del club muestra sus
convocatorias abiertas en `convocatorias_abiertas`. Las convocatorias de clubes
inactivos no se muestran, y un club inactivo no puede publicar (409).

Errores propios: `400` fecha pasada, sin zona horaria o lejana, cupos ≤ 0,
faltan campos obligatorios, deporte inexistente o que el club no ofrece; `401`
sin token; `403` sin rol `club_admin` o club ajeno; `404` club o convocatoria
inexistente; `409` club inactivo o convocatoria finalizada.

## Postulaciones (HU-33)

Solo deportistas (roles `player` o `usuario` del token, como en Ms_Users)
postulan con `POST /api/v1/clubs/announcements/{convocatoria_id}/applications`.
`PostulacionCreate` (todo opcional):

- `mensaje`: presentación, hasta 1000 caracteres.
- `antecedentes`: hasta 10 entradas `{institucion, deporte, anio_inicio, anio_fin?, logros?}`
  (años desde 1950 y no futuros; `anio_fin` vacío = sigue ahí).

El **perfil deportivo** no lo envía el deportista: ms-club pide a Ms_Users la
tarjeta pública del propio deportista con su token (`GET /api/v1/users/athletes/{id}`)
y guarda una copia fija en `perfil_deportivo` (nombre, inicial del apellido,
edad, foto, biografía y deportes con nivel). Así el club ve lo que se envió, y
Ms_Users solo entrega tarjetas de deportistas activos con correo verificado.

Reglas, en este orden:

1. La convocatoria existe y su club está activo; si no, `404`.
2. Está `ABIERTA`; si está `CERRADA` o `FINALIZADA`, `409`.
3. Ms_Users entrega el perfil: `403` si la cuenta no puede (por ejemplo, correo
   sin verificar), `503` si Ms_Users no responde.
4. Si la convocatoria tiene rango numérico de edad y se conoce la edad del
   deportista, debe estar dentro; si no, `409`.
5. No había postulado antes; si no, `409`.
6. Quedan cupos: cada postulación que no está `RECHAZADO` ocupa uno; si no, `409`.

Los pasos 2, 5 y 6 se repiten dentro de la transacción del `INSERT`, con la fila de
la convocatoria bloqueada (`FOR UPDATE`), así dos postulaciones simultáneas no
toman el mismo último cupo. La respuesta `201` trae el `id` y `estado: PENDIENTE`.

`GET /api/v1/clubs/announcements/applications/me` lista las postulaciones del
deportista, la más reciente primero, con su convocatoria, su estado y el
`comentario_club` que haya dejado el club. Filtro opcional `estado`, más `page` y
`page_size`. Las convocatorias ahora informan `cupos_disponibles`.

## Revisión de candidatos (HU-34)

Estados de una postulación: `PENDIENTE` (inicial), `PRESELECCIONADO`, `ACEPTADO`,
`RECHAZADO`, sin tildes como los demás valores de la API. `PRESELECCIONADO`
reemplazó al `EN_REVISION` de la HU-33 (la migración 004 convierte las filas).

Solo un administrador (`club_admin`) del club de la convocatoria (mismo control que la
HU-31: `401` sin token, `403` otro rol u otro club, incluido `admin`). Funciona
aunque el club esté inactivo o la prueba ya haya pasado: la selección se
confirma después de la prueba.

- `GET /api/v1/clubs/announcements/{convocatoria_id}/applications`: la
  convocatoria y sus candidatos, el más antiguo primero, con `usuario_id`,
  `estado`, `perfil_deportivo` (copia adjunta al postular), `mensaje`,
  `antecedentes`, `comentario_club` y `fecha_postulacion`. Filtro opcional
  `estado`, `page`, `page_size`, y `conteo_por_estado` de toda la convocatoria.
- `PATCH /api/v1/clubs/announcements/applications/{postulacion_id}` con
  `PostulacionEstadoUpdate` (el `UpdateApplicationStatusDto`):
  `{"estado": "PENDIENTE" | "PRESELECCIONADO" | "ACEPTADO" | "RECHAZADO", "comentario": "..."}`.
  `PENDIENTE` es "en espera": el club deja la postulación sin decidir, sin aviso al
  deportista. `comentario` (hasta 1000) lo lee el deportista; si no se envía se mantiene, y
  `null` lo borra. Responde `200` con el candidato.
  Pasar desde `RECHAZADO` a otro estado vuelve a ocupar un cupo: `409` si no hay.
  `404` si la postulación no existe.

### Aviso al deportista

Si el estado cambia a `PRESELECCIONADO`, `ACEPTADO` o `RECHAZADO`, el mismo `UPDATE` escribe, en la misma transacción, un
evento en `clubs_api.notification_outbox`: el patrón de Matching y Actividades
(ver `Ms_gateway/docs/notificaciones.md`). El worker del gateway lo pasa a la
bandeja del deportista y lo envía por push. Repetir el mismo estado no genera
otro aviso. El evento lleva `kind: "convocatoria"`, un título y texto según el
estado (sin el comentario del club, porque la alerta puede verse en la pantalla
bloqueada) y `data: {screen: "convocatoria", convocatoriaId, postulacionId, estado}`.
Para que llegue, el gateway debe leer este outbox (ver `PENDIENTES.md`).

## Comunicados de clubes (HU-38)

Un club avisa a los deportistas vinculados a él. Las rutas viven bajo `/notices`
porque `/announcements` ya es de las convocatorias (HU-32).

`POST /api/v1/clubs/{club_id}/notices` (solo un administrador del club, mismo control
que la HU-31; `404` si el club no existe, `409` si está inactivo) recibe
`ComunicadoCreate` (el `CreateAnnouncementDto`):

| Campo | Obligatorio | Validación |
| --- | --- | --- |
| `titulo` | Sí | 3–120 caracteres |
| `contenido` | Sí | 5–2000 caracteres |
| `audiencia` | Sí | `TODOS`, `POSTULANTES` o `MIEMBROS` |
| `prioridad` | No | `NORMAL` (por defecto) o `URGENTE` |

Respuesta `201` con el comunicado y `destinatarios` (a cuántos deportistas llegó).

**Audiencia.** No hay tabla de seguidores ni suscriptores, así que se define por el
estado de las postulaciones a las convocatorias del club:

- `POSTULANTES`: postulaciones en curso (`PENDIENTE` o `PRESELECCIONADO`).
- `MIEMBROS`: deportistas aceptados (`ACEPTADO`), es decir, confirmados.
- `TODOS`: la comunidad del club, ambos grupos. Los `RECHAZADO` nunca reciben avisos.

Un deportista aceptado en una convocatoria y pendiente en otra pertenece a las dos
audiencias, y recibe un solo aviso por comunicado.

**Destinatarios fijos.** La audiencia se resuelve al publicar y se guarda
(`comunicado_destinatario`). La bandeja muestra exactamente lo que se notificó:
quien se postula después no recibe los comunicados anteriores, y quien cambia de
estado conserva los que ya recibió.

**Notificación.** En la misma transacción que el comunicado se escribe un evento por
destinatario en `clubs_api.notification_outbox` (el mecanismo de la HU-34; ver
`Ms_gateway/docs/notificaciones.md`): `kind: "comunicado"`, título del comunicado
(con el prefijo `Urgente:` si lo es), texto `"{club}: {primeros 120 caracteres}"` y
`data: {screen: "comunicado", comunicadoId, clubId, prioridad}`. El texto completo se
lee en la app, no en la alerta. Si algo falla, no queda comunicado ni notificaciones.

`GET /api/v1/clubs/notices/me` (solo deportistas) es la bandeja: los comunicados
dirigidos al deportista, el más reciente primero. Filtros opcionales `club_id`,
`prioridad`, `page` y `page_size`. `GET /api/v1/clubs/{club_id}/notices` lista lo que
publicó el club (filtros `audiencia` y `prioridad`). Los comunicados no se editan ni
se borran.

## Administradores del club

Un club tiene varios administradores (cuentas `club_admin` de Ms_Users) en
`club_administrador`: el `PRINCIPAL`, que lo creó, y los `ADMIN`, que entraron porque el
principal asignó su correo. Cualquiera de ellos gestiona el club (perfil, deportes,
convocatorias, candidatos, comunicados, consultas y pagos del club) y ve la lista de
administradores y de asignaciones pendientes. El deportista que entra a un club no es
administrador.

- **Correo en el token.** Ms_Users agrega al access token el claim `email` (minúsculas;
  solo se emite con el correo verificado). `Actor.email` lo lee si es un texto con forma
  de correo; si falta o no sirve queda en `None` sin invalidar el token (tokens antiguos).
- **Asignar.** `POST /{club_id}/admins/assignments` con `{"email": "..."}`, solo el
  `PRINCIPAL` (`403` si no). El correo se valida con `email-validator` (`422` si no es
  válido) y se guarda en minúsculas. `409` si ya está pendiente en este club, si es el
  correo del propio principal (claim `email`), si ya administra este club o si los
  administradores más las asignaciones pendientes llegan a `CLUB_MAX_ADMINS` (por defecto
  10; entre 3 y 100). La respuesta (`{id, email, asignada_por, fecha_creacion}`) **nunca
  indica si existe una cuenta con ese correo**. `GET` lista las pendientes (la más
  reciente primero) y `DELETE .../assignments/{id}` revoca una (solo el `PRINCIPAL`;
  `404` si no existe, es de otro club o ya no está pendiente).
- **Activar.** En `GET /api/v1/clubs/me`, si la cuenta es `club_admin`, no administra
  ningún club y su token trae `email`, se toma en una transacción (`FOR UPDATE`) la
  asignación pendiente más antigua de ese correo en un club activo: se crea su fila `ADMIN`
  (`agregado_por` = quien asignó) y la asignación queda aceptada. Una cuenta de deportista
  con ese correo nunca la activa. Si la cuenta ya administra un club, sus asignaciones de
  otros clubes siguen pendientes. Sin asignación, `404` como antes.
- **Quitar y salir.** `DELETE /{club_id}/admins/{usuario_id}`: el `PRINCIPAL` quita a un
  `ADMIN`; un `ADMIN` sale con su propio `usuario_id` y no puede quitar a otros (`403`).
  El `PRINCIPAL` no puede ser quitado ni salir (`409`; el traspaso queda pendiente).
  Alguien que no es administrador de ese club responde `404`. Para devolverle el acceso
  a alguien se asigna su correo de nuevo.
- **Nombre.** Al crear el club o al activar una asignación se pide a Ms_Users el perfil
  de la propia cuenta con su token (`GET /api/v1/users/{id}/profile`) y se guarda
  "nombre apellido"; en `/me` solo se pide cuando hay una asignación que activar. Si
  Ms_Users falla se guarda `Administrador del club`: nunca bloquea.

## Consultas deportista ↔ club (HU-51)

Una conversación (`consulta_hilo`) por club y deportista. Del lado del club es una
bandeja compartida: cualquier administrador lee y responde, y el deportista ve las
respuestas firmadas por el club, nunca por la persona (no se expone `autor_id`).

- Deportista (roles `player`/`usuario`): `GET /consultas/me` (sus conversaciones con
  clubes activos, la del último mensaje primero), `GET` y `POST
  /{club_id}/consultas/me/messages` y `POST /{club_id}/consultas/me/read`. El primer
  mensaje crea la conversación y guarda el nombre del deportista (de Ms_Users con su
  token; `Deportista` si falla). Club inexistente o inactivo: `404`.
- Chat por convocatoria (migración 011): cada convocatoria tiene `chat_postulantes_habilitado`
  (booleano, `true` por defecto, lo que ya permitía el sistema). Con `false`, los postulantes
  leen su consulta, pero `POST .../consultas/me/messages` responde `403` ("solo puedes leer");
  el club siempre puede escribir. Un deportista con una postulación activa a una convocatoria
  con el chat apagado en ese club no puede escribir al club. Se cambia con
  `PATCH /api/v1/clubs/{club_id}/announcements/{convocatoria_id}/chat`, body
  `{"chat_postulantes_habilitado": bool}`, solo para administradores del club dueño (`403` a
  otros, `404` si la convocatoria no es del club). No avisa a nadie. Cada convocatoria lo
  devuelve en `chat_postulantes_habilitado`.
- Club (administradores): `GET /{club_id}/consultas`, `GET` y `POST
  /{club_id}/consultas/{hilo_id}/messages` y `POST /{club_id}/consultas/{hilo_id}/read`.
  Una conversación de otro club responde `404`. Un club inactivo lee pero no responde (`409`).
- `Mensaje = {id, de: "deportista" | "club", texto, enviado_en, propio}`. `texto` de 1 a
  1000 caracteres, sin solo espacios; `client_message_id` es un UUID de la app: repetirlo
  devuelve el mismo mensaje (`201`) sin duplicarlo ni volver a avisar.
- Paginación: orden ascendente por `id`; `after_id` trae los siguientes, `before_id` los
  anteriores y sin ninguno, los últimos; `limit` de 1 a 100 (50 por defecto).
- Sondeo con pocos recursos (sección F del contrato): leer mensajes (`GET .../messages`)
  y marcar leído (`POST .../read`) usan, de cada lado, una sola conexión y una sola
  sentencia SQL que comprueba el club, el permiso y la conversación; nunca llaman a
  Ms_Users. Marcar leído no escribe si la marca no avanza.
- Leídos: `{"hasta_id": n}` avanza la marca de ese lado (nunca retrocede ni pasa del
  último mensaje). `no_leidos` cuenta los mensajes del otro lado posteriores a la marca;
  del lado del club la marca es compartida.
- Avisos en `clubs_api.notification_outbox`, en la misma transacción del mensaje y
  **sin su texto**, con `kind: "consulta"`: al deportista cuando responde el club
  (`data: {screen: "consulta", clubId, hiloId}`) y a cada administrador cuando escribe
  el deportista (`data: {screen: "consulta_club", clubId, hiloId}`).
- Solo con postulantes (G3): el deportista lee y escribe solo si tiene una postulación
  distinta de `RECHAZADO` en el club (si no, `403`); el club puede escribir primero con
  `POST /{club_id}/consultas/postulantes/{usuario_id}/messages` (`404` si no es postulante
  activo; el nombre sale del perfil de su postulación). Al rechazar una postulación sin
  que quede otra activa en el club, la conversación se borra en la misma transacción.

## Canales del club (G4)

Anuncios del club a grupos de sus deportistas: hasta 20 canales por club y 500 miembros
por canal. Al crear uno entran los deportistas con alguna postulación `ACEPTADO`; luego
se agregan o sacan postulantes activos a mano. Escriben los administradores (firmado
como el club) y, si `miembros_escriben`, también los miembros. Otro club o quien no es
miembro recibe `404`. Avisos (`kind: "canal"`, sin el texto) a los miembros que no lo
silenciaron. Leer y marcar leído son una conexión y una sentencia, sin Ms_Users.

Inactividad: un hilo de fondo revisa cada hora (con `pg_try_advisory_lock`, una sola
instancia a la vez) los canales sin mensajes hace 30 días, avisa a los administradores
(`data: {screen: "canal_inactivo", clubId, canalId}`) y marca `aviso_inactividad_en`. El
club responde `eliminar` o `mantener` (30 días más, `mantener_hasta`); sin respuesta en 7
días se elimina. Un mensaje nuevo cancela el aviso. `CANALES_INACTIVIDAD_ENABLED=false`
lo apaga; solo corre con la base real.

## Pagos con Webpay Plus (HU-40)

Un deportista o un club contrata un plan y paga con Webpay Plus. Las tablas son
nuevas y propias de este servicio (`plan_suscripcion`, `transaccion_pago`,
`suscripcion`); no usan las `plan`/`membresia`/`pago` que existen en la base
`sportmatch_users`.

**Flujo**

1. `GET /api/v1/payments/plans` lista los planes (precio en pesos chilenos).
2. `POST /api/v1/payments/webpay/initiate` con `{"plan_id": 1}` (`PagoIniciar`, el
   `InitiatePaymentDto`). Responde `201` con `token_ws`, `url`, `buy_order`,
   `session_id`, `amount` y `status: "PENDING"`, y registra la transacción.
   El comprador sale del token (un deportista paga por sí mismo; un `club_admin`,
   por su club) y el monto es el precio del plan: ninguno viaja en la petición.
3. La app abre `checkout_url`, un enlace firmado que genera el formulario **POST**
   con `token_ws`, sin iframe. La web inicia con `client: "web"` y usa el mismo puente.
4. Webpay devuelve al usuario a `/api/v1/payments/webpay/return`, que confirma
   con Transbank y lo reenvía (303) a `WEBPAY_FRONTEND_RETURN_URL` (app) o
   `WEBPAY_WEB_RETURN_URL` (web), con los parámetros como query:
   `token_ws` si pasó por el formulario; `tbk_token`, `tbk_orden_compra` y
   `tbk_id_sesion` si canceló; solo los dos últimos si se agotó el tiempo.
5. La app llama a `POST /api/v1/payments/webpay/commit` con esos parámetros
   (`PagoConfirmar`, el `CommitPaymentDto`) y la sesión del usuario para recuperar
   el comprobante o reintentar. Ante una respuesta perdida, el backend consulta el estado.

Configuración, pruebas en Transbank y revisión de la web: [guía Webpay](docs/webpay/README.md).

**Resultado de `commit`**

| Caso | Respuesta | Transacción | Suscripción |
| --- | --- | --- | --- |
| Aprobado (`response_code == 0` y `status == AUTHORIZED`) | `200` con el comprobante y la `suscripcion` | `APPROVED` | Se crea o se extiende `duracion_dias` del plan |
| Rechazado por el emisor o Webpay | `400`, `detail` y `transaccion` (comprobante) | `REJECTED` | Sin cambios |
| Cancelado o tiempo agotado (`tbk_*`) | `400`, igual | `CANCELLED` | Sin cambios |
| Webpay no responde, o responde otra orden o monto | `502` | Sigue `PENDING` | Sin cambios |
| Transacción inexistente o de otra cuenta | `404` | Sin cambios | Sin cambios |

Confirmar dos veces la misma transacción devuelve el mismo resultado sin volver a
llamar a Webpay, ni cobrar, ni extender la suscripción. Solo la cuenta que inició
el pago puede confirmarlo.

**Suscripción.** Una por deportista o club. Pagar el mismo plan estando vigente suma
los días al fin actual; si ya venció, parte desde hoy. Con otro plan vigente se
rechaza al iniciar (`409`), para no perder días ya pagados. El estado `ACTIVE`/`EXPIRED`
se calcula al leer: nada tiene que correr cuando vence. `GET /subscription/me` la
muestra. Un plan `USUARIO` solo lo compran deportistas y uno `CLUB`, representantes
con un club creado.

**Modo de pruebas.** `WEBPAY_MOCK_MODE=true` reemplaza a Webpay por una página
simulada de este servicio (`/api/v1/payments/webpay/mock`) con tres botones: pagar,
simular rechazo y anular. No cobra nada y aprueba lo que el usuario elija, así que el
servicio **no arranca** si se combina con `WEBPAY_ENVIRONMENT=PRODUCTION`. Con
`false` (el valor por defecto) se usa el adaptador `TransbankWebpay`, que en
`INTEGRATION` emplea las credenciales públicas de prueba de Transbank. Para usar el
SDK oficial, se implementa `WebpayGateway` (`create_transaction`,
`commit_transaction`) y se pasa a `create_app()`; nada más cambia.

## Base de datos y ejecución

Variables en `.env` (ver `.env.example`): `DB_HOST`, `DB_PORT`, `DB_USER`,
`DB_PASSWORD`, `DB_NAME` (por defecto `sportmatch_clubs`), `JWT_SECRET`,
`JWT_ISSUER`, `JWT_AUDIENCE`, que deben ser los mismos de Ms_Users, y
`USERS_SERVICE_URL` (por defecto `http://127.0.0.1:8001`) y `USERS_TIMEOUT_SECONDS`
para pedir el perfil deportivo y los nombres a Ms_Users. Para postular, Ms_Users debe
estar corriendo; el resto del servicio no lo necesita (sin él, los nombres quedan
neutros). `CLUB_MAX_ADMINS` (3 a 100, por defecto 10) limita los administradores por club. Los pagos usan `WEBPAY_MOCK_MODE`,
`WEBPAY_ENVIRONMENT`, `WEBPAY_COMMERCE_CODE`, `WEBPAY_API_KEY`, `WEBPAY_PUBLIC_URL`,
`WEBPAY_FRONTEND_RETURN_URL` y `WEBPAY_TIMEOUT_SECONDS` (ver `.env.example`); las
primeras dos URL son obligatorias y la API key nunca se registra en logs. Igual que
Ms_Users, el `.env` se carga con `--env-file` de uvicorn.

Al arrancar, el servicio valida la configuración JWT, se conecta una vez a
PostgreSQL, comprueba que la base sea `DB_NAME` y registra `Connected to
PostgreSQL usuario@host:puerto/base`. Si algo falla, no arranca y muestra la
causa. `GET /api/v1/clubs/health/ready` repite la comprobación en caliente.

Una vez, en pgAdmin conectado a `sportmatch_clubs` como `postgres`, en este orden:

1. `db/00_create_database.sql` (reemplazar el password por `DB_PASSWORD` del `.env`).
2. `db/migrations/001_club_profile_admin.sql`.
3. `db/migrations/002_convocatorias.sql`.
4. `db/migrations/003_postulaciones.sql`.
5. `db/migrations/004_revision_postulaciones.sql`.
6. `db/migrations/005_comunicados.sql`.
7. `db/migrations/006_pagos.sql`.
8. `db/migrations/007_club_rut_administradores.sql`.
9. `db/migrations/008_consultas_club.sql`.
10. `db/migrations/009_admin_por_correo.sql`.
11. `db/migrations/010_consultas_postulantes_canales.sql`.
12. `db/migrations/011_chat_postulantes_convocatoria.sql`.
13. Solo en desarrollo, para tener planes de prueba: `db/seeds/demo_planes.sql`.

```bash
pip install -e ".[dev]"
uvicorn app.main:app --env-file .env --port 8004 --reload
python -m pytest
```

Puerto 8004 (users 8001, matching 8002, activities 8003). Aún no está
registrado en el gateway ni en `Ms_gateway/compose.yaml`.

Lo que falta por hacer está en [PENDIENTES.md](PENDIENTES.md).
