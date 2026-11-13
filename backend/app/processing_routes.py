"""Versioned FastAPI routes for persistent Phase 4 processing jobs."""

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from ai_engine.media_processing import ProcessingJob, ProcessingResult

from .processing_schemas import (
    ProcessingErrorResponse,
    ProcessingJobCreateRequest,
    ProcessingJobResponse,
    ProcessingJobRetryRequest,
    ProcessingResultsResponse,
)
from .processing_service import ProcessingService


router = APIRouter(prefix="/api/v1")


def get_processing_service(request: Request) -> ProcessingService:
    return request.app.state.processing_service


def _job_payload(job: ProcessingJob) -> dict:
    return {
        "job_id": job.job_id,
        "investigation_id": job.investigation_id,
        "media_id": job.media_id,
        "processing_status": job.processing_status.value,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "failure": None if job.failure is None else {
            "code": job.failure.code,
            "message": job.failure.message,
            "occurred_at": job.failure.occurred_at,
        },
    }


def _result_payload(result: ProcessingResult) -> dict:
    return {
        "result_id": result.result_id,
        "job_id": result.job_id,
        "media_id": result.media_id,
        "algorithm": result.algorithm,
        "algorithm_version": result.algorithm_version,
        "result_type": result.result_type,
        "result_data": dict(result.result_data),
        "created_at": result.created_at,
    }


@router.post(
    "/processing-jobs",
    status_code=202,
    response_model=ProcessingJobResponse,
    responses={400: {"model": ProcessingErrorResponse}, 404: {"model": ProcessingErrorResponse}, 422: {"model": ProcessingErrorResponse}},
)
def create_processing_job(
    payload: ProcessingJobCreateRequest,
    background_tasks: BackgroundTasks,
    service: ProcessingService = Depends(get_processing_service),
):
    job = service.create_job(str(payload.investigation_id), str(payload.media_id))
    background_tasks.add_task(service.execute, job.job_id)
    return {"api_version": "1.0", "job": _job_payload(job)}


@router.get(
    "/processing-jobs/{job_id}",
    response_model=ProcessingJobResponse,
    responses={400: {"model": ProcessingErrorResponse}, 404: {"model": ProcessingErrorResponse}},
)
def get_processing_job(
    job_id: str,
    service: ProcessingService = Depends(get_processing_service),
):
    return {"api_version": "1.0", "job": _job_payload(service.get_job(job_id))}


@router.get(
    "/processing-jobs/{job_id}/results",
    response_model=ProcessingResultsResponse,
    responses={400: {"model": ProcessingErrorResponse}, 404: {"model": ProcessingErrorResponse}},
)
def get_processing_results(
    job_id: str,
    service: ProcessingService = Depends(get_processing_service),
):
    results, total = service.get_results(job_id)
    normalized_job_id = results[0].job_id if results else service.get_job(job_id).job_id
    return {
        "api_version": "1.0",
        "job_id": normalized_job_id,
        "items": [_result_payload(result) for result in results],
        "total": total,
    }


@router.post(
    "/processing-jobs/{job_id}/retry",
    status_code=202,
    response_model=ProcessingJobResponse,
    responses={400: {"model": ProcessingErrorResponse}, 404: {"model": ProcessingErrorResponse}, 409: {"model": ProcessingErrorResponse}, 422: {"model": ProcessingErrorResponse}},
)
def retry_processing_job(
    job_id: str,
    payload: ProcessingJobRetryRequest,
    background_tasks: BackgroundTasks,
    service: ProcessingService = Depends(get_processing_service),
):
    job = service.retry_job(job_id, retry_failed=payload.retry_failed)
    background_tasks.add_task(service.execute, job.job_id)
    return {"api_version": "1.0", "job": _job_payload(job)}
