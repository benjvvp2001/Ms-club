# Dudas sobre tablas e información necesaria

Todo lo que no pude resolver con el código ni con las bases durante las historias de usuario HU-30 a HU-52
(clubes, convocatorias, postulaciones, comunicados, pagos, chat, bloqueos, tiempo real y ubicación). Cada duda
dice **qué asumí mientras tanto**, **por qué importa** y deja un espacio **Respuesta** para completar.

Este documento solo reúne las preguntas. El seguimiento de lo que falta construir está en
[PENDIENTES.md](PENDIENTES.md), y lo ya implementado en el [README](README.md).

Cómo leerlo: las dudas de la sección 1 condicionan a todas las demás, conviene responderlas primero.

---

## 0. Qué hay hoy en las bases (verificado en PostgreSQL 18, solo lectura)

| Base | Tablas que ya existían en `public` |
| --- | --- |
| `sportmatch_users` | `usuario`, `rol`, `usuario_deporte`, `preferencia_usuario`, `disponibilidad`, `consent`, `audit_events`, `notificacion`, `plan`, `membresia`, `pago` |
| `sportmatch_matching` | `match`, `solicitud_match`, `calificacion_usuario`, `ubicacion_usuario` (con coordenadas y columna geoespacial), `deporte`, y las de PostGIS (`spatial_ref_sys`) |
| `sportmatch_activities` | `actividad`, `inscripcion_actividad`, `comunidad`, `miembro_comunidad`, `deporte` |
| `sportmatch_clubs` | `club` (id, usuario_id, nombre, descripcion, email_contacto, telefono, direccion, latitud, longitud, logo, is_active, fecha_creacion), `club_deporte`, `interesado_club`, `deporte` |

- Roles: solo existen `postgres` y `sportmatch_users`. No existen los de matching, activities ni clubs.
- Ninguna de las cuatro bases tiene tablas de los servicios nuevos (`matching_api.*`, `activities_api.*`,
  `clubs_api.*`, convocatorias, postulaciones, comunicados, pagos de clubes, `ubicacion_actual`…).
- `sportmatch_users` tiene 0 usuarios y no tiene las migraciones 005 a 008 de Ms_Users.
- Un intento de crear roles y aplicar migraciones se **deshizo por completo** a pedido de quien dirige el proyecto.

---

## 1. Preguntas que condicionan todo lo demás (bases y nombres)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 1.1 | **¿Adaptamos el código a las tablas que ya existen o aplicamos las migraciones de cada servicio?** | `ms-club` ya se adaptó a `club` y `club_deporte`. Matching y Actividades usan tablas propias (`matching_api.*`, `activities_api.*`), no `match`/`solicitud_match` ni `actividad`/`inscripcion_actividad`. | Decide si hay que reescribir los repositorios de Matching y Actividades. Las tablas antiguas tienen otra forma (otros nombres de columna y de estado). | |
| 1.2 | ¿Quién crea los roles de cada servicio y con qué permisos? | Un rol por servicio (`sportmatch_clubs`, `sportmatch_matching`, `sportmatch_activities`) con permisos mínimos, sin usar `postgres`. | Hoy los servicios no pueden conectarse. Además las tablas antiguas pertenecen a `postgres`, y las migraciones que agregan columnas deben correrse con ese usuario. | |
| 1.3 | ¿Cuál es el nombre correcto de las bases y los roles: `sportmatch_*` o `sportmach_*`? | Los README y `.env.example` de varios servicios dicen `sportmach_*` (sin la "t"), y `Ms_Users` apunta a `users_db`. Usé `sportmatch_*`, que es lo que existe. | Un nombre equivocado impide la conexión y hay que corregir `.env.example`, README y `compose.yaml`. | |
| 1.4 | La tabla `deporte` está **repetida en tres bases** (matching, activities, clubs). ¿Cuál es la fuente de verdad? | `ms-club` usa la de su propia base. | La app usa códigos (`futbol`, `tennis`) y `deporte` guarda nombres (`Fútbol`, `Tenis`): el filtro por deporte no coincide (`tennis` no encuentra `Tenis`). Hay que unificar el catálogo y su clave. | |
| 1.5 | ¿Cómo se llaman realmente las carpetas de los servicios? | `ms-machine` es Matching, pero `compose.yaml` del gateway construye `../Ms_Matching` y `../Ms_Activities`, que no existen con esos nombres aquí (la de Actividades se llama `Ms_activities`). | `compose.windows.yaml` lo corrige solo en esta máquina. Habría que fijar un nombre único. | |
| 1.6 | ¿`ms-machine` es la versión vigente de Matching? | Es una copia atrasada de un repositorio de otra persona: no tiene la migración `003_notification_outbox` y su gateway espera otro nombre de carpeta. No hice commit ni push. | Los cambios del chat (HU-49, 50 y 52) hay que llevarlos a la versión real y revisar conflictos. Se pierde tiempo si se trabaja sobre la copia equivocada. | |

---

## 2. Clubes: perfil y administración (HU-30, HU-31)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 2.1 | ¿Podemos **agregar columnas a la tabla `club`**? La HU-31 necesita `historia`, `comuna`, `provincia`, `sitio_web`, `redes_sociales`, `portada`, `estado_verificacion` y `fecha_actualizacion`. | Sí, con la migración `001` (idempotente). Hoy no está aplicada. | Si `club` no se puede alterar, el perfil debe vivir en una tabla aparte y cambian el repositorio y las consultas. | |
| 2.2 | ¿La **dirección del club** se escribe como texto libre o se elige de un catálogo? ¿De dónde salen `comuna` y `provincia`? | El creador del club la escribe a mano: `direccion`, `comuna`, `provincia`, `latitud` y `longitud` (esas dos últimas, juntas o ninguna). | Si existe un catálogo de comunas, lo correcto es validar contra él. Hoy cada servicio repite la misma regla de formato. | |
| 2.3 | ¿Las **coordenadas del club** se calculan desde la dirección (geocodificación) o las ingresa el creador? | A mano, por ahora. | Sin geocodificar, la mayoría de los clubes quedará sin coordenadas y no se podrán mostrar en un mapa. | |
| 2.4 | ¿Qué es exactamente **`interesado_club`**? | No la usé. Podría ser "seguidores" o "personas que pidieron información". | Es la candidata para los suscriptores de comunicados (ver 4.1). No sé qué columnas tiene ni cómo se llena. | |
| 2.5 | ¿Hay una restricción de **un club por representante** (`club.usuario_id` único)? | La regla la aplica el servicio, no la base. | Si se quiere en la base, hay que crear un índice único como `postgres`, revisando antes que no haya duplicados. | |
| 2.6 | ¿Quién cambia `estado_verificacion` y cuándo un club queda "verificado"? | Nace en `pendiente`; no hay endpoint para cambiarlo. El rol `admin` no puede editar perfiles. | Hay que definir el proceso y qué muestra la app mientras tanto. | |
| 2.7 | ¿Cómo se **activa o desactiva** un club (`is_active`)? ¿Quién? | Solo se cambia a mano en la base. | Un club inactivo se oculta en el directorio, no puede publicar convocatorias ni comunicados. | |
| 2.8 | ¿Dónde se guardan **logo y portada** y quién los sube? | Solo se aceptan como URL `https://`. No hay servicio de archivos. | Mismo faltante que los adjuntos del chat (ver 6.5): conviene un solo almacenamiento. | |
| 2.9 | ¿Existen `slug`, `region` y galería de fotos para el club? | No existen; el perfil se identifica por UUID y la zona se busca en dirección, comuna y provincia. | La HU-30 habla de perfil público: una URL legible exige `slug`. | |
| 2.10 | ¿Cómo se crea una cuenta **`club_admin`**? | El registro siempre asigna `player`; para probar hay que cambiar el rol a mano en la base de Users. | Sin un alta de representantes nadie puede crear un club. | |
| 2.11 | ¿Es válido el rol que viene en el **token** hasta que venza (30 min), o debe consultarse a Ms_Users en cada petición? | Se confía en el token (Actividades sí consulta cada vez). | Un cambio de rol o una cuenta desactivada tardaría hasta 30 minutos en notarse. | |

---

## 3. Convocatorias y postulaciones (HU-32, HU-33, HU-34)

No existe ninguna tabla antigua equivalente: `convocatoria` y `postulacion_convocatoria` son nuevas.

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 3.1 | ¿Hay un **catálogo de categorías de edad** ("Sub-17", "Sub-15"...) o es texto libre? | Texto libre, junto con `edad_minima` y `edad_maxima`. | Un catálogo permitiría filtrar mejor. | |
| 3.2 | ¿Qué debe pasar con la **edad** al postular? | Si la convocatoria tiene rango y se conoce la edad del deportista, fuera de rango responde 409. Si no se conoce la fecha de nacimiento, se permite. | No lo pedía la HU. ¿Se bloquea o se avisa? | |
| 3.3 | ¿Qué cuenta como **cupo ocupado**? | Toda postulación no rechazada. | Si solo contaran las aceptadas, se podrían recibir más postulaciones que cupos. | |
| 3.4 | ¿La convocatoria debe **cerrarse sola** al llenarse o a la fecha de la prueba? | Sigue `ABIERTA` con `cupos_disponibles: 0` y rechaza con 409. | Define cómo la muestra la app. | |
| 3.5 | ¿El **perfil deportivo** del postulante se copia o se consulta cada vez? | Se copia al postular, porque un `club_admin` no puede leer tarjetas de deportistas en Ms_Users. | Si el deportista edita su perfil después, el club ve el dato viejo. ¿Es lo esperado? | |
| 3.6 | ¿Qué datos de **contacto** puede ver el club de alguien aceptado? | Solo la tarjeta pública y el `usuario_id`. | El club no tiene cómo coordinar con el seleccionado. ¿Chat, correo, o datos que el deportista acepte compartir? | |
| 3.7 | ¿Se avisa al club cuando **llega una postulación**? | No. Solo se avisa al deportista cuando el club cambia su estado. | Actividades sí avisa al organizador; aquí no. | |
| 3.8 | ¿El deportista puede **retirar** su postulación? ¿Y editar o reabrir una convocatoria? | No se puede en ninguno de los dos casos. | La HU no lo pedía; falta definir si es necesario. | |
| 3.9 | El estado `EN_REVISION` de la HU-33 pasó a llamarse **`PRESELECCIONADO`** (HU-34). | La migración 004 convierte las filas existentes. | Confirmar que los dos nombres son el mismo estado, y que "salir de rechazado" vuelve a ocupar un cupo. | |
| 3.10 | ¿El aviso al deportista puede incluir el **comentario del club**? | No: la alerta se ve en pantalla bloqueada, y el comentario se lee dentro de la app. | Es una decisión de privacidad. | |

---

## 4. Comunicados de clubes (HU-38)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 4.1 | La HU habla de "deportistas **suscritos**" al club. **¿Existe una tabla de seguidores?** | No hay. La audiencia se calcula con las postulaciones: `POSTULANTES` = pendientes y preseleccionados; `MIEMBROS` = aceptados; `TODOS` = ambos. Nunca los rechazados. | Si hay seguidores (¿`interesado_club`?), `TODOS` debería incluirlos y podría haber una audiencia `SEGUIDORES`. | |
| 4.2 | ¿Los comunicados se pueden **editar, retirar o programar**? | No. El rol del servicio ni siquiera tiene UPDATE/DELETE sobre ellos. | Un error de redacción hoy no se puede corregir. | |
| 4.3 | ¿Hay un **límite de frecuencia**? | No. Un doble clic publica y notifica dos veces. | Evita saturar a los deportistas. | |
| 4.4 | ¿Cuánto del texto puede mostrar la alerta push? | Los primeros 120 caracteres. | Misma política de privacidad que el chat (ver 6.9). | |
| 4.5 | ¿Una persona puede **silenciar a un club**? | No. | Mismo mecanismo que el silencio entre personas, pero con clubes. | |

---

## 5. Pagos con Webpay Plus (HU-40)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 5.1 | **Ya existen `plan`, `membresia` y `pago` en `sportmatch_users`.** ¿Son los vigentes? | No los usé. Creé `plan_suscripcion`, `transaccion_pago` y `suscripcion` en `ms-club`. Los README de Ms_Users se contradicen sobre si esas tablas siguen en uso. | Habría dos modelos de suscripción. Si las antiguas son las vigentes, hay que reescribir los pagos sobre ellas (y entonces no vivirían en `ms-club`). | |
| 5.2 | ¿Cuáles son los **planes reales**: nombres, precios, duración, y si el precio incluye IVA? | Dos planes de prueba (`db/seeds`). | Sin catálogo no se puede cobrar. | |
| 5.3 | ¿Qué **beneficios** desbloquea cada plan? | Activar los beneficios es solo registrar la suscripción `ACTIVE`. Ningún servicio la consulta. | Sin esto el pago no cambia nada en la app. | |
| 5.4 | ¿Se puede **cambiar de plan** a mitad de periodo? ¿Cómo se prorratea? ¿Hay renovación automática? | Con otro plan vigente, `initiate` responde 409. Webpay Plus cobra una sola vez; el cobro recurrente exige otro producto (OneClick). | Hoy cambiar de plan es imposible hasta que venza. | |
| 5.5 | ¿Un comprador puede tener **más de una suscripción**? ¿Un club y un deportista a la vez? | Una por comprador; el deportista compra planes `USUARIO` y el `club_admin`, planes `CLUB`. | Define la clave de la tabla `suscripcion`. | |
| 5.6 | ¿Qué hacer con los **pagos que nunca se confirman** (se pierde el retorno)? | Quedan `PENDING`; la suscripción no se activa aunque Webpay haya autorizado. | Hay que decidir si se consulta a Transbank (su documentación habla de 7 días) y quién lo ejecuta. | |
| 5.7 | ¿Cuáles son las **credenciales de producción** y los requisitos de Transbank para pasar de integración a producción? | Se usan las credenciales públicas de prueba en `INTEGRATION`. | No se puede salir a producción sin ellas. Además `WEBPAY_PUBLIC_URL` debe ser `https`. | |
| 5.8 | ¿**A qué página web vuelve el usuario** después de pagar? | El retorno redirige a `WEBPAY_FRONTEND_RETURN_URL`. El ejemplo que dejé es el deep link de la app (`sportmatchapp://pago`). | Se aclaró que `ms-club` es para el sitio web: debe ser una página del sitio, y su origen debe estar en `CORS_ALLOWED_ORIGINS` del gateway. | |
| 5.9 | ¿Hay **reembolsos**, historial de pagos, boleta o aviso de vencimiento? | Nada de eso existe. | La boleta electrónica del SII queda fuera. | |
| 5.10 | ¿Se debe exigir **correo verificado** antes de cobrar? | Solo se usa el rol del token. | Ms_Users ya sabe si el correo está verificado. | |
| 5.11 | ¿Qué datos de la tarjeta pueden guardarse? | Solo los últimos 4 dígitos. | Mínimo para el comprobante. | |

---

## 6. Chat: lista, ubicación, archivos y tiempo real (HU-49, HU-50, HU-52)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 6.1 | ¿En qué servicio debe vivir el chat? | En Matching (`ms-machine`), por decisión tuya. Una conversación es un match aceptado (`matching_api.requests`) y los mensajes son `matching_api.messages`. | La HU pedía tablas nuevas (`conversations`, `conversation_participants`, `messages`). No las creé para no duplicar el chat. | |
| 6.2 | **La base ya tiene `match` y `solicitud_match`.** ¿Son el origen de los matches y mensajes? | No. El chat usa `matching_api.requests` y `matching_api.messages`, que no tienen relación con las tablas antiguas. | Mismo asunto que 1.1: si las antiguas son las vigentes, el chat hay que reescribirlo sobre ellas. Las antiguas no tienen mensajes. | |
| 6.3 | ¿Existirán **chats de grupo** (actividades, clubes, comunidades)? | No. Hoy solo hay chat entre dos deportistas con match. Existen `comunidad` y `miembro_comunidad` en la base de actividades, pero ningún chat. | Si habrá grupos, conviene crear un `ms-chat` con las tablas de la HU y migrar. | |
| 6.4 | ¿La persona que envía un mensaje puede ver que fue **leído** (acuse con hora)? ¿Se puede desactivar? | Se expone `is_read` y `read_at` al emisor. | Es un asunto de privacidad. | |
| 6.5 | **¿Dónde se guardan las imágenes y archivos adjuntos?** | Se reciben como enlace `https` a hosts de la lista `CHAT_ATTACHMENT_HOSTS`. Con la lista vacía, responden 503. | Nadie sube todavía los archivos: hay que elegir un almacenamiento (bucket/CDN), cómo se suben (URLs firmadas, tamaño máximo) y qué host se autoriza. También sirve para el logo y la portada del club (2.8). | |
| 6.6 | ¿Se valida el contenido del archivo (que sea imagen, tamaño, virus)? ¿Se aceptan video, audio y HEIC (iPhone)? | El servidor solo valida el enlace. No se aceptan svg, html, ejecutables, comprimidos ni HEIC. | HEIC es el formato habitual del iPhone; si no se acepta, hay que convertir en el cliente. | |
| 6.7 | ¿Cuánto tiempo se conservan las **coordenadas exactas** enviadas en un mensaje? | Se guardan con 6 decimales, para siempre. No hay borrado ni expiración. | Dato personal sensible (Ley 21.719): falta una política de retención y decidir si se avisa antes de compartir la posición. | |
| 6.8 | ¿**FCM (Firebase) o Expo Push** para las notificaciones de mensajes? | No construí el push. Recomiendo usar el sistema de Expo que ya existe en el gateway. | La HU pide FCM, pero hacerlo en Matching duplicaría bandeja, dispositivos y reintentos, y la persona recibiría cada aviso dos veces. | |
| 6.9 | ¿El push puede mostrar un **extracto del mensaje**? | No. Hoy el gateway no envía el texto privado, a propósito. | La HU pide lo contrario. Decide si cambia esa política. | |
| 6.10 | ¿Qué datos lleva el push: `{conversationId, type: "CHAT_MESSAGE"}` (como pide la HU) o `screen: "chat"` y `matchId` (como usa la app)? | No elegí. | La app abre el chat con `screen` y `matchId`. | |
| 6.11 | ¿Se envía push a quien **tiene el chat abierto**? | Hoy sí: la bandeja de notificaciones y la lectura de mensajes son independientes. | Opciones: que la app marque la notificación leída al recibir `new_message`, o que el gateway consulte la presencia. | |
| 6.12 | ¿Se despliega **más de una réplica** de Matching? | Una sola. Los sockets viven en la memoria del proceso. | Con varias réplicas hace falta `LISTEN/NOTIFY` de PostgreSQL o Redis. | |
| 6.13 | ¿La insignia de no leídos debe contar chats con personas **bloqueadas o desactivadas**? | `totalUnread` las cuenta; la lista no las muestra. | La insignia puede ser mayor que la suma de la lista. | |
| 6.14 | ¿Quién publica el `wss://` hacia afuera, el gateway o Matching directamente? | El gateway (implementé el proxy). | Matching no está publicado fuera de Docker. | |

---

## 7. Bloquear y silenciar (HU-51)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 7.1 | **¿Un bloqueo debe cortar todo el contacto o solo ocultar en Descubrir?** | Reutilicé `bloqueo_usuario` (HU-42) y ahora un bloqueo retira la tarjeta del deportista en ambos sentidos: sin chat, solicitudes ni actividades en común. | Antes solo ocultaba en Descubrir. Cambia el comportamiento de Matching **y de Actividades** (que no probé en vivo, lo deduje leyendo su código). Para volver atrás, se quita un filtro. | |
| 7.2 | ¿Un bloqueo debe **alcanzar al club**? | No. Un deportista que bloqueó a un representante sigue recibiendo sus comunicados, y el club sigue viendo el perfil copiado. | Hay que decidir si `ms-club` consulta `/blocks/check` (necesita el token de una de las dos personas). | |
| 7.3 | ¿El silencio debe **apagar notificaciones** de verdad? | Hoy solo se guarda la lista (`silencio_usuario`). | Para que surta efecto, el evento de mensaje debe llevar quién lo envía, y el worker del gateway debe omitir el aviso. | |
| 7.4 | ¿Se silencian **personas o conversaciones**? | Personas, porque no hay chats de grupo. | Cambia si aparecen grupos (6.3). | |
| 7.5 | ¿Hay un **tope** de personas bloqueadas o silenciadas por cuenta? | No hay. | Evita abusos y consultas enormes. | |
| 7.6 | ¿Qué mensaje ve la persona cuando el chat "desaparece" por un bloqueo? | Uno genérico ("Deportista no disponible…"), que a propósito no revela el bloqueo. | La app debería mostrarlo con un texto claro. | |
| 7.7 | ¿Quién consulta `/blocks/check` y con qué credencial? | Solo una de las dos personas o un administrador, y responde solo `{"blocked": bool}`. No se expone a la app. | Si otros servicios lo necesitan con una cuenta de servicio, hay que crearla. | |

---

## 8. Ubicación (recomendaciones por cercanía)

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 8.1 | **¿Cada cuánta distancia se actualiza la ubicación del usuario?** | 200 metros (`LOCATION_MIN_MOVE_METERS`), configurable. | Quedó "aún sin definir". La app consulta `GET /location` para usar el mismo valor. | |
| 8.2 | ¿Cuánto tiempo vale una posición sin actualizar? | 1440 minutos, un día (`LOCATION_MAX_AGE_MINUTES`). Después se usa la zona fija del perfil. | Si la persona está quieta se renueva la fecha; si no abre la app, caduca. | |
| 8.3 | **La base de Matching ya tiene `ubicacion_usuario`** (latitud, longitud y columna geoespacial). ¿Es la tabla prevista para esto? | No la usé. Creé `ubicacion_actual` en Ms_Users (migración 008, sin aplicar), una fila por persona. | Podría haber dos tablas con el mismo fin, en bases distintas. Si la antigua es la elegida, la ubicación viviría en Matching. | |
| 8.4 | ¿**El cálculo de cercanía** vive en Ms_Users (sugerencias) o en Matching? | En Ms_Users, que ya expone `GET /users/suggestions` con `radius_km`. | Si Matching también ordena por cercanía, debe usar la misma fuente. | |
| 8.5 | ¿Se necesita el **consentimiento** del usuario para guardar su posición? ¿Dónde se registra? | Todavía no se registra: falta usar la tabla `consent` antes del primer envío. | Es un dato sensible. | |
| 8.6 | ¿Se **borran** las posiciones de cuentas inactivas? ¿Se guarda historial? | Se guarda solo la última, sin historial; las vencidas se ignoran pero no se eliminan. | Falta una política de retención. | |
| 8.7 | ¿Qué hace la app en **segundo plano**? | No está construido. Se asumió que envía al recorrer la distancia. | Afecta permisos, consumo de batería y la tienda de aplicaciones. | |
| 8.8 | **Actividades**: `actividad` tiene `direccion`, `latitud` y `longitud`, pero el servicio guarda un texto libre (`location`). ¿Cuál se usa? | El texto libre. | Si se quiere mostrar la actividad en un mapa o filtrar por cercanía, harían falta las coordenadas. | |
| 8.9 | ¿Qué precisión se acepta? | Coordenadas con 5 decimales (~1 m) y un campo `precision_m` opcional. | Una precisión menor protegería más la privacidad. | |

---

## 9. Autenticación, roles y notificaciones

| # | Duda | Qué asumí | Por qué importa | Respuesta |
| --- | --- | --- | --- | --- |
| 9.1 | ¿Los **roles** son `player`, `club_admin` y `admin`, o también existe `usuario`? | Los dos nombres (`player`, `usuario`) valen como deportista; `club_admin` es el representante. | El nombre exacto del rol está en el token y en varias comprobaciones. | |
| 9.2 | ¿El `admin` de SportMatch puede **editar clubes**? | No. La HU-31 pide un panel exclusivo del representante dueño. | Un administrador no podría corregir un club. | |
| 9.3 | ¿Las **notificaciones de ms-club** (revisión de postulaciones y comunicados) deben llegar a la bandeja del gateway? | Sí. El worker ya puede leer `clubs_api.notification_outbox` si se define `CLUBS_DATABASE_URL` (rol `sportmatch_clubs`). | Hasta que existan la tabla y el rol, los avisos no llegan. | |
| 9.4 | La app solo reconoce los destinos `activity`, `chat`, `matches` y `profile` al tocar un aviso. ¿Se agregan `convocatoria` y `comunicado`? | No hice cambios en la app. | Hoy el aviso queda en la bandeja sin destino. | |
| 9.5 | ¿Qué **origen** tiene el sitio web (para CORS y para el `wss://`)? | `CORS_ALLOWED_ORIGINS` vacío por defecto. | Sin él, el navegador no puede llamar al gateway ni abrir el socket (el gateway rechaza un `Origin` no permitido). | |

---

## 10. Cómo usar este documento

1. Empezar por la sección 1 (en especial 1.1, 1.2 y 1.6): son las que desbloquean la conexión a las bases.
2. Completar la columna **Respuesta**; lo que quede igual a "lo que asumí" no requiere cambios.
3. Con las respuestas, actualizar `PENDIENTES.md` y ajustar el código: cada decisión de [PENDIENTES.md § 2](PENDIENTES.md)
   indica el archivo donde se cambia.
