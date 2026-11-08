from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.dev_auth import setup_dev_auth
from app.judging import start_judge_worker, stop_judge_worker
from app.routers import admin, auth, chat, conversations, scoring, team, team_pages
from app.turns import drain_turns

load_dotenv()

ERROR_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation",
    429: "rate_limited",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    start_judge_worker()
    yield
    await stop_judge_worker()
    await drain_turns()


app = FastAPI(title="Jailbreak Challenge API", lifespan=lifespan)


# Every error uses the body the frontend expects: see "Errors" in the root README.
def error_response(status: int, message: str, retry_after_seconds: Optional[int] = None) -> JSONResponse:
    code = ERROR_CODES.get(status, "server_error")
    if retry_after_seconds is None:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
    return JSONResponse(
        {"error": {"code": code, "message": message, "retryAfterSeconds": retry_after_seconds}},
        status_code=status,
        headers={"Retry-After": str(retry_after_seconds)},
    )


@app.exception_handler(HTTPException)
async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    return error_response(exc.status_code, str(exc.detail), getattr(exc, "retry_after_seconds", None))


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    return error_response(422, "Invalid input.")


for module in (auth, team, team_pages, scoring, conversations, chat, admin):
    app.include_router(module.router, prefix="/api")

setup_dev_auth(app)
