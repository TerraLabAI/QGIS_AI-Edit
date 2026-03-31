import time
from dataclasses import dataclass
from typing import Callable, Optional

# Supported aspect ratios for Nano Banana 2 (label, width/height ratio)
ASPECT_RATIOS = [
    ("1:1", 1.0),
    ("5:4", 5 / 4),
    ("4:5", 4 / 5),
    ("4:3", 4 / 3),
    ("3:4", 3 / 4),
    ("3:2", 3 / 2),
    ("2:3", 2 / 3),
    ("16:9", 16 / 9),
    ("9:16", 9 / 16),
    ("21:9", 21 / 9),
]


def calculate_closest_aspect_ratio(width: int, height: int) -> str:
    """Pick the closest supported aspect ratio for the given dimensions."""
    if height == 0:
        return "16:9"
    ratio = width / height
    best_label = "1:1"
    best_diff = float("inf")
    for label, ar in ASPECT_RATIOS:
        diff = abs(ratio - ar)
        if diff < best_diff:
            best_diff = diff
            best_label = label
    return best_label


@dataclass
class GenerationResult:
    success: bool
    image_url: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    request_id: Optional[str] = None


class GenerationService:
    """Orchestrates the image generation flow. Pure Python."""

    def __init__(self, client, poll_interval: float = 2.0, max_polls: int = 60):
        self._client = client
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def reset(self):
        self._cancelled = False

    def generate(
        self,
        image_b64: str,
        prompt: str,
        auth: dict,
        aspect_ratio: str = "1:1",
        on_progress: Callable = None,
        ctx=None,
        suggested_resolution: str = "1K",
    ) -> GenerationResult:
        """Submit image for generation and poll until complete."""
        if self._cancelled:
            return GenerationResult(success=False, error="Generation cancelled")

        # Submit (pre-prompt is applied server-side in website config)
        resp = self._client.submit_generation(
            image_b64=image_b64,
            prompt=prompt,
            resolution=suggested_resolution,
            aspect_ratio=aspect_ratio,
            auth=auth,
        )

        if "error" in resp:
            return GenerationResult(
                success=False, error=resp["error"], error_code=resp.get("code", "")
            )

        request_id = resp["request_id"]

        if ctx is not None:
            ctx.submitted_resolution = suggested_resolution
            ctx.submitted_aspect_ratio = aspect_ratio
            ctx.submit_timestamp = time.time()
            ctx.request_id = request_id

        # If submit already returned the image (sync mode), skip polling
        if resp.get("status") == "completed" and resp.get("image_url"):
            if ctx is not None:
                ctx.poll_count = 0
                ctx.total_wait_seconds = 0.0
                ctx.final_status = "completed"
            return GenerationResult(
                success=True,
                image_url=resp["image_url"],
                request_id=request_id,
            )

        # Poll
        for i in range(self._max_polls):
            if self._cancelled:
                return GenerationResult(
                    success=False, error="Generation cancelled", request_id=request_id
                )

            status_resp = self._client.poll_status(request_id, auth=auth)

            # Fail fast on server errors instead of silently retrying
            if "error" in status_resp and "status" not in status_resp:
                if ctx is not None:
                    ctx.poll_count = i + 1
                    ctx.total_wait_seconds = (i + 1) * self._poll_interval
                    ctx.final_status = "error"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error", "Status check failed"),
                    error_code=status_resp.get("code", ""),
                    request_id=request_id,
                )

            status = status_resp.get("status", "unknown")

            if on_progress:
                on_progress(status, i + 1, self._max_polls)

            if status == "completed":
                if ctx is not None:
                    ctx.poll_count = i + 1
                    ctx.total_wait_seconds = (i + 1) * self._poll_interval
                    ctx.final_status = "completed"
                return GenerationResult(
                    success=True,
                    image_url=status_resp.get("image_url"),
                    request_id=request_id,
                )

            if status == "failed":
                if ctx is not None:
                    ctx.poll_count = i + 1
                    ctx.total_wait_seconds = (i + 1) * self._poll_interval
                    ctx.final_status = "failed"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error", "Generation failed"),
                    request_id=request_id,
                )

            if self._poll_interval > 0:
                time.sleep(self._poll_interval)

        if ctx is not None:
            ctx.poll_count = self._max_polls
            ctx.total_wait_seconds = self._max_polls * self._poll_interval
            ctx.final_status = "timeout"

        return GenerationResult(
            success=False,
            error="Generation timed out, please try again",
            request_id=request_id,
        )
