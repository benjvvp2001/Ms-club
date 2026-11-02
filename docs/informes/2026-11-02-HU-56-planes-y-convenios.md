# HU-56: gestionar los planes y convenios disponibles

**Fecha:** 2026-11-02
**Entorno de la verificación:** local aislado (Postgres `127.0.0.1:5433`, gateway `127.0.0.1:8000`). Webpay **simulado**
(ms-club en modo mock solo durante la prueba): no se usó el Webpay de integración ni ninguna credencial. Sin commit ni push.

## 1. Dónde viven los planes (y por qué se implementó ahí)

Los planes, las suscripciones y los pagos viven **solo en la base de ms-club** (`sportmatch_clubs`):
`plan_suscripcion`, `suscripcion` y `transaccion_pago` (migración `006_pagos.sql`, HU-40). Se comprobó en el esquema real:
en `sportmach_users` **no existen** las tablas `plan`, `membresia` ni `pago` que aparecen en los diagramas. Lo único de
Ms_Users que se relaciona es el catálogo de auditoría (`AuditAction` ya reserva `PLAN_CREATED`, `PLAN_UPDATED` y
`PLAN_DEACTIVATED`). No hay duplicación que resolver: la 006 lo dice y el código la sigue (`membresia` y `pago` quedaron sin usar).

Por eso la HU se implementó en **ms-club**, y la auditoría se escribe en Ms_Users por el endpoint interno de la HU-48
(`POST /internal/v1/audit/events`, secreto `INTERNAL_SERVICE_TOKEN`). El gateway solo declara las rutas.

## 2. Qué significa "convenio"

En el modelo no existía el concepto: `plan.tipo` es `USUARIO | CLUB` y significa **quién lo compra**; no hay `club_id` en el
plan ni referencias a convenios en el código (solo una frase en `docs/webpay/README.md` sobre un panel web simulado que ya no
existe). Se implementó como un **tipo de plan**: la columna nueva `categoria` (`PLAN` por defecto, o `CONVENIO`). Un convenio
se compra igual que un plan y sigue teniendo `tipo` (quién lo compra); la categoría solo lo distingue en el catálogo y el panel.

## 3. Archivos

Creados: `ms-club/db/migrations/012_gestion_planes.sql`, `app/schemas/plan_admin.py`, `app/services/plan_admin_service.py`,
`app/services/plan_audit.py`, `app/api/v1/planes_admin.py`, `tests/test_planes_admin.py`, `tests/test_planes_admin_postgres.py`,
`Frontend-SportMatch-APP/services/planesAdmin.ts`, `components/PlanesAdmin.tsx`, `scripts/test-planes-admin.cjs`,
`sportmatch-web/lib/planesAdmin.ts`.

Modificados (ms-club): `app/repositories/{base,mock_club_repository,postgres_club_repository}.py`, `app/services/pago_service.py`,
`app/schemas/pago.py`, `app/core/security.py` (`is_admin`), `app/api/dependencies.py`, `app/main.py` (router, auditor y los 400 ya
no repiten el valor enviado), `db/seeds/demo_planes.sql`, `tests/test_pagos.py` (nueva forma del plan), `PENDIENTES.md`.
Gateway: `gateway/app/api/routes/clubs.py` (4 rutas), `gateway/tests/test_clubs_proxy.py`, `compose.yaml` (`INTERNAL_SERVICE_TOKEN`
para ms-club). App: `app/(admin)/home.tsx` (pestaña "Planes"), `package.json`.

## 4. Endpoints nuevos

Todos exigen sesión en el gateway y el rol `admin` en ms-club (un no-admin recibe 403 aunque el plan no exista; sin token, 401).

| Método y ruta (`/api/v1/payments/admin/plans…`) | Rol | Qué hace |
|---|---|---|
| `GET /` | admin | Activos e inactivos, más reciente primero, con `suscripciones_vigentes`. Filtros `estado`, `tipo`, `categoria`; `pagina` y `limit` (máx. 50) |
| `POST /` | admin | Crea un plan o convenio (201). 409 si el nombre ya existe entre los activos |
| `PATCH /{plan_id}` | admin | Edita lo enviado (nombre, descripción, precio, duración, beneficios). Tipo y categoría no se editan. 409 si está desactivado |
| `POST /{plan_id}/deactivate` | admin | Borrado lógico (`is_active = false`). 409 si ya estaba desactivado. No existe ruta DELETE |

Cambiados: `GET /api/v1/payments/plans` (público) ahora incluye `categoria` y `beneficios`.

## 5. Migración 012 (ms-club, idempotente)

- `plan_suscripcion`: `categoria`, `beneficios jsonb` (lista de hasta 10), precio de 0 a 10.000.000, nombre único entre
  **activos** (índice parcial sobre `lower(btrim(nombre))`; reemplaza a `UNIQUE (tipo, nombre)`), y permisos INSERT/UPDATE (nunca DELETE) para `sportmatch_clubs`.
- `transaccion_pago` y `suscripcion`: copia de lo pagado (`plan_nombre`, `duracion_dias`, `beneficios` y, en la suscripción, `precio_pagado`).
  Las filas existentes se rellenan con el monto de su último pago o, si no hay, con los valores actuales del plan.
- Comprobado sobre datos reales (6 suscripciones y 20 pagos): las huellas de plan, suscripción y pago son idénticas antes y después.

## 6. Reglas implementadas

- **Un cambio de precio o duración no afecta lo ya pagado.** El pago copia nombre, duración y beneficios al iniciarse (el precio ya se copiaba en `amount`)
  y la suscripción usa esa copia. Un pago iniciado antes del cambio se liquida con las condiciones con que partió. Al renovar se toman las condiciones nuevas y se conservan los días ya pagados.
- **Catálogo:** solo planes activos. Un plan desactivado no se puede contratar ni renovar (404); las suscripciones vigentes siguen `ACTIVE` hasta su `fecha_fin`.
- **Renovación automática:** no existe en ningún servicio; una suscripción solo se extiende con un pago nuevo, así que un plan desactivado no se renueva.
- **Validaciones** (`extra="forbid"`): nombre 3–60 con caracteres permitidos, precio entero estricto 0–10.000.000 (sin decimales, NaN ni Infinity),
  duración 1–366, hasta 10 beneficios de 3–120 caracteres sin repetir, descripción ≤ 255, motivo 3–300; sin NUL ni sustitutos Unicode. Los errores son 400 (convención del servicio) y no repiten el valor.
- **Auditoría** (HU-48) dentro de la transacción del cambio: `PLAN_CREATED`, `PLAN_UPDATED`, `PLAN_DEACTIVATED`, con motivo opcional (hay uno por defecto) y metadata
  con `precio_anterior`, `precio_nuevo`, duraciones, `campos` cambiados, tipo y categoría (solo números y palabras fijas). Si falla la auditoría: 503 y no se guarda nada.
  Una edición sin cambios reales no escribe ni audita.
- Un plan de precio 0 se lista pero no se paga con Webpay (409 "gratuito").

## 7. Tests

| Suite | Resultado |
|---|---|
| ms-club completa contra `qa_tests` | **688 pasan**, 0 fallan, 0 omitidos (incluye 6 de Postgres real y los nuevos de HU-56) |
| Gateway completa | **224 pasan**, 11 omitidos (antes 219 y 11) |
| App (11 scripts `npm run test:*`) | 96 pasan, 0 fallan (incluye `test:planes-admin`, 7) |
| `tsc` de la app y del cliente web | sin errores |

Nuevos en ms-club: `test_planes_admin.py` (crear, editar, desactivar, validaciones, nombre duplicado, 401/403, precio que no altera suscripciones, catálogo, suscripción vigente
de un plan desactivado, auditoría con precio anterior y nuevo, rollback si falla la auditoría) y `test_planes_admin_postgres.py` (todas las migraciones en orden sobre una base vacía,
012 sobre filas antiguas, restricciones de la BD, el flujo con el repositorio real, rollback de auditoría y creación concurrente: un solo 201). Ningún test dejó esquemas ni tablas en `qa_tests`.
No hubo pruebas intermitentes. Ms_Users no se modificó en esta HU y su suite no se volvió a correr.

## 8. Verificación en el stack real (42 PASS, 0 FAIL)

Cuentas desechables: un admin, dos deportistas y un representante de club. Roles: deportista y club_admin 403 en las 4 rutas (aunque el plan no exista), sin token 401.
Se creó un plan y un convenio (catálogo público con beneficios), nombre duplicado 409, 6 validaciones 400. Dos deportistas contrataron con Webpay simulado; la suscripción guarda 4.990 / 30 días.
Se cambió precio, duración y beneficios: ambas suscripciones quedaron idénticas, el catálogo mostró el precio nuevo. Se desactivó: salió del catálogo, la suscripción de A siguió `ACTIVE`, comprar de nuevo dio 404,
el listado de administración mostró el plan inactivo con 2 suscripciones vigentes, y el nombre quedó libre. El historial de auditoría (ya corregido en la HU-48) mostró 3 `PLAN_CREATED`,
1 `PLAN_UPDATED` (precio 4.990 → 9.990, campos `precio,duracion_dias,beneficios`) y 1 `PLAN_DEACTIVATED`, sin correos ni tokens. Con Ms_Users detenido, crear, editar y desactivar dieron 503 y no cambió nada en la base.

Limpieza: se borraron las cuentas, sus suscripciones, pagos y planes `HU56 …`, las filas de auditoría de esas cuentas, y ms-club volvió a su configuración normal (sin modo simulado).
Las 5 bases quedaron con los mismos conteos y huellas que antes. El contador de identidad de `plan_suscripcion` avanzó (no se puede restaurar; es inofensivo).

## 9. App y web

App: servicio `services/planesAdmin.ts` y componente `components/PlanesAdmin.tsx` (lista con filtro, formulario de alta y edición, desactivar con confirmación) como pestaña "Planes" del panel de admin.
Web: solo el cliente `lib/planesAdmin.ts` (tipos y funciones); no hay pantalla.

## 10. Decisiones y pendientes

Ver la sección 17 de `ms-club/PENDIENTES.md`: decisiones (convenio como categoría, precio entero sin decimales, 400 en vez de 422, sin reactivar, sin renovación automática, pagos en vuelo se honran,
auditoría dentro de la transacción) y pendientes (desplegar con `INTERNAL_SERVICE_TOKEN`, aplicar la 012 en cada ambiente, pantalla web, catálogo del cliente con planes fijos).
