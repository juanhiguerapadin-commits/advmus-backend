AdVMus Backend — Secrets / Credenciales (NO commitear)

Este proyecto NO necesita guardar claves JSON en el repo.
En Cloud Run se usa ADC (Application Default Credentials) vía Service Account.
En local, lo recomendado es ADC vía gcloud.

------------------------------------------------------------
✅ Regla #1: NO subir secretos al repo
------------------------------------------------------------
- NO subir archivos .json de Service Accounts
- NO subir .env con claves reales
- NO subir env.yaml (Cloud Run env-vars file)
- Todo eso debe estar en .gitignore

Este folder "secrets/" existe solo como recordatorio/documentación.
No debería contener secretos reales.

------------------------------------------------------------
Autenticación a Google Cloud (recomendado)
------------------------------------------------------------
Cloud Run:
- Usa el Service Account asignado al servicio de Cloud Run.
- No hay JSON keys.

Local (Windows):
1) Autenticá ADC:
   gcloud auth application-default login

2) Verificá que tenés proyecto configurado:
   gcloud config get-value project

------------------------------------------------------------
Variables de entorno usadas por el backend
------------------------------------------------------------
Requeridas:
- GCS_BUCKET=advmus-backend-dev

Auth:
- AUTH_MODE=api_key | firebase | none

API Keys (elige UNA opción):
A) Single key (simple dev):
- API_KEY=KEY_LARGA_...

B) Multi-tenant map (preferido):
- API_KEYS_JSON='{"demo":"KEY1","tenant2":"KEY2"}'

C) Lista (opcional):
- API_KEYS=KEY1,KEY2

CORS:
- CORS_ALLOW_ORIGINS="http://localhost:3000,http://localhost:5173"

------------------------------------------------------------
Estructura esperada del repo (referencia)
------------------------------------------------------------
advmus-backend/
  app/
    main.py
    auth.py
    db_firestore.py
    storage.py
    core/
      errors.py
      __init__.py
    middlewares/
      request_id.py
      __init__.py
    routers/
      invoices.py
      admin.py
      __init__.py
    schemas.py
  secrets/
    README.txt
  requirements.txt
  Dockerfile
  .dockerignore
  .gitignore
  .env.example

------------------------------------------------------------
.env.example (plantilla)
------------------------------------------------------------
Si existe .env.example en la raíz:
- Es solo plantilla (sin secretos reales)
- Copiá a .env SOLO en local si querés (y .env debe estar en .gitignore)

------------------------------------------------------------
Notas de seguridad
------------------------------------------------------------
- En producción: usar Secret Manager para API_KEY / API_KEYS_JSON (cuando toque).
- Por ahora (MVP): está OK usar env vars en Cloud Run, pero sin commitearlas.
- Nunca compartas keys por GitHub / commits / PRs.
