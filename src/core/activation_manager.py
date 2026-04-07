"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""

from typing import Tuple
from qgis.core import QgsSettings, QgsApplication

SETTINGS_PREFIX = "AIEdit/"
SUBSCRIBE_URL = "https://terra-lab.ai/ai-edit"

# Localized strings — en / fr / es / pt
_STRINGS = {
    "en": {
        # Activation
        "enter_key": "Please enter your activation key.",
        "invalid_format": "Invalid key format. Keys start with tl_pro_",
        "no_connection": "Cannot reach server. Check your internet connection.",
        "invalid_key": "Invalid activation key. Check your key and try again.",
        "subscription_expired": "Your subscription has expired or been canceled. Renew at terra-lab.ai/dashboard",
        "validation_failed": "Validation failed.",
        "key_verified": "Activation key verified!",
        "consent_title": "Terms & Privacy",
        "consent_text": (
            "By using AI Edit, your raster images will be sent to our secure cloud "
            "processing servers for AI analysis. Images are processed "
            "in real-time and immediately discarded after processing.\n\n"
            "By continuing, you agree to our Terms of Sale and Privacy Policy."
        ),
        "consent_terms_link": "Terms of Sale",
        "consent_privacy_link": "Privacy Policy",
        "consent_accept": "I Agree",
        "consent_decline": "Cancel",
        # Dock widget
        "dock_title": "AI Edit by TerraLab",
        "select_area": "Select an Area to Edit with AI:",
        "start_ai_edit": "Start AI Edit",
        "click_drag": "Click and drag to select your edit area",
        "what_change": "What should AI change?",
        "prompt_placeholder": "Type your prompt or use a template below...",
        "prompt_templates": "\u25bc Prompt Templates",
        "generate": "Generate",
        "stop": "Stop",
        "preparing": "Preparing...",
        "no_visible_layer": "No visible layer. Add imagery to your project.",
        "activate_title": "Activate AI Edit",
        "get_key": "Get your activation key",
        "paste_key": "Then paste your activation key:",
        "activate": "Activate",
        "enter_code": "Enter your code",
        "change_key": "Change key",
        "report_bug": "Report a bug",
        "tutorial": "Tutorial",
        "about_us": "About us",
        "trial_exhausted_info": (
            "AI Edit runs on cloud AI infrastructure with real "
            "costs. Your subscription helps keep the plugin open source."
        ),
        "subscribe_link": "Subscribe at terra-lab.ai",
        # Plugin
        "ai_edit": "AI Edit",
        "ai_edit_tooltip": "AI Edit by TerraLab\nAI-powered image editing for geospatial data",
        "zone_too_small": "Selected zone too small (min 50x50px)",
        "no_zone": "No zone selected",
        "export_error": "Export error: {error}",
        "layer_added": "Layer added: {name}",
        "error_adding_layer": "Error adding layer: {error}",
        # Presets
        "remove_clouds": "Remove clouds",
        "remove_shadows": "Remove shadows",
        "remove_trees": "Remove trees",
        "remove_water": "Remove water",
        "remove_haze": "Remove haze",
        "add_trees": "Add trees",
        "add_buildings": "Add buildings",
        "add_solar_panels": "Add solar panels",
        "add_road": "Add road",
        "add_park": "Add park",
        "add_crops": "Add crops",
    },
    "fr": {
        # Activation
        "enter_key": "Veuillez entrer votre clé d'activation.",
        "invalid_format": "Format de clé invalide. Les clés commencent par tl_pro_",
        "no_connection": "Impossible de contacter le serveur. Vérifiez votre connexion internet.",
        "invalid_key": "Clé d'activation invalide. Vérifiez votre clé et réessayez.",
        "subscription_expired": "Votre abonnement a expiré ou a été annulé. Renouvelez sur terra-lab.ai/dashboard",
        "validation_failed": "Échec de la validation.",
        "key_verified": "Clé d'activation vérifiée !",
        "consent_title": "Conditions & Confidentialité",
        "consent_text": (
            "En utilisant AI Edit, vos images raster seront envoyées à nos serveurs "
            "cloud sécurisés pour analyse par IA. Les images sont "
            "traitées en temps réel et immédiatement supprimées après traitement.\n\n"
            "En continuant, vous acceptez nos Conditions Générales de Vente et notre "
            "Politique de Confidentialité."
        ),
        "consent_terms_link": "Conditions Générales de Vente",
        "consent_privacy_link": "Politique de Confidentialité",
        "consent_accept": "J'accepte",
        "consent_decline": "Annuler",
        # Dock widget
        "dock_title": "AI Edit par TerraLab",
        "select_area": "Sélectionnez une zone à modifier avec l'IA :",
        "start_ai_edit": "Démarrer AI Edit",
        "click_drag": "Cliquez et glissez pour sélectionner votre zone",
        "what_change": "Que doit modifier l'IA ?",
        "prompt_placeholder": "Décrivez la modification ou utilisez un modèle ci-dessous...",
        "prompt_templates": "\u25bc Modèles de prompt",
        "generate": "Générer",
        "stop": "Arrêter",
        "preparing": "Préparation...",
        "no_visible_layer": "Aucune couche visible. Ajoutez une image à votre projet.",
        "activate_title": "Activer AI Edit",
        "get_key": "Obtenir votre clé d'activation",
        "paste_key": "Puis collez votre clé d'activation :",
        "activate": "Activer",
        "enter_code": "Entrez votre code",
        "change_key": "Changer de clé",
        "report_bug": "Signaler un bug",
        "tutorial": "Tutoriel",
        "about_us": "À propos",
        "trial_exhausted_info": (
            "AI Edit fonctionne sur une infrastructure cloud avec des coûts réels. "
            "Votre abonnement contribue à maintenir le plugin open source."
        ),
        "subscribe_link": "S'abonner sur terra-lab.ai",
        # Plugin
        "ai_edit": "AI Edit",
        "ai_edit_tooltip": "AI Edit par TerraLab\nÉdition d'images géospatiales par IA",
        "zone_too_small": "Zone sélectionnée trop petite (min 50x50px)",
        "no_zone": "Aucune zone sélectionnée",
        "export_error": "Erreur d'export : {error}",
        "layer_added": "Couche ajoutée : {name}",
        "error_adding_layer": "Erreur d'ajout de couche : {error}",
        # Presets
        "remove_clouds": "Supprimer les nuages",
        "remove_shadows": "Supprimer les ombres",
        "remove_trees": "Supprimer les arbres",
        "remove_water": "Supprimer l'eau",
        "remove_haze": "Supprimer la brume",
        "add_trees": "Ajouter des arbres",
        "add_buildings": "Ajouter des bâtiments",
        "add_solar_panels": "Ajouter des panneaux solaires",
        "add_road": "Ajouter une route",
        "add_park": "Ajouter un parc",
        "add_crops": "Ajouter des cultures",
    },
    "es": {
        # Activation
        "enter_key": "Ingrese su clave de activación.",
        "invalid_format": "Formato de clave inválido. Las claves comienzan con tl_pro_",
        "no_connection": "No se puede contactar al servidor. Verifique su conexión a internet.",
        "invalid_key": "Clave de activación inválida. Verifique su clave e intente de nuevo.",
        "subscription_expired": "Su suscripción ha expirado o fue cancelada. Renueve en terra-lab.ai/dashboard",
        "validation_failed": "Validación fallida.",
        "key_verified": "¡Clave de activación verificada!",
        "consent_title": "Términos y Privacidad",
        "consent_text": (
            "Al usar AI Edit, sus imágenes ráster serán enviadas a nuestros servidores "
            "cloud seguros para análisis por IA. Las imágenes "
            "se procesan en tiempo real y se eliminan inmediatamente después del procesamiento.\n\n"
            "Al continuar, acepta nuestros Términos de Venta y Política de Privacidad."
        ),
        "consent_terms_link": "Términos de Venta",
        "consent_privacy_link": "Política de Privacidad",
        "consent_accept": "Acepto",
        "consent_decline": "Cancelar",
        # Dock widget
        "dock_title": "AI Edit por TerraLab",
        "select_area": "Seleccione un área para editar con IA:",
        "start_ai_edit": "Iniciar AI Edit",
        "click_drag": "Haga clic y arrastre para seleccionar su área de edición",
        "what_change": "¿Qué debe cambiar la IA?",
        "prompt_placeholder": "Escriba su instrucción o use una plantilla abajo...",
        "prompt_templates": "\u25bc Plantillas de prompt",
        "generate": "Generar",
        "stop": "Detener",
        "preparing": "Preparando...",
        "no_visible_layer": "Ninguna capa visible. Agregue una imagen a su proyecto.",
        "activate_title": "Activar AI Edit",
        "get_key": "Obtener su clave de activación",
        "paste_key": "Luego pegue su clave de activación:",
        "activate": "Activar",
        "enter_code": "Ingrese su código",
        "change_key": "Cambiar clave",
        "report_bug": "Reportar un error",
        "tutorial": "Tutorial",
        "about_us": "Acerca de nosotros",
        "trial_exhausted_info": (
            "AI Edit funciona en infraestructura cloud con costos reales. "
            "Su suscripción ayuda a mantener el plugin de código abierto."
        ),
        "subscribe_link": "Suscribirse en terra-lab.ai",
        # Plugin
        "ai_edit": "AI Edit",
        "ai_edit_tooltip": "AI Edit por TerraLab\nEdición de imágenes geoespaciales con IA",
        "zone_too_small": "Zona seleccionada demasiado pequeña (mín 50x50px)",
        "no_zone": "Ninguna zona seleccionada",
        "export_error": "Error de exportación: {error}",
        "layer_added": "Capa añadida: {name}",
        "error_adding_layer": "Error al añadir capa: {error}",
        # Presets
        "remove_clouds": "Eliminar nubes",
        "remove_shadows": "Eliminar sombras",
        "remove_trees": "Eliminar árboles",
        "remove_water": "Eliminar agua",
        "remove_haze": "Eliminar neblina",
        "add_trees": "Añadir árboles",
        "add_buildings": "Añadir edificios",
        "add_solar_panels": "Añadir paneles solares",
        "add_road": "Añadir carretera",
        "add_park": "Añadir parque",
        "add_crops": "Añadir cultivos",
    },
    "pt": {
        # Activation
        "enter_key": "Insira sua chave de ativação.",
        "invalid_format": "Formato de chave inválido. As chaves começam com tl_pro_",
        "no_connection": "Não foi possível contatar o servidor. Verifique sua conexão com a internet.",
        "invalid_key": "Chave de ativação inválida. Verifique sua chave e tente novamente.",
        "subscription_expired": "Sua assinatura expirou ou foi cancelada. Renove em terra-lab.ai/dashboard",
        "validation_failed": "Falha na validação.",
        "key_verified": "Chave de ativação verificada!",
        "consent_title": "Termos e Privacidade",
        "consent_text": (
            "Ao usar o AI Edit, suas imagens raster serão enviadas aos nossos servidores "
            "cloud seguros para análise por IA. As imagens são "
            "processadas em tempo real e descartadas imediatamente após o processamento.\n\n"
            "Ao continuar, você concorda com nossos Termos de Venda e Política de Privacidade."
        ),
        "consent_terms_link": "Termos de Venda",
        "consent_privacy_link": "Política de Privacidade",
        "consent_accept": "Eu Concordo",
        "consent_decline": "Cancelar",
        # Dock widget
        "dock_title": "AI Edit por TerraLab",
        "select_area": "Selecione uma área para editar com IA:",
        "start_ai_edit": "Iniciar AI Edit",
        "click_drag": "Clique e arraste para selecionar sua área de edição",
        "what_change": "O que a IA deve alterar?",
        "prompt_placeholder": "Digite sua instrução ou use um modelo abaixo...",
        "prompt_templates": "\u25bc Modelos de prompt",
        "generate": "Gerar",
        "stop": "Parar",
        "preparing": "Preparando...",
        "no_visible_layer": "Nenhuma camada visível. Adicione uma imagem ao seu projeto.",
        "activate_title": "Ativar AI Edit",
        "get_key": "Obter sua chave de ativação",
        "paste_key": "Depois cole sua chave de ativação:",
        "activate": "Ativar",
        "enter_code": "Insira seu código",
        "change_key": "Alterar chave",
        "report_bug": "Reportar um erro",
        "tutorial": "Tutorial",
        "about_us": "Sobre nós",
        "trial_exhausted_info": (
            "O AI Edit funciona em infraestrutura cloud com custos reais. "
            "Sua assinatura ajuda a manter o plugin de código aberto."
        ),
        "subscribe_link": "Assinar em terra-lab.ai",
        # Plugin
        "ai_edit": "AI Edit",
        "ai_edit_tooltip": "AI Edit por TerraLab\nEdição de imagens geoespaciais com IA",
        "zone_too_small": "Zona selecionada muito pequena (mín 50x50px)",
        "no_zone": "Nenhuma zona selecionada",
        "export_error": "Erro de exportação: {error}",
        "layer_added": "Camada adicionada: {name}",
        "error_adding_layer": "Erro ao adicionar camada: {error}",
        # Presets
        "remove_clouds": "Remover nuvens",
        "remove_shadows": "Remover sombras",
        "remove_trees": "Remover árvores",
        "remove_water": "Remover água",
        "remove_haze": "Remover neblina",
        "add_trees": "Adicionar árvores",
        "add_buildings": "Adicionar edifícios",
        "add_solar_panels": "Adicionar painéis solares",
        "add_road": "Adicionar estrada",
        "add_park": "Adicionar parque",
        "add_crops": "Adicionar culturas",
    },
}


def _get_locale() -> str:
    """Detect QGIS locale, return language code."""
    try:
        locale = QgsApplication.instance().locale()
        if locale:
            lang = locale[:2]
            if lang in _STRINGS:
                return lang
    except Exception:
        pass
    return "en"


def tr(key: str) -> str:
    """Get a localized string for the current QGIS locale."""
    locale = _get_locale()
    strings = _STRINGS.get(locale, _STRINGS["en"])
    return strings.get(key, _STRINGS["en"].get(key, key))


def is_activated(settings=None) -> bool:
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}activated", False, type=bool)


def get_activation_key(settings=None) -> str:
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}activation_key", "")


def has_consent(settings=None) -> bool:
    """Check if the user has accepted terms and privacy policy."""
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)


def save_consent(settings=None):
    """Mark that the user accepted terms and privacy policy."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}consent_accepted", True)


def save_activation(key: str, settings=None):
    """Save activation key and mark as activated."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}activation_key", key.strip())
    s.setValue(f"{SETTINGS_PREFIX}activated", True)


def clear_activation(settings=None):
    """Clear activation state (e.g. when key becomes invalid)."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}activation_key", "")
    s.setValue(f"{SETTINGS_PREFIX}activated", False)


def validate_key_with_server(client, key: str) -> Tuple[bool, str]:
    """Validate an activation key against the server.

    Returns (success, message).
    """
    key = key.strip()
    if not key:
        return False, tr("enter_key")

    if not key.startswith("tl_pro_"):
        return False, tr("invalid_format")

    # Call /api/plugin/usage with the key as Bearer token
    auth = {
        "Authorization": f"Bearer {key}",
        "X-Product-ID": "ai-edit",
    }
    try:
        result = client.get_usage(auth=auth)
    except Exception:
        return False, tr("no_connection")

    if "error" in result:
        code = result.get("code", "")
        if code == "INVALID_KEY":
            return False, tr("invalid_key")
        if code == "SUBSCRIPTION_INACTIVE":
            return False, tr("subscription_expired")
        return False, result.get("error", tr("validation_failed"))

    return True, tr("key_verified")


def get_subscribe_url() -> str:
    return SUBSCRIBE_URL
