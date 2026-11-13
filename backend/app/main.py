"""FastAPI application factory for the provisional Phase 2 API."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_engine.media_processing import MediaProcessingPipeline

from .config import Settings
from .database import Database
from .media_storage import MediaSourceStorage, UnavailableMediaSourceStorage
from .processing_repository import ProcessingRepository
from .processing_routes import router as processing_router
from .processing_service import ProcessingService
from .repository import InvestigationRepository
from .routes import router
from .schemas import ErrorResponse
from .service import ApiProblem, InvestigationService


def create_app(
    settings: Settings | None = None,
    *,
    media_storage: MediaSourceStorage | None = None,
    processing_pipeline: MediaProcessingPipeline | None = None,
) -> FastAPI:
    """Create an app with persistent databases and injectable media storage.

    The default media adapter is explicitly unavailable because this local
    backend has no Phase 3 media table or upload storage implementation.
    """
    settings = settings or Settings.from_environment()
    database = Database(settings.database_path)
    database.initialize()

    app = FastAPI(title="PS11 Multimodal Intelligence API", version="2.0.0")
    app.state.database = database
    app.state.investigation_service = InvestigationService(InvestigationRepository(database))
    app.state.processing_service = ProcessingService(
        ProcessingRepository(database),
        media_storage if media_storage is not None else UnavailableMediaSourceStorage(),
        processing_pipeline,
    )
    app.include_router(router)
    app.include_router(processing_router)

    @app.exception_handler(ApiProblem)
    async def api_problem_handler(request: Request, exc: ApiProblem):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem_handler(request: Request, exc: RequestValidationError):
        errors = []
        code = "VALIDATION_ERROR"
        for error in exc.errors():
            location = error.get("loc", ())
            message = error.get("msg", "Invalid value")
            if "language" in location and message.startswith("Value error, INVALID_LANGUAGE_TAG:"):
                code = "INVALID_LANGUAGE_TAG"
                message = message.split(": ", 1)[-1]
            elif "language" in location and message.startswith("Value error, LANGUAGE_NOT_SUPPORTED:"):
                code = "LANGUAGE_NOT_SUPPORTED"
                message = message.split(": ", 1)[-1]
            errors.append({"field": ".".join(str(part) for part in location), "message": message})
        public_message = {
            "INVALID_LANGUAGE_TAG": "Language must be a valid BCP 47 tag.",
            "LANGUAGE_NOT_SUPPORTED": "The requested language is not supported.",
            "VALIDATION_ERROR": "Request validation failed.",
        }[code]
        body = ErrorResponse(error={"code": code, "message": public_message, "details": {"fields": errors}})
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @app.exception_handler(StarletteHTTPException)
    async def http_problem_handler(request: Request, exc: StarletteHTTPException):
        known_errors = {
            400: ("BAD_REQUEST", "The request could not be processed."),
            401: ("UNAUTHORIZED", "Authentication is required."),
            403: ("FORBIDDEN", "The request is not allowed."),
            404: ("NOT_FOUND", "The requested resource was not found."),
            405: ("METHOD_NOT_ALLOWED", "The HTTP method is not allowed for this resource."),
            409: ("CONFLICT", "The request conflicts with the current resource state."),
            413: ("PAYLOAD_TOO_LARGE", "The request payload is too large."),
            415: ("UNSUPPORTED_MEDIA_TYPE", "The request media type is not supported."),
            422: ("VALIDATION_ERROR", "Request validation failed."),
            429: ("TOO_MANY_REQUESTS", "Too many requests were received."),
            503: ("SERVICE_UNAVAILABLE", "The service is temporarily unavailable."),
        }
        code, message = known_errors.get(
            exc.status_code, ("HTTP_ERROR", "The request could not be completed.")
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message, "details": {}}},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_problem_handler(request: Request, exc: Exception):
        # Keep internal exception details out of the public API response.
        return JSONResponse(
            status_code=500,
            content={"error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected server error occurred.",
                "details": {},
            }},
        )

    return app


app = create_app()
