# Postman — ms-club (clubes, convocatorias, postulaciones, comunicados, consultas y canales)

Colección con las rutas vigentes de ms-club, llamadas **por el gateway** (`/api/v1/clubs/...`). Los pagos, planes y suscripciones
viven en Ms-pago y no están aquí. Mismo formato que `Ms_Users/postman`.

## Archivos

- `SportMatch-Club.postman_collection.json` — la colección (8 carpetas: cuentas, club, convocatorias, postulaciones, comunicados, administradores, consultas y canales).
- `SportMatch-Club.postman_environment.json` — el entorno (`base_url`, contraseña de prueba y variables que se llenan solas).

## Cómo usarla

1. En Postman: File → Import → los dos `.json` de esta carpeta, y elige el entorno «SportMatch ms-club - local (por el gateway)».
2. Levanta el stack (`Ms_gateway`, `docker compose ... up -d`) con el gateway en `http://127.0.0.1:8000`.
3. Corre la carpeta `00 - Cuentas` primero: registra y confirma un representante de club y un deportista (correos `qa.*@example.com`; el código está en la variable `verification_code`).
   El registro exige verificar el correo con el código que `ms_users` imprime en consola (`docker logs`) o, solo en desarrollo,
   `EMAIL_VERIFICATION_ACCEPT_ANY_CODE=true`; si tu entorno lo pide, confirma el correo antes de seguir.
4. Corre las demás carpetas en orden: cada una guarda los ids (`club_id`, `convocatoria_id`, `canal_id`…) en el entorno.

## Por consola

```bash
npx -p newman newman run postman/SportMatch-Club.postman_collection.json -e postman/SportMatch-Club.postman_environment.json
```

## Límites del plan

Las carpetas 02, 04 y 07 incluyen peticiones que superan el plan Gratis (3.ª convocatoria abierta, comunicados, 2.º canal): aceptan
`201` o `402` y, si es 402, comprueban el cuerpo `{codigo: "LIMITE_PLAN", ...}`. Los límites los define Ms-pago.
