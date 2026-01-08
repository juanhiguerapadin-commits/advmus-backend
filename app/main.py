from fastapi import FastAPI
from app.routers.invoices import router as invoices_router

app = FastAPI(title="AdVMus API", version="0.1.0")


@app.get("/")
def root():
    return {"message": "AdVMus backend running. Go to /docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


# Versionado PRO: TODO lo de la API vive bajo /v1
app.include_router(invoices_router, prefix="/v1")
