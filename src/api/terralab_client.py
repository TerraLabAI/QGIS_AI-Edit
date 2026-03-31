from __future__ import annotations

import time
import uuid
from urllib.parse import quote, urlencode

from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy, get_export_dial
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning
from ..core.request_context import request_context
from .blocking_request import BlockingRequest
from .image_download import _TIMEOUT_DOWNLOAD
from .network_error_classifier import (
    _MIN_IMAGE_BYTES,
    CANCELLED_CODE,
    DownloadError,
    _cancelled_result,
    _classify_network_error,
    _current_feedback,
    _is_feedback_cancelled,
    _looks_like_image,  # noqa: F401
    _reply_failed,
    _safe_int,
    network_setup_hint,
    qgis_timeout_hint,
    request_feedback,
)
from .network_response import (
    http_failure,
    json_body,
    response_object,
    transfer_timeout,
    valid_headers,
    valid_http_url,
)




_TIMEOUT_API = 30_000



_TIMEOUT_STARTUP = 8_000
_SUBMIT_TIMEOUTS_MS = {
    "1K": 45_000,
    "2K": 60_000,
    "4K": 90_000,
}




_TIMEOUT_WRITE_MS = 10_000



_TIMEOUT_QUICK_POST_MS = 5_000


_TIMEOUT_ACCOUNT_DELETE_MS = 15_000

_TIMEOUT_UPLOAD_PUT_MS = 60_000

_TIMEOUT_PAIR_POLL_MS = 10_000


def _api_timeout_ms() -> int:
    return get_export_dial("timeouts_ms.api", _TIMEOUT_API)


def _startup_timeout_ms() -> int:
    return get_export_dial("timeouts_ms.startup", _TIMEOUT_STARTUP)


def _with_context(path: str) -> str:







    try:
        params = request_context()
        if not params:
            return path
        query = urlencode(params)
        return f"{path}{'&' if '?' in path else '?'}{query}"
    except Exception:  # nosec B110
        return path



__all__ = [
    "_looks_like_image",
    "_MIN_IMAGE_BYTES",
    "_TIMEOUT_DOWNLOAD",
    "CANCELLED_CODE",
    "DownloadError",
    "TerraLabClient",
    "network_setup_hint",
    "qgis_timeout_hint",
    "request_feedback",
]




_client_context: dict[str, bytes | None] = {"header": None}


def _client_context_value() -> bytes:
    if _client_context["header"] is None:
        try:
            params = request_context()
            value = urlencode(params)
            _client_context["header"] = value.encode("ascii", errors="ignore")
        except Exception:  # nosec B110
            _client_context["header"] = b""
    return _client_context["header"] or b""


class TerraLabClient:







    def __init__(self, base_url: str = None, env_vars: dict | None = None):
        if base_url is None:



            if env_vars and env_vars.get("TERRALAB_BASE_URL"):
                base_url = env_vars["TERRALAB_BASE_URL"]
            else:
                base_url = self._read_base_url()
        if not valid_http_url(base_url):
            raise ValueError("Invalid API base URL")
        self.base_url = base_url.rstrip("/")




        self._raw_prompt = str((env_vars or {}).get("RAW_PROMPT", "")).lower() == "true"

    @staticmethod
    def _read_base_url() -> str:

        import os

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        try:
            if os.path.isfile(env_path):
                with open(env_path, encoding="utf-8-sig", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TERRALAB_BASE_URL="):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass  # nosec B110
        return "https://terra-lab.ai"



    def submit_generation(
        self,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        auth: dict,
        image_b64: str | None = None,
        upload_token: str | None = None,
        context_images: list[str] | None = None,
        context_image_notes: list[str] | None = None,
        guidance_image: str | None = None,
        guidance_upload_token: str | None = None,
        centroid_lat: float | None = None,
        centroid_lon: float | None = None,
        ground_resolution_m: float | None = None,
        bbox_wgs84: dict | None = None,
        bbox: dict | None = None,
        crs_authid: str | None = None,
        crs_wkt: str | None = None,
        export_width: int | None = None,
        export_height: int | None = None,
        basemap: str | None = None,
        parent_request_id: str | None = None,
        session_id: str | None = None,
        template_id: str | None = None,
        template_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:





















        if _is_feedback_cancelled(_current_feedback()):
            return _cancelled_result()
        if (image_b64 is None) == (upload_token is None):
            raise ValueError(
                "submit_generation requires exactly one of image_b64 or upload_token"
            )
        payload: dict = {
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        }


        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if upload_token is not None:
            payload["upload_token"] = upload_token
        else:
            payload["image"] = image_b64
        if context_images:
            payload["context_images"] = context_images



            if context_image_notes and any(n for n in context_image_notes):
                payload["context_image_notes"] = context_image_notes





        if guidance_upload_token:
            payload["guidance_upload_token"] = guidance_upload_token
            payload["marks_on_input"] = True
        elif guidance_image:
            payload["guidance_image"] = guidance_image
            payload["marks_on_input"] = True
        if centroid_lat is not None and centroid_lon is not None:
            payload["centroid_lat"] = centroid_lat
            payload["centroid_lon"] = centroid_lon
        if ground_resolution_m is not None:
            payload["ground_resolution_m"] = ground_resolution_m
        if bbox_wgs84 is not None:
            payload["bbox_wgs84"] = bbox_wgs84
        if bbox is not None:
            payload["bbox"] = bbox
        if crs_authid:
            payload["crs_authid"] = crs_authid
        elif crs_wkt:
            payload["crs_wkt"] = crs_wkt
        if export_width and export_height:
            payload["export_width"] = export_width
            payload["export_height"] = export_height
        if basemap:
            payload["basemap"] = basemap
        if parent_request_id:
            payload["parent_request_id"] = parent_request_id
        if session_id:
            payload["session_id"] = session_id
        if template_id:
            payload["template_id"] = template_id
        if template_name:
            payload["template_name"] = template_name


        if self._raw_prompt:
            payload["raw_prompt"] = True
        body = json_body(payload)
        return self._request(
            "POST",
            "/api/ai-edit/generate",
            auth=auth,
            body=body,
            timeout_ms=_get_submit_timeout_ms(resolution),
        )

    def request_upload_url(self, auth: dict, image_format: str = "png") -> dict:












        body = json_body({"format": image_format})
        return self._request(
            "POST",
            "/api/ai-edit/upload-url",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def upload_to_signed_url(
        self,
        url: str,
        data: bytes,
        headers: dict,
        timeout_ms: int | None = None,
    ) -> tuple[bool, str | None]:

        if timeout_ms is None:
            timeout_ms = get_export_dial("pipeline.terralab_client.upload_put_timeout_ms", _TIMEOUT_UPLOAD_PUT_MS)
        if _is_feedback_cancelled(_current_feedback()):
            return (False, f"{CANCELLED_CODE}: cancelled")
        if not valid_http_url(url) or not valid_headers(headers):
            return (False, "CLIENT_ERROR: invalid upload request")
        if not isinstance(data, bytes) or not data:
            return (False, "CLIENT_ERROR: empty upload")
        timeout_ms = transfer_timeout(timeout_ms, _TIMEOUT_UPLOAD_PUT_MS)
        req = QNetworkRequest(QUrl(url))
        for k, v in headers.items():
            req.setRawHeader(k.encode("utf-8"), v.encode("utf-8"))
        QtC.set_transfer_timeout(req, timeout_ms)
        feedback = _current_feedback()
        if _is_feedback_cancelled(feedback):
            return (False, f"{CANCELLED_CODE}: cancelled")
        blocker = BlockingRequest()
        payload = QByteArray(data)
        err = blocker.put(req, payload, feedback=feedback)
        if _is_feedback_cancelled(feedback):
            return (False, f"{CANCELLED_CODE}: cancelled")
        if _reply_failed(err, blocker):
            code, msg = _classify_network_error(blocker, timeout_ms)
            return (False, f"{code}: {msg}")
        reply = blocker.reply()
        http_status = reply.attribute(QtC.HttpStatusCodeAttribute) if reply else None
        status_int = _safe_int(http_status) if http_status is not None else None
        if status_int is None or not 200 <= status_int < 300:
            log_warning(f"Upload PUT failed: HTTP {status_int}")
            return (False, f"HTTP {status_int}")
        return (True, None)

    def poll_status(self, request_id: str, auth: dict, force_fallback: bool = False) -> dict:



        path = f"/api/ai-edit/generate/status?request_id={quote(request_id, safe='')}"
        if force_fallback:
            path += "&force_fallback=true"
        return self._request("GET", path, auth=auth)

    def get_usage(self, auth: dict, timeout_ms: int | None = None) -> dict:


        return self._request("GET", "/api/plugin/usage", auth=auth, timeout_ms=timeout_ms)

    def get_favorites(self, auth: dict) -> dict:

        return self._request("GET", "/api/plugin/favorites", auth=auth)

    def get_generation_history(
        self, auth: dict, limit: int = 24, favorites_only: bool = False,
        before: str | None = None,
    ) -> dict:





        path = f"/api/ai-edit/history?limit={limit}"
        if favorites_only:
            path += "&favorites_only=true"
        if before:
            path += f"&before={quote(before, safe='')}"
        return self._request("GET", path, auth=auth)

    def set_generation_favorite(
        self, auth: dict, request_id: str, is_favorite: bool
    ) -> dict:

        body = json_body({"request_id": request_id, "is_favorite": is_favorite})
        return self._request(
            "POST",
            "/api/ai-edit/history/favorite",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def delete_generation_session(
        self, auth: dict, session_id: str | None = None, request_id: str | None = None
    ) -> dict:



        if bool(session_id) == bool(request_id):
            raise ValueError("Pass exactly one of session_id or request_id")
        body = json_body({"session_id": session_id} if session_id else {"request_id": request_id})
        return self._request(
            "POST",
            "/api/ai-edit/history/delete",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def delete_account(self, auth: dict, confirm: str) -> dict:








        body = json_body({"confirm": confirm})
        result = self._request(
            "POST",
            "/api/plugin/account/delete",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial(
                "pipeline.terralab_client.account_delete_timeout_ms", _TIMEOUT_ACCOUNT_DELETE_MS
            ),
        )




        if isinstance(result, dict) and "error" not in result and result.get("code"):
            result = dict(result)
            result["error"] = get_export_copy(
                "pipeline.terralab_client.account_delete_failed", tr("The account could not be deleted.")
            )
        return result

    def rename_generation_session(self, auth: dict, session_id: str, title: str) -> dict:


        body = json_body({"session_id": session_id, "title": title})
        return self._request(
            "POST",
            "/api/ai-edit/history/rename",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def add_favorite(
        self,
        auth: dict,
        prompt: str,
        label: str | None = None,
        source_category: str | None = None,
    ) -> dict:

        body = json_body({
            "prompt": prompt,
            "label": label,
            "source_category": source_category,
        })
        return self._request(
            "POST", "/api/plugin/favorites", auth=auth, body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def remove_favorite(self, auth: dict, prompt: str) -> dict:

        body = json_body({"prompt": prompt})
        return self._request(
            "POST", "/api/plugin/favorites/delete", auth=auth, body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def get_account(self, auth: dict, include_usage: bool = False) -> dict:







        path = "/api/plugin/account"
        if include_usage:
            path += "?include=usage"
        return self._request("GET", path, auth=auth, timeout_ms=_startup_timeout_ms())

    def get_export_config(self) -> dict:

        return self._request(
            "GET",
            _with_context("/api/ai-edit/export-config"),
            timeout_ms=_startup_timeout_ms(),
        )

    def get_bootstrap(self, auth: dict | None = None) -> dict:



        path = _with_context("/api/plugin/bootstrap")
        if auth:
            return self._request(
                "GET", path, auth=auth, timeout_ms=_startup_timeout_ms()
            )
        return self._request("GET", path, timeout_ms=_startup_timeout_ms())

    def get_config(self, product: str) -> dict:

        return self._request(
            "GET", _with_context(f"/api/plugin/config?product={quote(product, safe='')}"),
            timeout_ms=_startup_timeout_ms(),
        )

    def get_plugin_login_link(
        self, target: str, cta_source: str, auth: dict, locale: str | None = None
    ) -> dict:






        from ..core.request_context import plugin_version

        payload: dict = {
            "target": target,
            "cta_source": cta_source,
            "plugin_version": plugin_version(),
        }
        if locale:
            payload["locale"] = locale
        return self._request(
            "POST",
            "/api/plugin/login-link",
            auth=auth,
            body=json_body(payload),
            timeout_ms=5_000,
        )

    def poll_pairing(self, code: str, timeout_ms: int | None = None) -> dict:






        if timeout_ms is None:
            timeout_ms = get_export_dial("pipeline.terralab_client.pair_poll_timeout_ms", _TIMEOUT_PAIR_POLL_MS)
        return self._request(
            "GET",
            f"/api/plugin/pair/poll?code={quote(code, safe='')}",
            timeout_ms=timeout_ms,
        )

    def cancel_pairing(self, code: str) -> dict:





        body = json_body({"code": code, "product": "ai-edit"})
        return self._request(
            "POST",
            "/api/plugin/pair/cancel",
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.quick_post_timeout_ms", _TIMEOUT_QUICK_POST_MS),
        )

    def send_telemetry_batch(self, events: list, auth: dict) -> dict:

        body = json_body({"events": events})
        return self._request(
            "POST",
            "/api/plugin/track",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.quick_post_timeout_ms", _TIMEOUT_QUICK_POST_MS),
        )

    def refund_generation(
        self, request_id: str, reason: str, auth: dict, error_code: str | None = None
    ) -> dict:







        payload = {"request_id": request_id, "reason": reason}
        if error_code:
            payload["error_code"] = error_code
        body = json_body(payload)
        return self._request(
            "POST",
            "/api/ai-edit/generate/refund",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def analyze_pixels(
        self, auth: dict, kind: str, width: int, height: int, rgb: str, seg_hint: bool = False
    ) -> dict:



        payload = {"kind": kind, "width": int(width), "height": int(height), "rgb": rgb}
        if seg_hint:
            payload["seg_hint"] = True
        return self._request(
            "POST",
            "/api/ai-edit/analyze-pixels",
            auth=auth,
            body=json_body(payload),
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def get_export_size(self, auth: dict, inputs: dict) -> dict:



        return self._request(
            "POST",
            "/api/ai-edit/export-size",
            auth=auth,
            body=json_body(inputs),
            timeout_ms=get_export_dial("pipeline.terralab_client.quick_post_timeout_ms", _TIMEOUT_QUICK_POST_MS),
        )

    def analyze_palette(self, auth: dict, total: int, bins: list) -> dict:


        return self._request(
            "POST",
            "/api/ai-edit/analyze-pixels",
            auth=auth,
            body=json_body({"kind": "palette", "total": int(total), "bins": bins}),
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def download_image(self, url: str) -> bytes:

        from .image_download import download_image

        return download_image(url, BlockingRequest)



    def _request(
        self,
        method: str,
        path: str,
        auth: dict | None = None,
        body: bytes | None = None,
        timeout_ms: int | None = None,
    ) -> dict:





        if _is_feedback_cancelled(_current_feedback()):
            return _cancelled_result()
        if method not in ("GET", "POST"):
            return {"error": "Unsupported request method", "code": "CLIENT_ERROR"}
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            return {"error": "Invalid request path", "code": "CLIENT_ERROR"}
        if auth is not None and not valid_headers(auth):
            return {"error": "Invalid request headers", "code": "CLIENT_ERROR"}
        timeout_ms = transfer_timeout(_api_timeout_ms() if timeout_ms is None else timeout_ms, _TIMEOUT_API)
        url = f"{self.base_url}{path}"
        if not valid_http_url(url):
            return {"error": "Invalid request URL", "code": "CLIENT_ERROR"}
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QtC.RedirectPolicyAttribute, QtC.NoLessSafeRedirectPolicy)
        req.setRawHeader(b"Content-Type", b"application/json")
        req.setRawHeader(b"Accept", b"application/json")
        QtC.set_transfer_timeout(req, timeout_ms)
        if auth:
            for key, value in auth.items():
                req.setRawHeader(key.encode("utf-8"), value.encode("utf-8"))
        context_header = _client_context_value()
        if context_header:
            req.setRawHeader(b"X-Client-Context", context_header)
        trace_id = uuid.uuid4().hex
        req.setRawHeader(b"X-Client-Request-ID", trace_id.encode("ascii"))
        feedback = _current_feedback()
        blocker = BlockingRequest()
        started = time.perf_counter()
        if method == "GET":
            err = blocker.get(req, forceRefresh=True, feedback=feedback)
        else:
            payload = QByteArray(body) if body else QByteArray()
            err = blocker.post(req, payload, feedback=feedback)
        if _is_feedback_cancelled(feedback):
            return _cancelled_result()
        reply = blocker.reply()
        status = _safe_int(reply.attribute(QtC.HttpStatusCodeAttribute)) if reply is not None else None
        raw = bytes(reply.content()) if reply is not None else b""
        log_debug(
            f"API request id={trace_id} method={method} status={status} "
            f"duration_ms={int((time.perf_counter() - started) * 1000)} bytes={len(raw)}"
        )
        parsed = response_object(raw) if raw else None
        if _reply_failed(err, blocker):
            if status is not None and status >= 400 and parsed is not None and "error" in parsed:
                return http_failure(parsed, status, reply.rawHeader(b"Retry-After"))
            code, msg = _classify_network_error(blocker, timeout_ms)
            result = {"error": msg, "code": code}
            if status is not None and status >= 400:
                return http_failure(result, status, reply.rawHeader(b"Retry-After"))
            return result
        if status is not None and status >= 400:
            log_warning(f"API request failed id={trace_id} status={status}")
            return http_failure(parsed, status, reply.rawHeader(b"Retry-After"))

        if status is None or not 200 <= status < 300 or status == 206:
            return {"error": "Invalid server response", "code": "SERVER_ERROR"}
        if not raw and status == 204:
            return {}
        if parsed is None:
            log_warning(f"Invalid API response id={trace_id} bytes={len(raw)}")
            return {"error": "Invalid server response", "code": "SERVER_ERROR"}
        return parsed


def _get_submit_timeout_ms(resolution: str) -> int:







    try:
        from ..core.config_store import get_store
        store = get_store()
        cfg = store.get_server_export_config() if store is not None else None
        if cfg:
            server_map = cfg.get("submit_timeouts_ms")
            if isinstance(server_map, dict):
                val = server_map.get(resolution)
                return transfer_timeout(val, _SUBMIT_TIMEOUTS_MS.get(resolution, _TIMEOUT_API))
    except Exception:  # nosec B110
        pass
    return _SUBMIT_TIMEOUTS_MS.get(resolution, _TIMEOUT_API)
