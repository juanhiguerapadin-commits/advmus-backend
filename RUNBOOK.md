# AdVMus Backend Runbook

## Local (Windows)
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload

## Deploy (Cloud Run) - image + env.yaml (recomendado)
$REGION="southamerica-east1"
$SERVICE="advmus-backend-dev"
$PROJECT=(gcloud config get-value project).Trim()
$REPO_ID=(gcloud artifacts repositories list --location $REGION --format="value(name)" | Select-Object -First 1).Split("/")[-1]
$TAG=(git rev-parse --short HEAD).Trim()
$IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO_ID/advmus-backend:$TAG"

gcloud builds submit --tag $IMAGE .
gcloud run deploy $SERVICE --image $IMAGE --region $REGION --env-vars-file .\env.yaml

## Service URL
https://advmus-backend-dev-23085158400.southamerica-east1.run.app
