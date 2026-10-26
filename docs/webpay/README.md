# Webpay Plus y revisión de SportMatch

Revisión: 26 de octubre de 2026. La integración usa la API REST de Transbank desde `Ms-club`. El entorno local queda en **INTEGRATION**, con `WEBPAY_MOCK_MODE=false`: se visita Webpay de Transbank, pero no se cobra dinero real.

## Flujo implementado

1. App o web solicita `POST /api/v1/payments/webpay/initiate` enviando `plan_id`. La web añade `client: "web"`; la app usa el valor predeterminado `app`.
2. El backend calcula comprador, monto y orden; crea la transacción en Transbank. Las credenciales nunca van al frontend.
3. La respuesta contiene `checkout_url`, además de los campos anteriores `url` y `token_ws`. Se abre `checkout_url`: es un enlace firmado que vence a los 15 minutos y genera el formulario **POST** exigido por Transbank, con botón alternativo si JavaScript está deshabilitado.
4. Webpay retorna al gateway `/api/v1/payments/webpay/return` por GET o POST. El backend confirma el resultado con Transbank antes de redirigir, aunque haya expirado la sesión de login.
5. El destino es `/pago` en Expo o `/pago/resultado` en Next.js. Estas pantallas consultan `POST /payments/webpay/commit` autenticadas para obtener el resultado o reintentar una confirmación pendiente.
6. Solo `AUTHORIZED` con `response_code=0`, y con orden, sesión y monto coincidentes, activa la suscripción. Una respuesta repetida no extiende nuevamente su vigencia.

Si se pierde la respuesta de confirmación, el backend consulta el estado mediante GET a Transbank. Un fallo de conexión no se registra como rechazo. La app conserva el intento pendiente por usuario para recuperarlo tras cerrarla. La web permite revisar o continuar el intento desde **Mi suscripción**.

## Configuración

En `Ms_gateway/.env` cuando se usa Docker Compose, o en el entorno del proceso `Ms-club` cuando se ejecuta directamente:

```dotenv
WEBPAY_MOCK_MODE=false
WEBPAY_ENVIRONMENT=INTEGRATION
WEBPAY_COMMERCE_CODE=
WEBPAY_API_KEY=
WEBPAY_PUBLIC_URL=http://localhost:8000
WEBPAY_FRONTEND_RETURN_URL=sportmatchapp://pago
WEBPAY_WEB_RETURN_URL=http://localhost:3000/pago/resultado
```

En integración, los campos de comercio y clave vacíos usan las credenciales públicas de prueba. En un teléfono, `WEBPAY_PUBLIC_URL` debe usar la IP LAN del equipo o un dominio accesible; `localhost` apunta al teléfono. En Expo Go, conservar el retorno de Expo correspondiente, por ejemplo `exp://IP_DEL_PC:8081/--/pago`. En una compilación instalada, usar el scheme `sportmatchapp://pago`.

`WEBPAY_WEB_RETURN_URL` debe coincidir con el origen donde se inició sesión en la web: no mezclar `localhost`, `127.0.0.1` y una IP LAN porque no comparten cookies ni sessionStorage.

Para aplicar cambios locales desde `Ms_gateway`:

```powershell
docker compose -f compose.yaml -f compose.windows.yaml up -d --build --no-deps ms_club gateway
```

El backend y gateway locales se reconstruyeron durante esta revisión. Para producción, aún deben configurarse las credenciales propias de comercio, `WEBPAY_ENVIRONMENT=PRODUCTION` y las URLs HTTPS del despliegue, y completarse la validación que exige Transbank. No se configuraron cobros reales.

## Evidencia de validación

Se probó en Edge con Playwright, usando el frontend Next.js compilado, su BFF real y `Ms-club` con repositorio aislado en memoria para no modificar las suscripciones existentes. **El proveedor de pago fue la API real de integración de Transbank**, no `MockWebpay`.

- Creación y consulta de estado: `INITIALIZED`, con monto correcto.
- Compra con tarjeta de prueba: aprobada, comprobante por $14.990, autorización `1213`, últimos dígitos `6623`. [Captura](aprobado.png).
- Recarga del resultado: misma fecha de fin; sin segunda extensión.
- Rechazo desde el banco de integración: mensaje de rechazo en SportMatch. [Captura](rechazado.png).
- Anulación desde Webpay: mensaje de cancelación; la suscripción anterior conservó su fecha de fin.
- Portada y suscripción a 390 y 1440 píxeles: sin desbordamiento horizontal ni errores JavaScript. [Suscripción móvil](suscripcion-movil.png), [portada móvil](portada-movil.png).
- Acceso sin sesión a suscripción: redirige al inicio de sesión.
- Tests de backend cubren aprobación, rechazo, cancelación, permisos, idempotencia, respuesta perdida, firma del checkout, sesión/monto incorrectos y callback sin login.
- Tests de la app cubren persistencia por usuario, envío del retorno de cancelación, errores reintentables y activación exclusiva por aprobación.

Comandos de comprobación, desde cada proyecto:

Resultado de esta revisión: 608 tests de `Ms-club`, 116 de proxy del gateway, 20 de la web y 87 de servicios de la app aprobados. Lint y TypeScript sin errores; compilación de producción Next.js correcta. Los tests Python emiten advertencias de deprecación de sus librerías de pruebas, sin fallos.

```powershell
# Ms-club
.\.venv\Scripts\python.exe -m pytest
# Ms_gateway/gateway
.\.venv\Scripts\python.exe -m pytest tests/test_clubs_proxy.py tests/test_users_proxy.py
# sportmatch-web
npm test
npm run lint
npm run build
npm audit
# Frontend-SportMatch-APP
npx tsc --noEmit
npm run lint
node --test scripts/test-*.cjs
```

## Fallos corregidos y límites de la revisión

- La app enviaba el token por GET y no tenía `/pago`: ahora abre el formulario POST firmado y recibe aprobación, rechazo, cancelación o timeout.
- La web bloqueaba iniciar/confirmar pagos y no tenía contratación del club: ahora ofrece `/suscripcion`, catálogo del backend y comprobante.
- Se perdía el intento ante errores de conexión o al cerrar el modal: ahora se conserva y puede reintentarse.
- El backend no recuperaba una confirmación cuya respuesta se perdiera ni comprobaba la sesión devuelta; además redondeaba montos inesperados: corregido.
- El retorno dependía de que el frontend mantuviera su login: ahora confirma desde el backend.
- Los límites de espera del gateway y BFF eran menores que el flujo de confirmación y recuperación: ajustados.
- La configuración local seguía simulando pagos: cambiado a la API de integración.
- La web usaba Next.js 16.4.0, señalado por `npm audit` con vulnerabilidades críticas: actualizado a 16.4.3. Auditoría posterior sin vulnerabilidades.
- Validación del origen CSRF inválido y dependencias del efecto de animación de `MatchModal`: corregidas.

El panel administrativo `/planes` sigue usando `lib/planes.mock.ts`: sus cambios de planes/convenios son locales y **no cambian el catálogo que se cobra**. La nueva pantalla del club sí usa `/payments/plans`. También permanecen módulos administrativos de moderación simulados; esta revisión no reemplaza todos los mocks de la plataforma.

La prueba completa de pago se ejecutó en navegador con almacenamiento de pedidos aislado. No equivale a una prueba del flujo completo contra PostgreSQL ni a la certificación de Transbank. La app pasó TypeScript, lint y tests de servicio; falta comprobar el retorno en un teléfono físico Android/iOS con su compilación y red concretas.

Fuentes oficiales consultadas: [API REST Webpay](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/referencia/webpay/README.md), [ambientes y tarjetas de prueba](https://github.com/TransbankDevelopers/transbank-developers-docs/blob/master/documentacion/como_empezar/README.md), [Expo WebBrowser SDK 54](https://github.com/expo/expo/blob/sdk-54/docs/pages/versions/v54.0.0/sdk/webbrowser.mdx).
