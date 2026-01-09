# AdVMus Backend Runbook

## Local (Windows)
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload

## Deploy (Cloud Run)
gcloud run deploy advmus-backend-dev --source . --region southamerica-east1 --service-account advmus-backend-sa

## Prod URL
https://advmus-backend-dev-23085158400.southamerica-east1.run.app
