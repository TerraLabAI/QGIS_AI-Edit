"""Programmatic API for MCP integration. No UI, no dialogs.

This module provides a stable public interface for AI agents to control
AI Edit without touching the human UI.
"""
from __future__ import annotations

import os
import urllib.request

TERRALAB_SIGNUP_URL = (
    "https://terra-lab.ai/register"
    "?product=ai-edit"
    "&utm_source=qgis&utm_medium=mcp&utm_campaign=ai-edit&utm_content=signup"
)
TERRALAB_PRICING_URL = "https://terra-lab.ai/ai-edit?utm_source=qgis&utm_medium=mcp&utm_campaign=ai-edit"


class EditMCPAPI:
    """Public API for MCP/headless access to AI Edit."""

    def __init__(self, plugin):
        self._plugin = plugin

    def get_status(self) -> dict:
        """Check plugin readiness without touching UI."""
        plugin = self._plugin
        status = {"installed": True}

        if not hasattr(plugin, "_auth_manager"):
            status.update({
                "ready": False,
                "state": "NOT_INITIALIZED",
                "action_required": "Open the AI Edit panel in QGIS to initialize.",
            })
            return status

        auth = plugin._auth_manager

        if not auth.has_activation_key():
            status.update({
                "ready": False,
                "state": "NO_ACCOUNT",
                "action_required": "Create a free TerraLab account to get an activation key.",
                "steps": [
                    f"1. Go to {TERRALAB_SIGNUP_URL}",
                    "2. Create your account",
                    "3. Copy your activation key",
                    "4. Paste in AI Edit panel > Activate",
                ],
                "signup_url": TERRALAB_SIGNUP_URL,
                "free_trial": "5 generations included for free",
            })
            return status

        status["activated"] = True

        try:
            allowed, reason, error_code = auth.check_can_generate()
            if allowed:
                status.update({
                    "ready": True,
                    "state": "READY",
                    "can_generate": True,
                })
                try:
                    usage = auth.get_usage_info()
                    if "error" not in usage:
                        status["usage"] = usage
                except Exception:
                    pass  # nosec B110
            else:
                status["ready"] = False
                status["can_generate"] = False
                if error_code == "TRIAL_EXHAUSTED":
                    status.update({
                        "state": "TRIAL_EXHAUSTED",
                        "action_required": "Free trial used up. Upgrade to continue.",
                        "pricing_url": TERRALAB_PRICING_URL,
                    })
                elif error_code == "QUOTA_EXCEEDED":
                    status.update({
                        "state": "QUOTA_EXCEEDED",
                        "action_required": "Monthly quota reached.",
                        "pricing_url": TERRALAB_PRICING_URL,
                    })
                elif error_code == "SUBSCRIPTION_INACTIVE":
                    status.update({
                        "state": "SUBSCRIPTION_INACTIVE",
                        "action_required": "Subscription inactive. Reactivate on dashboard.",
                        "pricing_url": TERRALAB_PRICING_URL,
                    })
                elif error_code == "INVALID_KEY":
                    status.update({
                        "state": "INVALID_KEY",
                        "action_required": "Activation key is invalid.",
                        "signup_url": TERRALAB_SIGNUP_URL,
                    })
                else:
                    status.update({
                        "state": "CANNOT_GENERATE",
                        "reason": reason,
                        "error_code": error_code,
                    })
        except Exception:
            status.update({"ready": False, "state": "CHECK_FAILED"})

        return status

    def generate(self, prompt: str, bbox: dict, resolution: str = "1K") -> dict:
        """Run AI Edit generation. Returns structured result or error dict."""
        plugin = self._plugin

        if not hasattr(plugin, "_auth_manager"):
            return {"_error": "AI Edit not initialized. Open the AI Edit panel first."}

        auth_mgr = plugin._auth_manager
        if not auth_mgr.has_activation_key():
            return {"_error": f"AI Edit not activated. Create account at {TERRALAB_SIGNUP_URL}"}

        allowed, reason, code = auth_mgr.check_can_generate()
        if not allowed:
            if code in ("TRIAL_EXHAUSTED", "QUOTA_EXCEEDED"):
                return {"_error": f"Cannot generate: {reason}. Upgrade at {TERRALAB_PRICING_URL}"}
            return {"_error": f"Cannot generate: {reason}"}

        # Import canvas_exporter and raster_writer from the plugin package
        try:
            from .ui.canvas_exporter import export_canvas_zone, has_server_config
            from .ui.raster_writer import add_geotiff_to_project, write_geotiff
        except ImportError:
            return {"_error": "AI Edit internal modules not accessible."}

        if not has_server_config():
            return {"_error": "AI Edit server config not loaded. Open the AI Edit panel to initialize."}

        # Export canvas zone
        from qgis.core import QgsRectangle
        from qgis.utils import iface

        extent = QgsRectangle(bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"])
        map_settings = iface.mapCanvas().mapSettings()

        try:
            image_b64, img_w, img_h, actual_extent = export_canvas_zone(
                map_settings, extent, target_resolution=resolution
            )
        except Exception as e:
            return {"_error": f"Canvas export failed: {str(e)}"}

        extent_dict = {
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        }
        crs_wkt = map_settings.destinationCrs().toWkt()

        # Run generation (blocking)
        auth = auth_mgr.get_auth_header()
        gen_service = plugin._generation_service

        try:
            result = gen_service.generate(
                image_b64=image_b64,
                prompt=prompt,
                auth=auth,
                suggested_resolution=resolution,
                aspect_ratio="auto",
                on_progress=lambda *a: None,
            )
        except Exception as e:
            return {"_error": f"Generation failed: {str(e)}"}

        if not result.success:
            return {"_error": result.error or "AI Edit generation failed"}

        # Download result image (only allow https)
        image_url = result.image_url or ""
        if not image_url.startswith("https://"):
            return {"_error": "Invalid image URL scheme"}
        try:
            req = urllib.request.Request(image_url, headers=auth)
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
                image_data = resp.read()
        except Exception as e:
            return {"_error": f"Failed to download result: {str(e)}"}

        # Write GeoTIFF
        output_dir = os.path.join(os.path.expanduser("~"), "Documents", "AI_Edit_Output")
        os.makedirs(output_dir, exist_ok=True)

        try:
            geotiff_path = write_geotiff(
                image_data, extent_dict, crs_wkt, output_dir, prompt,
            )
        except Exception as e:
            return {"_error": f"GeoTIFF write failed: {str(e)}"}

        # Add to project
        try:
            layer = add_geotiff_to_project(geotiff_path, prompt)
        except Exception as e:
            return {"_error": f"Failed to add layer: {str(e)}"}

        return {
            "success": True,
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "geotiff_path": geotiff_path,
            "resolution": resolution,
            "extent": extent_dict,
        }

    def get_presets(self) -> dict:
        """Return all available prompt presets organized by category."""
        from .core.prompt_presets import get_all_categories
        categories = get_all_categories()
        return {"categories": categories}

    def get_credits(self) -> dict:
        """Return current usage/credits info."""
        plugin = self._plugin
        if not hasattr(plugin, "_auth_manager"):
            return {"_error": "AI Edit not initialized."}
        auth_mgr = plugin._auth_manager
        if not auth_mgr.has_activation_key():
            return {"_error": "AI Edit not activated."}
        try:
            usage = auth_mgr.get_usage_info()
            if "error" in usage:
                return {"_error": usage["error"]}
            return usage
        except Exception as e:
            return {"_error": f"Failed to fetch credits: {str(e)}"}

    def get_resolutions(self) -> dict:
        """Return available resolutions and their credit costs."""
        try:
            from .ui.canvas_exporter import _server_config
            if _server_config and "resolution_credit_costs" in _server_config:
                costs = _server_config["resolution_credit_costs"]
                return {
                    "resolutions": list(costs.keys()),
                    "credit_costs": costs,
                }
        except (ImportError, Exception):
            pass
        return {
            "resolutions": ["1K", "2K", "4K"],
            "credit_costs": {"1K": 1, "2K": 2, "4K": 4},
        }
