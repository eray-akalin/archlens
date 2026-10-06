from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.users import router as users_router
from app.db import init_db

app = FastAPI(title="tiny-service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router)


@app.on_event("startup")
def startup() -> None:
    init_db()
    print("tiny-service started")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
