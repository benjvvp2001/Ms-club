# ms-club — pendientes

Todo lo que queda abierto en `ms-club`: tareas por hacer y decisiones tomadas
durante el desarrollo que el equipo debe confirmar. Cubre la HU-30 (directorio y
perfiles de clubes), HU-31 (administrar perfil de club), HU-32 (convocatorias),
HU-33 (postulaciones), HU-34 (revisión de candidatos), HU-38 (comunicados), HU-40
(pago de planes con Webpay Plus), HU-49 (lista de chats), HU-50 (ubicación y archivos en el chat),
HU-51 (bloquear y silenciar) y HU-52 (tiempo real y push de mensajes). La HU-49, la HU-50 y la HU-52 están
**implementadas en `ms-machine`** y la HU-51 en **`Ms_Users`**, no en ms-club, pero su seguimiento queda aquí. Este archivo se actualiza con cada historia; lo ya
implementado está en el [README](README.md).

Las dudas sobre tablas e información que faltan por confirmar están reunidas en [DUDAS_TABLAS_E_INFORMACION.md](DUDAS_TABLAS_E_INFORMACION.md).

## 1. Puesta en marcha local

- [ ] En pgAdmin, conectado a `sportmatch_clubs` como `postgres`, ejecutar
      `db/00_create_database.sql` con el `DB_PASSWORD` de `.env` (crea el rol
      `sportmatch_clubs`, que hoy no existe).
- [ ] En el mismo lugar y en orden: `db/migrations/001_club_profile_admin.sql`,
      `002_convocatorias.sql`, `003_postulaciones.sql`,
      `004_revision_postulaciones.sql`, `005_comunicados.sql` y `006_pagos.sql`. Sin la
      003 fallan también las rutas de convocatorias y el perfil de club (informan cupos
      y convocatorias abiertas); sin la 004 fallan las de postulaciones (leen
      `comentario_club`); la 005 solo afecta a las rutas de comunicados y la 006 a las
      de pagos.
- [ ] Para probar pagos, ejecutar también `db/seeds/demo_planes.sql` como `postgres`
      (dos planes de PRUEBA) y dejar `WEBPAY_MOCK_MODE=true` en el `.env` local.
- [ ] **Agregar las variables `WEBPAY_*` al `.env` de cada integrante** (copiarlas de
      `.env.example`): el servicio no arranca sin `WEBPAY_PUBLIC_URL` ni
      `WEBPAY_FRONTEND_RETURN_URL`. El `.env` de esta máquina ya las tiene, con el modo
      simulado activo.
- [ ] Reinstalar dependencias (`pip install -e ".[dev]"`): se agregaron `PyJWT`,
      `email-validator`, `httpx` y `tzdata`.
- [ ] Arrancar con `uvicorn app.main:app --env-file .env --port 8004` y confirmar
      `Connected to PostgreSQL ...` en el log.
- [ ] Para postular (HU-33), Ms_Users debe estar corriendo en `USERS_SERVICE_URL`
      (por defecto `http://127.0.0.1:8001`).
- [ ] Probar los endpoints con datos reales y tokens reales de Ms_Users (un
      `club_admin` y un `player`). Hasta ahora se validó con tests y con una copia
      de la estructura de las tablas en PostgreSQL, sin datos reales: las tablas
      reales no se pudieron leer porque el rol disponible no tiene permisos.
- [ ] Crear una cuenta `club_admin` para probar: hoy solo se puede cambiando el
      rol a mano en la base de Ms_Users (el registro siempre asigna `player`).
- [ ] **A revisar: conectar los servicios a las 4 bases `sportmatch_*` de PostgreSQL 18** (`sportmatch_users`,
      `sportmatch_matching`, `sportmatch_activities`, `sportmatch_clubs`; dueño `postgres`). Estado verificado el
      2026-10-14, **sin ningún objeto nuevo creado** (la arquitectura está lista; la decisión de tocar las bases queda
      pendiente):
      - Solo existe el rol `sportmatch_users`. Faltan los roles de matching, activities y clubs, y `ms-club` no entra
        con la contraseña de su `.env`.
      - Las bases tienen tablas antiguas en `public` (`usuario`, `match`, `solicitud_match`, `actividad`, `club`,
        `club_deporte`, `interesado_club`…), pero ninguna de las nuevas de los servicios (`matching_api.*`,
        `activities_api.*`, `clubs_api.*`, ni `convocatoria`, `postulacion_convocatoria`, `comunicado_*`, planes y pagos).
      - Decidir entre (a) **adaptar el código a las tablas existentes**, como se hizo con `club` y `club_deporte` en
        `ms-club`: matching sobre `match`/`solicitud_match`, actividades sobre `actividad`/`inscripcion_actividad`; o
        (b) aplicar las migraciones de cada servicio (tablas nuevas y columnas nuevas en `club`).
      - Con cualquiera de las dos hay que crear o definir un rol por servicio (permisos mínimos, no `postgres`) y
        ajustar su `.env`: en `Ms_Users` solo cambiaría el nombre de la base de `users_db` a `sportmatch_users`
        (su rol y contraseña ya funcionan, la base está vacía: 0 usuarios).
      - Falta además la migración `003` (cola de avisos de Matching) en `ms-machine/db/migrations`.
      - Un intento de aplicar las migraciones (3 roles, esquemas `*_api`, tablas de clubes y 8 columnas nuevas en
        `club`) **se deshizo por completo** a pedido de la persona dueña del proyecto: no queda nada de eso.

- [x] **Ubicación en vivo del usuario (Ms_Users)**: implementada. `GET/PUT/DELETE /api/v1/users/location` (también en el gateway);
      una fila por persona en `ubicacion_actual` (migración `008_current_location.sql`, **sin aplicar en tus bases**). Las
      sugerencias con `radius_km` usan la posición en vivo si es reciente y, si no, la zona del perfil, para quien consulta
      y para cada candidato. Las coordenadas no se devuelven ni se registran. Probado: 23 tests con repositorio simulado,
      6 con SQL real, 10 de 10 rupturas a propósito detectadas y 16/16 de punta a punta (gateway real, Ms_Users real,
      PostgreSQL con las migraciones 001 a 008).
- [ ] **Definir la distancia de actualización** ("cada cierta distancia recorrida"): por ahora `LOCATION_MIN_MOVE_METERS=200`
      m, y la app debe enviar al recorrer ese valor (lo lee con `GET /location`). Falta decidir el valor real, y también
      `LOCATION_MAX_AGE_MINUTES` (por ahora 1440: una posición de más de un día se ignora).
- [ ] **Aplicar `008_current_location.sql` antes de desplegar Ms_Users**: sin la tabla las sugerencias fallan, porque la consulta
      la une. `users_migrate` / `python -m app.database.migrate` la aplican. Hoy tu base `sportmatch_users` no tiene
      ninguna de las migraciones nuevas de Ms_Users (005 a 008).
- [ ] **Falta en la app**: pedir permiso de ubicación al usuario, detectar que recorrió la distancia y llamar a `PUT /location`
      (y a `DELETE /location` si el usuario desactiva compartirla). Segundo plano en el teléfono y consumo de batería: a definir.
- [ ] **Consentimiento y retención**: ubicación es un dato sensible. Falta registrar el consentimiento en `consent` antes de
      aceptar el primer `PUT /location`, y decidir si se borran posiciones de cuentas inactivas (hoy solo se ignoran al
      pasar `LOCATION_MAX_AGE_MINUTES`, no se eliminan).
- [ ] **Recomendaciones en Matching**: el filtro de cercanía vive solo en `GET /users/suggestions` de Ms_Users. Si Matching
      también ordena por cercanía, debe usar la misma fuente. La tabla antigua `sportmatch_matching.ubicacion_usuario` no se usa.
- [ ] **Coordenadas del club**: se podrían obtener de la dirección que escribe quien crea el club (geocodificación) para que no tenga que ingresarlas a mano. Hoy `latitud`/`longitud` se envían manualmente, ambas o ninguna.

## 2. Decisiones por confirmar con el equipo

Se tomaron para cumplir los criterios; cambiarlas es acotado.

| Decisión | Motivo | Dónde cambiarla |
| --- | --- | --- |
| El rol de representante es `club_admin` (no `club` ni `ADMIN_CLUB`) | Son los únicos roles que existen en Ms_Users: `admin`, `club_admin`, `player` | `app/core/security.py` |
| `admin` de SportMatch no puede editar clubes ni sus deportes | La HU-31 pide un panel exclusivo del representante dueño | `app/services/authorization.py` |
| Solo `PATCH` para editar el club (no `PUT`) | La HU-31 pedía uno u otro; PATCH permite cambios parciales | `app/api/v1/clubs.py` |
| Rutas bajo `/api/v1/clubs` (`/announcements`, `/announcements/applications/me`) en vez de `/api/v1/calls` o `/api/v1/applications` | Un solo prefijo por servicio, como Ms_Users; simplifica el gateway | `app/api/v1/convocatorias.py`, `postulaciones.py` |
| Errores de validación responden `400` (Ms_Users responde `422`) | Las HU piden 400 para datos inválidos | `app/main.py` |
| Estados de postulación sin tilde; `PRESELECCIONADO` reemplazó a `EN_REVISION` (HU-33) y la migración 004 convierte las filas existentes | La HU-34 define `PENDIENTE`, `PRESELECCIONADO`, `ACEPTADO`, `RECHAZADO`; sin tildes como el resto de la API | `app/schemas/postulacion.py`, migración 004 |
| Rutas de la HU-34: `GET /announcements/{id}/applications` y `PATCH /announcements/applications/{id}` (no `/api/v1/applications/:id/status`) | Mismo prefijo `/api/v1/clubs`; el GET comparte ruta con el POST de postular | `app/api/v1/postulaciones.py` |
| El club puede revisar aunque la prueba ya pasó o el club esté inactivo | La selección se confirma después de la prueba; el dueño sigue administrando su club | `app/services/postulacion_service.py` |
| No se puede volver a `PENDIENTE`; cualquier otro cambio entre `PRESELECCIONADO`, `ACEPTADO` y `RECHAZADO` está permitido | PENDIENTE solo indica "sin revisar" | `PostulacionEstadoUpdate` en `app/schemas/postulacion.py` |
| Salir de `RECHAZADO` vuelve a ocupar un cupo (409 si no hay) | Mantiene la regla de cupos de la HU-33 | `review_postulacion` en los repositorios |
| La notificación no incluye el comentario del club | La alerta puede verse en la pantalla bloqueada; el comentario se lee en la app | `_AVISOS` en `app/services/postulacion_service.py` |
| El evento de notificación se escribe desde el código del repositorio, no con un trigger como en Ms_activities | Mismo resultado transaccional, y es explícito y probado en los tests | `review_postulacion` en los repositorios |
| Repetir el mismo estado no genera otro aviso (solo actualiza el comentario) | Igual que Matching y Actividades: operaciones sin cambios no notifican | `review_postulacion` en los repositorios |
| Comunicados bajo `/api/v1/clubs/{id}/notices` y `/api/v1/clubs/notices/me` (la HU-38 proponía `.../announcements`) | `/announcements` ya es de las convocatorias (HU-32): `POST /{id}/announcements` publica una convocatoria | `app/api/v1/comunicados.py` |
| Campo `audiencia` en snake_case/español (la HU decía `audienciaTarget`) | Misma convención que el resto de la API (`categoria_edad`, `deporte_id`...) | `ComunicadoCreate` en `app/schemas/comunicado.py` |
| **Audiencia sin tabla de seguidores**: `POSTULANTES` = postulaciones `PENDIENTE`/`PRESELECCIONADO`; `MIEMBROS` = `ACEPTADO`; `TODOS` = ambos grupos (nunca `RECHAZADO`) | No existe seguimiento ni suscripción a clubes; `interesado_club` está en la base pero su significado no está definido y no se pudo leer | `ESTADOS_POR_AUDIENCIA` en `app/schemas/comunicado.py` |
| La audiencia se resuelve y guarda al publicar (`comunicado_destinatario`) | La bandeja muestra exactamente lo notificado, aunque luego cambien las postulaciones; quien se postula después no recibe avisos antiguos | `create_comunicado` en los repositorios |
| Un club inactivo no puede publicar comunicados (409), pero sí consultar los suyos | Igual que con las convocatorias | `app/services/comunicado_service.py` |
| Publicar para una audiencia sin destinatarios responde 201 con `destinatarios: 0` | No es un error del club; la cifra le avisa que no llegó a nadie | `app/services/comunicado_service.py` |
| Los comunicados no se editan, borran ni se programan | La HU solo pide emitir y consultar; el rol del servicio no tiene UPDATE/DELETE sobre ellos | migración 005 |
| La alerta lleva solo los primeros 120 caracteres; el texto completo se lee en la app | La alerta puede verse en la pantalla bloqueada | `_PREVIEW_MAX` en `app/services/comunicado_service.py` |
| **Los pagos viven en ms-club con tablas nuevas** (`plan_suscripcion`, `transaccion_pago`, `suscripcion`), sin usar las `plan`/`membresia`/`pago` que ya existen en la base `sportmatch_users` | Los README de Ms_Users se contradicen (uno dice que siguen en USERS, otro que pasan a clubs); esas tablas son de otra base, no se pueden leer ni cambiar con este rol, y no guardan token ni sesión de Webpay. La HU pedía `TransaccionPago` y `SuscripcionUsuario` | `db/migrations/006_pagos.sql` |
| La `suscripcion` sirve a deportistas y a clubes (una fila por comprador) | La HU habla de "usuario/club comprador" | `006_pagos.sql` |
| Estados en inglés (`PENDING`, `APPROVED`, `REJECTED`, `CANCELLED`, `ACTIVE`, `EXPIRED`) | Los pidió la HU; el resto de la API está en español | `app/schemas/pago.py`, `006_pagos.sql` |
| Rutas bajo `/api/v1/payments` (no `/api/v1/clubs`) | La HU propone ese prefijo; los pagos no son solo de clubes | `app/api/v1/pagos.py` |
| El comprador sale del token y el monto es el precio del plan; el cliente solo envía `plan_id` | Evita que alguien pague por otra cuenta o elija el monto | `PagoService.initiate` |
| Un deportista solo compra planes `USUARIO`; un `club_admin`, planes `CLUB` y con club ya creado | Un plan de club se cobra a su club | `PagoService._comprador` |
| Un comprador tiene una sola suscripción; con otro plan vigente, `initiate` responde 409 | Cambiar de plan a mitad de periodo perdería o regalaría días; falta definir cómo se prorratea | `PagoService.initiate` |
| Renovar el mismo plan suma los días al fin vigente (o parte de hoy si ya venció) | La HU pide "extender la vigencia (+30 días)" | `settle_transaccion` en los repositorios |
| Pago rechazado o cancelado responde **400** con el comprobante en `transaccion` | La HU pide 400 en rechazos y el cliente necesita el comprobante | `app/api/v1/pagos.py` |
| Cancelación y tiempo agotado (parámetros `TBK_*`) pasan la transacción a `CANCELLED`, sin llamar a Webpay | Así lo describe la documentación de Transbank; tienen prioridad sobre `token_ws` | `PagoConfirmar` en `app/schemas/pago.py` |
| El backend tiene un puente `/api/v1/payments/webpay/return` que redirige a la app | Webpay vuelve por GET (o POST en integración) a una URL; una app móvil o SPA necesita ese salto, y no se acepta una URL de retorno elegida por el cliente | `webpay_return` en `app/api/v1/pagos.py` |
| `WEBPAY_MOCK_MODE` vale `false` si no se define, y no se permite con `WEBPAY_ENVIRONMENT=PRODUCTION` | Una variable olvidada nunca debe convertir los pagos en aprobaciones gratis | `get_webpay_settings` en `app/core/config.py` |
| Adaptador propio de la API REST de Transbank (sin el SDK oficial) | Evita una dependencia; el contrato `WebpayGateway` permite cambiarlo por el SDK sin tocar nada más | `app/services/webpay.py` |
| En `INTEGRATION` se usan las credenciales públicas de prueba de Transbank si no se definen otras | Están en su documentación y en sus SDK; no cobran | `app/core/config.py` |
| Solo se guardan los últimos 4 dígitos de la tarjeta | Datos mínimos | `webpay.py`, `transaccion_pago.card_last4` |
| El estado de la suscripción (`ACTIVE`/`EXPIRED`) se calcula al leer | No hay que correr nada cuando vence | `get_suscripcion` en los repositorios |
| **HU-49 se implementó en `ms-machine` (Matching)**, no en ms-club ni en un `ms-chat` nuevo | El único chat con mensajes está ahí (`matching_api.messages`) y la app ya lo usa; `Ms_activities` no tiene chat (su README deja el chat grupal fuera de alcance). Decidido con el equipo tras comparar las opciones | `ms-machine/` |
| No se crearon las tablas `conversations`, `conversation_participants` ni `messages` de la HU: una conversación es un match aceptado (`matching_api.requests`) y los mensajes son `matching_api.messages` | Crearlas habría duplicado el chat y dejado fuera todos los mensajes existentes | `ms-machine/db/migrations/004_message_read_state.sql` |
| El estado de lectura es `messages.read_at` (fecha, nulo = sin leer) y la API expone también `is_read` | `read_at` da más información que un booleano (el acuse de lectura trae su hora) | `004_message_read_state.sql`, `app/schemas/matching.py` |
| La migración se llamó `004` y marca como leídos todos los mensajes anteriores | La `003` está reservada para `003_notification_outbox` (documentada para Matching); evitar contadores inflados el primer día | `ms-machine/db/migrations/` |
| Rutas `/api/v1/chats` (lista, mensajes, `read`) en un router aparte; JSON en camelCase solo en esa respuesta | Es el contrato que pidió la HU; el router propio permite extraerlas luego a un `ms-chat` | `ms-machine/app/api/v1/chats.py`, `app/schemas/chats.py` |
| Abrir la conversación (GET de mensajes, también por la ruta antigua `/matching/matches/{id}/messages`) marca como leído hasta el mensaje más nuevo **entregado**, no todo | Un mensaje que llega durante la lectura nunca debe quedar leído sin haberse visto | `PostgresMatchingRepository.messages` |
| Un match sin mensajes aparece en la lista (`lastMessage: null`, `unreadCount: 0`) | Es lo que hace hoy la pestaña de chats | `PostgresMatchingRepository.chats` |
| `participant.name` es el nombre más la inicial del apellido (`Juan P.`) | Es lo único que Users entrega de otra persona | `MatchingService.chats` |
| Los errores de validación de `ms-machine` siguen siendo 422 (a diferencia de ms-club, que responde 400) | Se respetó la convención de ese servicio | — |
| **HU-50 se implementó en `ms-machine`** (la HU decía `ms-clubs`) | La HU extiende la tabla `messages` y la ruta de enviar mensajes del chat, que viven en Matching (ver HU-49) | `ms-machine/` |
| Imágenes y archivos se reciben como **enlace** (`attachmentUrl`), no como archivo subido, y solo se aceptan enlaces `https` a hosts de `CHAT_ATTACHMENT_HOSTS` con extensión permitida; con la lista vacía responden 503 | No hay servicio de almacenamiento. Aceptar cualquier URL dejaría que un usuario haga cargar a la app de otro contenido de su servidor (revela su IP) | `ms-machine/app/services/attachments.py` |
| `svg`, `html`, ejecutables y comprimidos no se aceptan; HEIC tampoco | SVG y HTML pueden ejecutar scripts; HEIC (iPhone) no se muestra en todos los clientes | `IMAGE_EXTENSIONS` / `FILE_EXTENSIONS` en `attachments.py` |
| La ruta `POST /api/v1/chats/{id}/messages` responde 200 (no 201) | Igual que la ruta de texto existente, que devuelve 200 también en los reintentos idempotentes | `ms-machine/app/api/v1/chats.py` |
| `clientMessageId` es opcional en la ruta nueva (la HU no lo incluía) | Sin él no se puede reintentar sin duplicar; el servidor genera uno si falta | `ChatMessageCreate` |
| La entrada acepta camelCase y snake_case; la respuesta de mensajes sigue en snake_case | El payload de la HU es camelCase; el resto de la respuesta de mensajes ya lo consume la app | `ms-machine/app/schemas/chats.py` |
| Un mensaje sin texto se resume como «Imagen», «Archivo» o «Ubicación» en la lista de chats | Para que la lista nunca muestre un vistazo vacío | `_PREVIEW` en `postgres_matching_repository.py` |
| **HU-51 está en `Ms_Users`**, y reutiliza la tabla `bloqueo_usuario` (HU-42) en vez de crear `user_blocks` | Los bloqueos ya existían con la misma forma (par único bloqueador/bloqueado, borrado en cascada); duplicarla dejaría dos listas de bloqueos | `Ms_Users/db/migrations/006_reports_blocks.sql` |
| **Un bloqueo ahora retira la tarjeta del deportista en ambos sentidos** (y con ella el chat, las solicitudes y partes de Actividades), en vez de solo ocultar en Descubrir como decía la HU-42 | Es la forma de impedir mensajes y contacto sin modificar Matching ni Actividades: ambos piden esa tarjeta antes de cada operación. La alternativa (que cada servicio consulte `/blocks/check`) obliga a cambiar todos | `UserService.athlete_cards` |
| `GET /blocks/check` solo lo consulta una de las dos personas o un administrador, y responde solo `{"blocked": bool}` | Evitar que cualquier cuenta averigüe quién bloqueó a quién | `UserService.check_block` |
| Se silencian **personas**, no conversaciones | Una conversación directa es un match con una sola persona; no hay chats de grupo | `silencio_usuario` |
| `POST /block` responde 201 si es nuevo y 200 si ya existía; el cuerpo acepta `blockedUserId` y `blocked_user_id` | La HU pide camelCase; el resto del servicio es snake_case | `Ms_Users/app/api/v1/users.py` |
| **HU-52 se implementó en `ms-machine`, solo la parte de tiempo real (WebSockets). El push NO se construyó**: ya existe en el gateway, con Expo Push | El gateway ya tiene registro de dispositivos (`PUT /api/v1/notifications/devices`, tokens `ExponentPushToken[...]`), reintentos y recibos. Un adaptador FCM propio duplicaría ese sistema, usaría tokens que la app no genera y avisaría dos veces | `Ms_gateway/docs/notificaciones.md` |
| El socket se autentica con la primera trama, no con el token en la URL | Una URL con el token queda en los logs de acceso y en los proxies | `ms-machine/app/api/v1/realtime.py` |
| La conexión se cierra al vencer el token (4401) y la app reconecta con uno nuevo | Un socket abierto no debe seguir recibiendo mensajes de una sesión que ya no es válida (cuenta desactivada, token robado) | `realtime.py` |
| Una conversación abierta por conexión; hasta 5 conexiones por persona; 30 tramas por 10 s | Acotar el abuso y los recursos | `MAX_CONNECTIONS_PER_USER`, `MAX_FRAMES` |
| Quien tiene la conversación abierta recibe `new_message`; quien no, `unread_update` (nunca los dos) | Es la separación que pide la HU (sala de la conversación frente a canal personal) | `RealtimeHub.emit_to_conversation` / `emit_to_user` |
| El emisor recibe su propio `new_message` en sus otros dispositivos con el chat abierto, y nunca un `unread_update` | Mantener sincronizados sus dispositivos | `MatchingService._announce` |
| Un reintento de un mensaje ya guardado no se anuncia otra vez | Evitar duplicados en pantalla | `_created` en `PostgresMatchingRepository.send` |
| El cliente debe llamar a `PATCH /chats/{id}/read` al recibir `new_message` con el chat abierto; el servidor no marca leído por su cuenta | Mantener una sola regla de lectura (la de la HU-49) | `ms-machine/README.md` |
| El evento `new_message` lleva el mensaje en snake_case (igual que REST) y el resto en camelCase (`conversationId`) | El contrato de eventos pedido es camelCase; el objeto del mensaje ya lo consume la app | `realtime.py` |
| **Gateway: `GET /api/v1/users/blocks/check` no se expone a la app** | Es para que otros servicios consulten bloqueos con el token de una de las dos personas; la app no lo necesita, y exponerlo sin necesidad ofrece una vía más para sondear bloqueos | `Ms_gateway/gateway/app/api/routes/users.py` |
| Gateway: las páginas simuladas de Webpay (`/webpay/mock*`) se reenvían siempre, sin token | En desarrollo el navegador del usuario debe llegar a ellas; ms-club responde 404 salvo con `WEBPAY_MOCK_MODE=true`, y se prueba | `Ms_gateway/gateway/app/api/routes/clubs.py` |
| Gateway: el proxy reenvía `Location` solo en respuestas 301/302/303/307/308 | Sin eso, el retorno de Webpay (303 hacia la app) llegaba sin destino; reenviarlo en otras respuestas no tiene sentido | `forward_request` en `routes/users.py` |
| Gateway: en cada grupo de rutas, las fijas (`/me`, `/deportes`, `/notices/me`...) van antes que las que llevan identificador | Para que una pública como `/api/v1/clubs/{club_id}` no tape a una privada como `/api/v1/clubs/me` y se salte la comprobación de sesión del gateway | `Ms_gateway/gateway/app/api/routes/clubs.py` |
| Gateway: ms-club es opcional (sin `CLUBS_SERVICE_URL` responde 503 y la disponibilidad no lo espera) | Igual que los demás servicios; no obliga a tenerlo para arrancar | `Ms_gateway/gateway/app/core/config.py` |
| **Ubicación del club: fija, la define quien crea el club, y solo se muestra.** El usuario la ve y decide por sí mismo si postula; `ms-club` no ordena ni filtra por cercanía al usuario | Los campos son `direccion`, `comuna`, `provincia`, `latitud` y `longitud` (las dos últimas ya existen en tu tabla `club`; `comuna` y `provincia` son columnas nuevas de la migración 001, aún sin aplicar). La ubicación **móvil** del usuario se usa solo para recomendar usuarios cercanos al hacer match, en Ms_Users/Matching, no aquí. El filtro `zona` de clubes sigue siendo por texto | decisión de producto del 2026-10-14 |
| El club ve el `usuario_id` del deportista | Identifica al candidato entre postulaciones; Ms_Users ya lo expone en las tarjetas | `CandidatoRead` en `app/schemas/postulacion.py` |
| Al postular se valida la edad contra `edad_minima`/`edad_maxima` (409) | No estaba en la HU-33; evita postulaciones fuera de categoría. Solo aplica si hay rango y edad conocida | `app/services/postulacion_service.py` |
| El perfil deportivo se copia desde Ms_Users al postular (no se guarda solo un `sportsProfileId`) | Un `club_admin` no puede leer tarjetas de deportistas en Ms_Users; así el club ve el perfil y el deportista no puede inventarlo | `app/services/users_client.py` |
| Una convocatoria solo puede ser de un deporte que el club ofrece | Datos coherentes entre perfil y convocatorias | `app/services/convocatoria_service.py` |
| Un club inactivo no puede publicar (409) y sus convocatorias no se muestran | Coherente con el directorio, que oculta clubes inactivos | `convocatoria_service.py`, repositorios |
| Cada postulación no rechazada ocupa un cupo | La HU-33 pide validar cupos disponibles al postular | `create_postulacion` en los repositorios |
| Filtros de fecha de convocatorias en hora de Chile (`America/Santiago`) | Una prueba a las 23:30 debe aparecer en su día local | `app/schemas/convocatoria.py`, repositorios |
| Logo, portada y redes se guardan como URL `https://` | No hay servicio de archivos | `app/schemas/club.py` |

## 3. Datos del perfil de club que aún no existen

| Falta | Uso | Hoy |
| --- | --- | --- |
| `slug` en `club` | Perfil por URL legible (`/clubs/club-deportivo-nunoa`) | Solo por UUID |
| `region` en `club` | Filtro por región | `zona` busca en dirección, comuna y provincia |
| Tabla de fotos (galería) | Galería del perfil (HU-30) | No existe |

Cuando existan: agregar los campos a `StoredClub`, al repositorio PostgreSQL, al
mock, a los DTOs y a los tests.

## 4. Administración del club (HU-31)

- [ ] Subida de logo y portada: falta definir dónde se alojan los archivos
      (bucket/CDN) y quién los sube.
- [ ] Verificación de clubes: `estado_verificacion` existe (`pendiente` por
      defecto) pero no hay endpoint para que el equipo de SportMatch lo cambie.
      Ese endpoint sería para el rol `admin`, que no puede editar perfiles de club.
- [ ] Activar/desactivar el club (`is_active`): no hay endpoint; hoy solo se
      cambia en la base.
- [ ] El rol se lee del JWT. Si Ms_Users cambia el rol de una cuenta, el cambio
      rige cuando el token vence (30 min). `Ms_activities` consulta a Ms_Users en
      cada petición; evaluar si ms-club debe hacer lo mismo.
- [ ] Flujo de registro/alta de representantes de club en Ms_Users y en la app.
- [ ] `club.usuario_id` no tiene restricción única en la base; la regla de un club
      por representante la aplica el servicio. Si se quiere en la base, crear un
      índice único como `postgres` (antes, revisar que no haya duplicados).

## 5. Convocatorias (HU-32)

- [x] Postulación de deportistas (HU-33); `cupos_disponibles` descuenta las
      postulaciones no rechazadas.
- [ ] Editar o reabrir una convocatoria publicada: hoy solo se puede cerrar.
- [ ] `ConvocatoriaRead` muestra el club (`id`, `nombre`, `logo`) pero no su
      ubicación; si la app la necesita en la tarjeta, agregarla.
- [ ] La convocatoria no pasa a `CERRADA` sola al llenarse: sigue `ABIERTA` con
      `cupos_disponibles: 0` (y rechaza postulaciones con 409). Definir si debe
      mostrarse distinta en la app.

## 6. Postulaciones (HU-33)

- [x] Revisión por el club y aviso al deportista cuando cambia el estado (HU-34).
- [ ] Retirar una postulación: el deportista no puede hacerlo.
- [ ] Avisar al club cuando llega una postulación nueva (Actividades avisa al
      organizador; aquí no se hace). Sería otro evento en `clubs_api.notification_outbox`.
- [ ] El perfil copiado no se actualiza si el deportista edita su perfil en
      Ms_Users después de postular (es intencional; confirmar con el equipo).
- [ ] El control de cupos bloquea la fila de la convocatoria; se probó con tests y
      en PostgreSQL, pero no con postulaciones concurrentes reales.

## 7. Revisión de candidatos y notificaciones (HU-34)

- [ ] **Registrar el outbox de ms-club en el worker del gateway**: sin esto los
      eventos quedan en `clubs_api.notification_outbox` y no llegan a la bandeja
      ni al push. En `Ms_gateway/gateway/app/services/notifications.py`, agregar
      `(self.settings.clubs_database_url, 'clubs_api')` a `self.sources`; agregar
      `CLUBS_DATABASE_URL` a la configuración del gateway y a `compose.yaml`
      (conexión a `sportmatch_clubs` con el rol `sportmatch_clubs`, que ya tiene
      los permisos que el worker necesita). No se modificó Ms_gateway.
- [ ] Frontend: la navegación de notificaciones solo conoce `screen` `activity`,
      `chat`, `matches` y `profile`. Agregar `convocatoria` (con `convocatoriaId` y
      `postulacionId`) en `services/notificationNavigation.ts` para abrir la
      postulación al tocar el aviso; hoy el aviso queda en la bandeja sin destino.
- [ ] Pantalla del club para revisar candidatos y de "mis postulaciones" para el
      deportista (con `comentario_club`).
- [ ] La entrega de la notificación es "al menos una vez" (lo define el worker
      del gateway); el envío push real depende de `PUSH_ENABLED` y de la
      configuración móvil pendiente descrita en `Ms_gateway/docs/notificaciones.md`.
- [ ] Contactar al seleccionado: el club no tiene datos de contacto del deportista
      (solo su tarjeta pública). Definir cómo coordinan (chat, correo, datos que
      el deportista acepte compartir al ser aceptado).

## 8. Comunicados de clubes (HU-38)

- [ ] **El gateway debe leer el outbox de ms-club** (mismo punto de la sección 7):
      sin eso los comunicados se guardan y se ven en la bandeja de
      `/api/v1/clubs/notices/me`, pero no llegan a la bandeja de notificaciones
      ni al push.
- [ ] Frontend: agregar `screen: "comunicado"` (con `comunicadoId` y `clubId`) en
      `services/notificationNavigation.ts`, y las pantallas: el club redacta y ve sus
      comunicados; el deportista lee su bandeja. Hoy el aviso queda sin destino.
- [ ] **Seguidores o suscriptores**: la HU habla de "deportista suscrito". Cuando
      exista una tabla de seguimiento (o se defina `interesado_club`), `TODOS` debe
      incluirlos y quizás debería existir una audiencia propia (`SEGUIDORES`).
- [ ] Un deportista no puede silenciar a un club ni marcar comunicados como leídos
      en la bandeja propia (la lectura del aviso push la maneja el gateway).
- [ ] Sin límite de frecuencia ni protección contra doble envío: un doble clic del
      club publica y notifica dos veces. Evaluar un tope por club (por ejemplo, por
      hora) o una clave de idempotencia.
- [ ] Editar, retirar o programar un comunicado.
- [ ] El envío escribe una fila de outbox por destinatario en una sola transacción;
      está bien para cientos o pocos miles de postulantes. Con audiencias mucho
      mayores (seguidores masivos), pasar a envío por lotes.

## 9. Pagos con Webpay Plus (HU-40)

**Qué está verificado y qué no.** Con la API real de Transbank (entorno de integración,
credenciales públicas de prueba): crear una transacción devuelve el token de 64
caracteres y la URL del formulario, y confirmar sin haber pagado responde 422 (el
servicio lo traduce a 502 y deja la transacción `PENDING`). **No se ha probado un pago
completo**: eso exige pagar en el formulario de Webpay con una tarjeta de prueba, y la
lectura de una respuesta aprobada real está comprobada solo contra el formato que
describe la documentación. Tampoco se probó con dos conexiones simultáneas reales a
PostgreSQL; la exactitud de "una sola vez" se apoya en un `UPDATE ... WHERE status =
'PENDING'` y en un upsert atómico, verificados en PostgreSQL y por tests.

- [ ] **Probar un pago completo en integración**: `WEBPAY_MOCK_MODE=false`, iniciar un
      pago, pagar con una tarjeta de prueba de Transbank y confirmar; revisar la
      respuesta aprobada y la rechazada.
- [ ] **Los beneficios aún no se aplican**: "activar los beneficios" hoy es registrar la
      suscripción `ACTIVE`. Ningún servicio la consulta todavía. Falta definir qué
      desbloquea cada plan (publicar convocatorias, comunicados, más postulaciones...) y
      aplicarlo donde corresponda.
- [ ] **Catálogo real de planes**: hoy solo existen dos planes de prueba (`db/seeds`).
      Definir nombres, precios, duración, y si el precio incluye IVA.
- [ ] **Pagos que nunca se confirman**: si el usuario paga y se pierde el retorno (cierra
      la app, se cae la conexión, vence la sesión de 30 min), la transacción queda
      `PENDING` y la suscripción no se activa aunque Webpay haya autorizado el pago.
      Falta un proceso que consulte a Transbank el estado de las `PENDING` (la
      documentación indica 7 días de consulta) y las cierre. Confirmar con Transbank qué
      pasa con una transacción que nunca se confirma.
- [ ] **Credenciales y puesta en producción**: pedir el código de comercio y la API key de
      producción, guardarlos solo en el entorno del servidor, y revisar con Transbank los
      requisitos para pasar de integración a producción. `WEBPAY_PUBLIC_URL` debe ser
      `https` en producción.
- [x] Gateway: rutas de `/api/v1/payments` (públicas: `plans`, `webpay/return`, `webpay/mock*`; con token:
      `initiate`, `commit` y `subscription/me`) y reenvío de la redirección `303` del retorno de Webpay.
      Probado de punta a punta con el gateway y ms-club como servidores reales (15 de 15).
- [ ] `WEBPAY_PUBLIC_URL` de ms-club debe ser la dirección del gateway que alcanza el navegador del
      usuario, no la interna de Docker.
- [ ] Frontend: pantalla de planes; enviar al navegador a `url` con un formulario POST
      (`token_ws`), nunca en iframe; recibir el retorno (`sportmatchapp://pago?token_ws=...`
      o la página web) y llamar a `commit`; pantallas de comprobante, rechazo y
      cancelación. El `token_ws` vive 5 minutos antes de pagar y el JWT 30: si venció,
      volver a iniciar sesión antes de confirmar.
- [ ] Reembolsos y anulaciones: no hay forma de revertir un pago desde el servicio.
- [ ] Historial de pagos del usuario (`GET transactions/me`), correo o boleta de
      respaldo (la boleta electrónica del SII queda fuera), y aviso al usuario cuando su
      suscripción esté por vencer (se puede usar el outbox de notificaciones).
- [ ] Cambio de plan con una suscripción vigente (hoy responde 409) y renovación
      automática: Webpay Plus cobra una sola vez; para cobros recurrentes habría que usar
      otro producto de Transbank (OneClick).
- [ ] Si un usuario inicia pagos de dos planes distintos antes de pagar el primero y
      paga ambos, la suscripción queda con el plan del último pago aprobado y con los
      días de los dos. Es poco probable; se descartó bloquearlo para no rechazar un pago
      ya autorizado por Webpay.
- [ ] Doble clic en "pagar" crea dos transacciones `PENDING` en Webpay (inofensivo, no se
      cobra ninguna hasta que se confirma). Evaluar idempotencia o un límite de frecuencia
      por cuenta para no inundar a Transbank de transacciones.
- [ ] El servicio no pide a Ms_Users confirmar que el correo del deportista está
      verificado antes de cobrar; solo usa el rol del token.
- [ ] Retirar o unificar las tablas `plan`/`membresia`/`pago` de `sportmatch_users`
      (son de otro equipo) para que no queden dos modelos de suscripción.

## 10. Lista de chats con mensajes no leídos (HU-49, en `ms-machine`)

**Qué se probó.** Los 32 tests de `ms-machine` (14 existentes y 18 nuevos) pasan contra PostgreSQL 18
real, repetidos tres veces; romper a propósito ocho reglas del repositorio hizo fallar los tests las
ocho veces. El rol `sportmatch_users` de esta máquina no puede crear esquemas, así que se levantó una
instancia privada y temporal de PostgreSQL (puerto 55432, ya apagada y borrada). Para repetirlo:
`MATCHING_TEST_DATABASE_URL` con un rol que pueda crear esquemas. Detalle en `ms-machine/README.md`.

- [ ] **Avisar al dueño de `ms-machine` y llevar los cambios a la versión real.** Esa carpeta es un
      repositorio de GitHub de otra persona (`eritoD/ms-machine`, un solo commit «Mach»). **No se hizo
      commit ni push**: los cambios están sin confirmar en el árbol de trabajo (`README.md`,
      `app/main.py`, `app/repositories/postgres_matching_repository.py`, `app/schemas/matching.py`,
      `app/services/matching_service.py`, y nuevos `app/api/v1/chats.py`, `app/schemas/chats.py`,
      `db/migrations/004_message_read_state.sql`, `tests/test_chats.py`). Además esta copia **no
      parece ser la vigente**: no trae la migración `003_notification_outbox` que documenta
      `Ms_gateway/docs/notificaciones.md`, y el `compose.yaml` del gateway construye `../Ms_Matching`,
      una carpeta que no existe aquí. Hay que aplicar estos cambios sobre la versión real y revisar
      conflictos.
- [x] **Gateway**: `GET /api/v1/chats`, `GET/POST /api/v1/chats/{id}/messages` y
      `PATCH /api/v1/chats/{id}/read` se reenvían a Matching, con token.
- [ ] **Frontend**: la pestaña de chats arma su lista con `/matching/state`. Cambiarla a
      `GET /api/v1/chats` para mostrar el último mensaje real y la insignia con `unreadCount`, y
      refrescarla al volver de abrir un chat.
- [ ] No hay un endpoint con el total de no leídos para la insignia de la pestaña: hoy se suma en el
      cliente con la lista (hasta 100 chats por página).
- [ ] **Chats de grupo (`GROUP` en la HU)**, de actividades y de clubes: no existen; la lista solo tiene
      conversaciones entre dos deportistas con match. Si se hacen, crear el `ms-chat` con las tablas de
      la HU y migrar los mensajes de Matching; el router `/chats` ya está separado para facilitarlo.
- [ ] El acuse de lectura (`is_read` / `read_at` en los mensajes que envías) es visible para quien los
      envió. Decidir si se muestra en la app y si cada persona puede desactivarlo (privacidad).
- [ ] La bandeja de notificaciones del gateway y `read_at` son independientes: leer un chat no marca
      como leída la notificación de ese mensaje.
- [ ] Rendimiento: `unreadCount` se calcula con una subconsulta por conversación y un índice parcial
      (`messages_unread`). Revisar con volumen real de mensajes.

## 11. Ubicación, imágenes y archivos en el chat (HU-50, en `ms-machine`)

**Qué se probó.** Los 137 tests de `ms-machine` pasan contra PostgreSQL 18 real (instancia privada y
temporal, ya apagada y borrada), repetidos; romper a propósito ocho reglas de seguridad de los enlaces y de
los mensajes hizo fallar los tests las ocho veces. Los tests encontraron además un error mío (los chats sin
mensajes mostraban «Ubicación» como último mensaje), ya corregido.

- [ ] **Falta el almacenamiento de archivos**: nadie sube todavía las imágenes ni los archivos. Mientras
      `CHAT_ATTACHMENT_HOSTS` esté vacío, IMAGE y FILE responden 503; la ubicación y el texto sí funcionan.
      Hay que elegir dónde se guardan (bucket o CDN), cómo los sube la app (idealmente URLs firmadas de
      subida, con tope de tamaño) y qué host de descarga se autoriza en esa variable, en el `.env` y en el
      `compose.yaml` de `ms_matching`.
- [ ] **El servidor no mira el contenido del archivo**: solo valida el enlace (https, host, extensión). Que
      sea realmente una imagen, su tamaño y un posible análisis de virus deben hacerse al subirlo.
- [x] **Gateway**: `POST /api/v1/chats/{id}/messages` se reenvía (junto con las rutas de la HU-49).
      Falta pasar `CHAT_ATTACHMENT_HOSTS` al servicio Matching en el `compose.yaml`.
- [ ] **Frontend**: `services/chat.ts` no conoce la ruta nueva ni los campos nuevos. Faltan el botón de
      adjuntar, el selector y la vista de ubicación (abrir el punto en un mapa), mostrar imágenes y archivos,
      y pedir el permiso de ubicación del dispositivo.
- [ ] **Privacidad de la ubicación**: se guardan las coordenadas exactas (6 decimales) dentro del chat y
      quedan para siempre; no hay borrado de mensajes ni expiración. Definir una política de retención
      (datos personales, Ley 21.719) y si hay que avisar a la persona antes de compartir su posición.
- [ ] Solo hay puntos fijos: no hay ubicación en vivo ni edición o borrado de mensajes.
- [ ] La versión real de Matching genera notificaciones por mensaje (esta copia no): revisar que un mensaje
      sin texto no se envíe con el cuerpo vacío (usar «Imagen», «Archivo» o «Ubicación»).
- [ ] No se aceptan video, audio ni HEIC/HEIF (el formato habitual del iPhone): convertir en el cliente o
      ampliar la lista.

### Error corregido en tres servicios: números infinitos devolvían 500

Un número como `1e999` llega a la validación como infinito, y el mensaje de error que repite el valor no se
puede convertir a JSON: el servicio respondía **500 en lugar de 422/400**, para cualquier usuario autenticado.

- [x] **ms-machine** (HU-50): corregido, con test.
- [x] **ms-club** (la HU-31 lo traía en `latitud`/`longitud`): corregido, con test.
- [x] **Ms_Users**: corregido con el mismo manejador (`app/main.py`), con test (`tests/test_validation_errors.py`).
      Se confirmó el defecto antes: `PUT /api/v1/users/{id}/preferences` con `zona.latitud`: `999` daba 422 y
      `1e999` daba 500. Ahora `1e999`, `-1e999`, `Infinity`, `-Infinity` y `NaN` dan 422.
- [x] **Ms_activities** y el **gateway** revisados: Actividades solo tiene un decimal en una respuesta
      (`average`), no en una entrada, y el gateway reenvía los cuerpos sin interpretar sus números. No tienen el defecto.

## 12. Bloquear y silenciar (HU-51, en `Ms_Users`)

**Qué se probó.** 179 tests de `Ms_Users` pasan contra PostgreSQL 18 real (168 con datos en memoria),
rompiendo a propósito 10 reglas: los tests fallaron en 9 y la décima es una guarda sin efecto observable
(`blocked_between(a, a)` ya da falso porque nadie puede bloquearse a sí mismo). Además se probó **entre servicios reales**:
Matching (con su chat, en PostgreSQL) contra un `Ms_Users` en ejecución. Con un bloqueo, ninguna de las dos
personas puede escribir, leer ni enviar solicitudes, el chat sale de sus listas, y al desbloquear vuelve
con su historial y su contador (18 de 18 comprobaciones). **No se probó Actividades**: su comportamiento
con un bloqueo se dedujo leyendo su código.

- [ ] **Confirmar el cambio de comportamiento con producto.** Antes un bloqueo solo ocultaba en Descubrir y
      dejaba intactos matches, chats y actividades; ahora corta el contacto. Efecto en Actividades (por
      lectura del código): las actividades de la otra persona desaparecen de los listados y su detalle
      responde 404, no se puede aceptar, calificar ni ver a esa persona en una actividad compartida, y
      desaparecen de las listas de participantes. Para volver a lo anterior, quitar el filtro
      `blocked_with` de `UserService.athlete_cards`. Se actualizó un test de la HU-42 que fijaba la regla vieja.
- [x] **Gateway**: se agregaron `POST /block`, `DELETE /block/{id}`, `GET /blocks/me` y
      `GET/PATCH /chat-preferences`. `/blocks/check` es para otros servicios y a propósito **no** se expone a la app
      (el gateway responde 404). La app puede seguir con `/me/blocks`, que ya corta el contacto.
- [ ] **El silencio todavía no apaga ninguna notificación**: hoy solo se guarda la lista. Para que surta
      efecto, el worker de notificaciones del gateway (su bandeja vive en la base de Usuarios) debe omitir el
      aviso y el push de mensajes de personas silenciadas, y para eso el evento de mensaje debe llevar quién
      lo envió (hoy lleva solo el match). Los bloqueados no necesitan esto: no pueden enviar.
- [ ] **El club no está cubierto**: las postulaciones, los comunicados y la revisión de candidatos de
      ms-club no consultan bloqueos. Un deportista que bloqueó a un representante sigue recibiendo sus
      comunicados y el representante sigue viendo su perfil copiado. Decidir si ms-club debe llamar a
      `/blocks/check` (requiere el token de una de las dos personas).
- [ ] El mensaje que ve la persona cuando el chat «desaparece» es genérico («Deportista no disponible…»); a
      propósito no revela el bloqueo. Falta que la app lo muestre con un texto claro.
- [ ] Frontend: botones para bloquear y silenciar dentro del chat, y pantallas con las listas de bloqueados y
      silenciados (hoy solo existe el bloqueo desde el perfil).
- [ ] Aplicar la migración `007_chat_mutes.sql` donde corra `Ms_Users`: `users_migrate` de Compose o
      `python -m app.database.migrate` la ejecutan; el rol `sportmatch_users` de esta máquina no puede
      crear tablas en `users_db`/`sportmatch_users`, así que no se aplicó aquí.
- [ ] No hay un tope de personas bloqueadas ni silenciadas por cuenta.
- [ ] Silenciar conversaciones de grupo o por actividad: no existen esos chats.

## 13. Tiempo real y push de mensajes (HU-52, en `ms-machine`)

**Qué se probó.** Los 166 tests de `ms-machine` pasan contra PostgreSQL 18 real (29 son nuevos). Se
rompieron a propósito 12 reglas del tiempo real y los tests fallaron en 12 de 12. Además se probó con un
**servidor uvicorn real y un cliente WebSocket real**: autenticación, eventos a la conversación abierta y al
destinatario que está en otro lado, contadores, 50 mensajes en orden y desconexiones; un mensaje llega
unos 30 ms después de guardarse (11 de 11 comprobaciones). **No se probó con la app ni a través del
gateway**, que todavía no reenvía WebSockets.

**Decisión necesaria sobre el push** (lo que pide la HU y no se construyó, y por qué):

- [ ] **FCM o Expo.** La HU pide Firebase Cloud Messaging; el sistema que ya funciona usa Expo Push y la app
      obtiene tokens de Expo. Construir FCM en Matching duplicaría la bandeja, los dispositivos y los
      reintentos, y el usuario recibiría cada aviso dos veces. **Recomendación: usar el existente.**
- [ ] **Extracto del mensaje en el push.** La HU pide título con el nombre del emisor y cuerpo con un
      extracto o «Te ha enviado una imagen/ubicación». `Ms_gateway/docs/notificaciones.md` fija lo contrario
      a propósito: no se envía el texto privado del chat para que no se vea en pantalla bloqueada. Decidir si
      cambia esa política; cambiarla es tarea de la versión real de Matching (donde está el disparador de
      mensajes) y del gateway, no de esta copia.
- [ ] **Datos del push**: la app abre el chat al tocar un aviso con `screen: "chat"` y `matchId`. La HU pide
      `{conversationId, type: "CHAT_MESSAGE"}`; hay que elegir uno y revisar lo que emite la versión real de
      Matching (esta copia no tiene el disparador de notificaciones de mensajes).
- [ ] **No avisar de personas silenciadas** (HU-51): sigue sin aplicarse. El evento de mensaje debe llevar al
      emisor para que el gateway lo omita; la lista ya existe en `silencio_usuario`. Bloqueados no hace falta:
      no pueden enviar.
- [ ] **No enviar push a quien está mirando el chat.** Hoy el push sale aunque la persona tenga el chat
      abierto, porque la bandeja de notificaciones y la lectura de mensajes son independientes y el gateway no
      conoce la presencia. Opciones: que la app marque la notificación como leída al recibir `new_message`
      (rápido, tiene 2 s de margen), o que el gateway consulte `RealtimeHub.is_active` (necesita exponerlo).

**Para que la app pueda usarlo:**

- [ ] **Gateway**: reenviar `wss://…/api/v1/chats/ws`. Hoy solo reenvía HTTP (con `httpx`) y Matching no está
      publicado fuera de la red de Docker. Hace falta un proxy WebSocket bidireccional (y la librería
      `websockets` en la imagen del gateway) o publicar Matching; lo primero es lo recomendable.
- [ ] **Frontend**: cliente WebSocket con reconexión espaciada, token nuevo en cada conexión, `join`/`leave`
      según la pantalla, actualizar la insignia con `unread_update`, y volver a cargar `GET /api/v1/chats` al
      reconectar (los eventos perdidos no se reenvían). La app sigue consultando cada 4 s mientras tanto.
- [ ] Reconstruir la imagen de Matching: se agregó `websockets` a `requirements.txt`.

**Límites conocidos de esta versión:**

- [ ] **Una sola réplica.** Los sockets están en la memoria del proceso. Con varias réplicas, un evento solo
      llega a quienes están conectados a la que guardó el mensaje; hace falta `LISTEN/NOTIFY` de PostgreSQL o Redis.
- [ ] `totalUnread` cuenta todas las conversaciones activas, también las de personas bloqueadas o desactivadas
      que la lista oculta, así que la insignia puede ser mayor que la suma de la lista.
- [ ] No hay evento de «el otro leyó» (acuse en vivo), ni «escribiendo…».
- [ ] Un cliente puede abrir muchas conexiones sin autenticar (cada una dura 5 s): falta un límite por IP,
      mejor en el gateway.
- [ ] Sin métricas de conexiones abiertas, eventos enviados ni clientes soltados.

## 14. Filtro por deporte

- [x] `GET /api/v1/clubs/deportes` lista el catálogo y se puede filtrar por `deporte_id`.
- [ ] La app usa códigos (`futbol`, `tennis`) en Ms_Users y la tabla `deporte` usa
      nombres (`Fútbol`, `Tenis`): `tennis` no encuentra `Tenis`. La app debería
      usar el catálogo de ms-club para los filtros de clubes y convocatorias.

## 15. Integración con el resto del backend

- [x] `Ms_gateway`: rutas hacia `/api/v1/clubs`, `/api/v1/payments` y `/api/v1/chats`, `CLUBS_SERVICE_URL`
      (en `.env.example`), reenvío de `Authorization` y de la redirección `3xx`. Públicas: directorio, perfil,
      deportes, sección de convocatorias y su detalle, planes y el retorno de Webpay; el resto exige token. Esto no
      depende de `AUTH_REQUIRED`, que solo valida la configuración de la clave. 124 tests del gateway pasan
      (54 nuevos); romper a propósito 9 reglas de las rutas hizo fallar los tests las 9 veces.
- [x] `Ms_gateway/compose.yaml`: servicio `ms_club` bajo el perfil `clubs` (`docker compose --profile clubs up`), que **no**
      es dependencia del gateway (si no arranca, el resto de la app sigue). Variables nuevas en `.env`: `CLUBS_SERVICE_URL=http://ms_club:8004`,
      `CLUBS_DB_PASSWORD`, `WEBPAY_PUBLIC_URL`, `WEBPAY_FRONTEND_RETURN_URL` (página del sitio web) y, para adjuntos del chat,
      `CHAT_ATTACHMENT_HOSTS`. Antes hay que crear la base y el rol (`ms-club/db/00_create_database.sql`) y aplicar las migraciones 001 a 006.
      Validado con `docker compose config` (sin imágenes construidas ni contenedores levantados).
- [x] **Worker de notificaciones**: lee también `clubs_api.notification_outbox` si se define `CLUBS_DATABASE_URL` (rol `sportmatch_clubs`).
      Probado contra PostgreSQL real (llega a la bandeja de la persona correcta y un reintento no duplica).
- [ ] **Tests viejos de notificaciones**: 8 fallan con PostgreSQL real porque `ms-machine/db/migrations` no trae la `003` (la de
      `notification_outbox`, que sí existe en el Ms_Matching real). No es de este cambio; confirma que lo reemplace la versión real.
- [x] **Proxy de WebSocket**: `wss://…/api/v1/chats/ws` se reenvía (22 tests con un Matching falso; 6 reglas rotas a propósito,
      detectadas). Probado con gateway y ms-machine reales (7/7: sitio web, app sin Origin, origen ajeno, token vencido). Falta reconstruir la imagen del gateway (nueva dependencia `websockets`).
- [ ] **Ms-club es para el sitio web** (nota de producto): `WEBPAY_FRONTEND_RETURN_URL` debe ser una página del sitio
      (no `sportmatchapp://pago`), y el origen del sitio debe estar en `CORS_ALLOWED_ORIGINS` del gateway. El chat es para app y web.
- [ ] **Pruebas del gateway en esta máquina**: faltaban `pydantic-settings` y `cryptography` en el Python global,
      así que los tests del gateway no corrían (8 errores al recolectar). Se probó en un entorno virtual aparte con
      las versiones de `requirements.txt`; instalar esas dependencias para correrlos. 9 tests de notificaciones
      se omiten porque necesitan `NOTIFICATIONS_TEST_ADMIN_URL`.
- [ ] Colección de Postman, como la de `Ms_Users/postman`.
- [ ] Frontend: pantallas de directorio, perfil, panel del club, convocatorias y
      postulaciones (la app aún no tiene roles de club).

## 16. Fuera de las HU-30/31/32/33/34/38/40/49/50/51/52 (para siguientes historias)

- `interesado_club`: existe en la base y el servicio no la usa.
- `membresia` y `pago`: según `Ms_Users/db/README.md` pertenecen a este servicio. La HU-40
  creó `transaccion_pago` y `suscripcion` como reemplazo propio; las `membresia`/`pago`
  de la base `sportmatch_users` siguen sin tocar (ver la sección 9).
