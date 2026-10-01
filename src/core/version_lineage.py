




















from __future__ import annotations


def _version_record(
    *,
    layer_id: str | None = None,
    request_id: str | None = None,
    prompt: str | None = "",
    parent_request_id: str | None = None,
    template_id: str | None = None,
    template_name: str | None = None,
    resolution: str | None = "",
    job: dict | None = None,
) -> dict:
    return {
        "layer_id": layer_id,
        "request_id": request_id,
        "prompt": prompt or "",
        "parent_request_id": parent_request_id,
        "template_id": template_id,
        "template_name": template_name,
        "resolution": resolution or "",
        "job": job,
        "preview": False,
        "preview_name": None,
        "promoted": False,
    }


def original_version_record() -> dict:

    return _version_record()


def result_version_record(
    layer_id: str,
    request_id: str | None,
    prompt: str | None,
    *,
    parent_request_id: str | None,
    template_id: str | None,
    template_name: str | None,
    resolution: str | None,
) -> dict:

    return _version_record(
        layer_id=layer_id,
        request_id=request_id,
        prompt=prompt,
        parent_request_id=parent_request_id,
        template_id=template_id,
        template_name=template_name,
        resolution=resolution,
    )


def restored_version_record(job: dict) -> dict:



    return _version_record(
        request_id=job.get("request_id"),
        prompt=job.get("prompt"),
        parent_request_id=job.get("parent_request_id"),
        template_id=job.get("template_id"),
        template_name=job.get("template_name"),
        resolution=job.get("resolution"),
        job=job,
    )
