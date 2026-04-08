"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""

import uuid
from typing import Tuple, Optional
from qgis.core import QgsSettings, QgsApplication

SETTINGS_PREFIX = "AIEdit/"
TERRALAB_PREFIX = "TerraLab/"
SUBSCRIBE_URL = "https://terra-lab.ai/ai-edit"
DASHBOARD_URL = "https://terra-lab.ai/dashboard/ai-edit"

# Hardcoded fallback config (used when server is unreachable)
DEFAULT_CONFIG = {
    "free_credits": 10,
    "free_tier_active": True,
    "promo_active": True,
    "promo_code": "EARLYBIRD",
    "upgrade_url": "https://terra-lab.ai/dashboard/ai-edit",
}

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
        "change_key_paste": "Enter your new activation key:",
        "report_bug": "Report a bug",
        "tutorial": "Tutorial",
        "about_us": "About us",
        "trial_exhausted_info": (
            "AI Edit runs on cloud AI infrastructure with real "
            "costs. Your subscription helps keep the plugin open source."
        ),
        "subscribe_link": "Subscribe at terra-lab.ai",
        # Free tier
        "free_title": "Try AI Edit for free",
        "free_subtitle": "No credit card required.",
        "free_email_placeholder": "your@email.com",
        "free_submit": "Get {credits} free AI edits",
        "free_check_email": "Check your email! Click the link to access your dashboard.",
        "free_post_signup_hint": "Paste your key from terra-lab.ai/dashboard:",
        "free_flow_info": "Enter your email, check your inbox, get your free key from the dashboard, paste it here.",
        "free_sending": "Sending...",
        "free_error_invalid_email": "Please enter a valid email address.",
        "free_error_disposable": "Please use a non-disposable email address.",
        "free_error_device_used": "Free edits already claimed on this device.",
        "free_error_already_registered": "This email already has free edits. Check your dashboard.",
        "free_error_rate_limited": "Too many attempts. Please wait a moment.",
        "free_error_send_failed": "Could not send the email. Please try again.",
        "free_exhausted": "You have used all {credits} free edits.",
        "free_promo_message": "Subscribe now. First month at 13 euros instead of 19 euros with code {code}.",
        "free_subscribe": "Subscribe now",
        "free_or_paste_key": "Already have a key? Paste it here",
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
        "change_key_paste": "Entrez votre nouvelle cle d'activation :",
        "report_bug": "Signaler un bug",
        "tutorial": "Tutoriel",
        "about_us": "À propos",
        "trial_exhausted_info": (
            "AI Edit fonctionne sur une infrastructure cloud avec des coûts réels. "
            "Votre abonnement contribue à maintenir le plugin open source."
        ),
        "subscribe_link": "S'abonner sur terra-lab.ai",
        # Free tier
        "free_title": "Essayez AI Edit gratuitement",
        "free_subtitle": "Aucune carte bancaire requise.",
        "free_email_placeholder": "votre@email.com",
        "free_submit": "Obtenir {credits} edits AI gratuits",
        "free_check_email": "Consultez votre email ! Cliquez sur le lien pour acceder a votre dashboard.",
        "free_post_signup_hint": "Collez votre cle depuis terra-lab.ai/dashboard :",
        "free_flow_info": "Entrez votre email, consultez votre boite mail, recuperez votre cle gratuite sur le dashboard, collez-la ici.",
        "free_sending": "Envoi en cours...",
        "free_error_invalid_email": "Veuillez entrer une adresse email valide.",
        "free_error_disposable": "Veuillez utiliser une adresse email non jetable.",
        "free_error_device_used": "Les edits gratuits ont deja ete utilises sur cet appareil.",
        "free_error_already_registered": "Cet email a deja des edits gratuits. Consultez votre dashboard.",
        "free_error_rate_limited": "Trop de tentatives. Veuillez patienter un moment.",
        "free_error_send_failed": "Impossible d'envoyer l'email. Veuillez reessayer.",
        "free_exhausted": "Vous avez utilise vos {credits} edits gratuits.",
        "free_promo_message": "Abonnez-vous. Premier mois a 13 euros au lieu de 19 euros avec le code {code}.",
        "free_subscribe": "S'abonner maintenant",
        "free_or_paste_key": "Vous avez deja une cle ? Collez-la ici",
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
        "change_key_paste": "Ingrese su nueva clave de activacion:",
        "report_bug": "Reportar un error",
        "tutorial": "Tutorial",
        "about_us": "Acerca de nosotros",
        "trial_exhausted_info": (
            "AI Edit funciona en infraestructura cloud con costos reales. "
            "Su suscripción ayuda a mantener el plugin de código abierto."
        ),
        "subscribe_link": "Suscribirse en terra-lab.ai",
        # Free tier
        "free_title": "Pruebe AI Edit gratis",
        "free_subtitle": "Sin tarjeta de credito.",
        "free_email_placeholder": "su@email.com",
        "free_submit": "Obtener {credits} ediciones AI gratis",
        "free_check_email": "Revise su email. Haga clic en el enlace para acceder a su dashboard.",
        "free_post_signup_hint": "Pegue su clave desde terra-lab.ai/dashboard:",
        "free_flow_info": "Ingrese su email, revise su bandeja, obtenga su clave gratuita en el dashboard, peguela aqui.",
        "free_sending": "Enviando...",
        "free_error_invalid_email": "Ingrese una direccion de email valida.",
        "free_error_disposable": "Use una direccion de email no desechable.",
        "free_error_device_used": "Las ediciones gratis ya fueron reclamadas en este dispositivo.",
        "free_error_already_registered": "Este email ya tiene ediciones gratis. Revise su dashboard.",
        "free_error_rate_limited": "Demasiados intentos. Espere un momento.",
        "free_error_send_failed": "No se pudo enviar el email. Intente de nuevo.",
        "free_exhausted": "Ha usado sus {credits} ediciones gratis.",
        "free_promo_message": "Suscribase ahora. Primer mes a 13 euros en lugar de 19 euros con el codigo {code}.",
        "free_subscribe": "Suscribirse ahora",
        "free_or_paste_key": "Ya tiene una clave? Peguela aqui",
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
        "change_key_paste": "Insira sua nova chave de ativacao:",
        "report_bug": "Reportar um erro",
        "tutorial": "Tutorial",
        "about_us": "Sobre nós",
        "trial_exhausted_info": (
            "O AI Edit funciona em infraestrutura cloud com custos reais. "
            "Sua assinatura ajuda a manter o plugin de código aberto."
        ),
        "subscribe_link": "Assinar em terra-lab.ai",
        # Free tier
        "free_title": "Experimente AI Edit gratis",
        "free_subtitle": "Sem cartao de credito.",
        "free_email_placeholder": "seu@email.com",
        "free_submit": "Obter {credits} edicoes AI gratis",
        "free_check_email": "Verifique seu email! Clique no link para acessar seu dashboard.",
        "free_post_signup_hint": "Cole sua chave de terra-lab.ai/dashboard:",
        "free_flow_info": "Insira seu email, verifique sua caixa de entrada, obtenha sua chave gratuita no dashboard, cole aqui.",
        "free_sending": "Enviando...",
        "free_error_invalid_email": "Insira um endereco de email valido.",
        "free_error_disposable": "Use um endereco de email nao descartavel.",
        "free_error_device_used": "As edicoes gratis ja foram utilizadas neste dispositivo.",
        "free_error_already_registered": "Este email ja tem edicoes gratis. Verifique seu dashboard.",
        "free_error_rate_limited": "Muitas tentativas. Aguarde um momento.",
        "free_error_send_failed": "Nao foi possivel enviar o email. Tente novamente.",
        "free_exhausted": "Voce usou suas {credits} edicoes gratis.",
        "free_promo_message": "Assine agora. Primeiro mes a 13 euros em vez de 19 euros com o codigo {code}.",
        "free_subscribe": "Assinar agora",
        "free_or_paste_key": "Ja tem uma chave? Cole aqui",
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

    if not key.startswith("tl_pro_") and not key.startswith("tl_free_"):
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


def get_dashboard_url() -> str:
    return DASHBOARD_URL


def get_tutorial_url(client=None) -> str:
    """Get tutorial URL from server config, falling back to product page."""
    config = get_server_config(client)
    return config.get("tutorial_url", "https://terra-lab.ai/ai-edit")


# -- Device ID management --

def get_device_id(settings=None) -> str:
    """Get or generate a persistent device ID."""
    s = settings or QgsSettings()
    device_id = s.value(f"{TERRALAB_PREFIX}device_id", "")
    if not device_id:
        device_id = str(uuid.uuid4())
        s.setValue(f"{TERRALAB_PREFIX}device_id", device_id)
    return device_id


# -- Cross-plugin email sharing --

def get_shared_email(settings=None) -> str:
    """Get email from shared TerraLab namespace (set by any plugin)."""
    s = settings or QgsSettings()
    return s.value(f"{TERRALAB_PREFIX}user_email", "")


def save_shared_email(email: str, settings=None):
    """Save email to shared TerraLab namespace for cross-plugin use."""
    s = settings or QgsSettings()
    s.setValue(f"{TERRALAB_PREFIX}user_email", email.strip())


# -- Server config --

_cached_config: Optional[dict] = None


def get_server_config(client=None) -> dict:
    """Fetch server-driven config, with local caching and fallback."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    if client is None:
        return DEFAULT_CONFIG

    try:
        result = client.get_config("ai-edit")
        if "error" not in result:
            _cached_config = result
            return result
    except Exception:
        pass

    return DEFAULT_CONFIG


def clear_config_cache():
    """Clear cached config (e.g. on plugin reload)."""
    global _cached_config
    _cached_config = None


# -- Magic link signup --

def send_magic_link(client, email: str) -> Tuple[bool, str]:
    """Send a magic link for free tier signup.

    Returns (success, message_key).
    """
    email = email.strip()
    if not email or "@" not in email:
        return False, "free_error_invalid_email"

    device_id = get_device_id()
    try:
        result = client.send_magic_link(email, device_id, "ai-edit")
    except Exception:
        return False, "free_error_send_failed"

    if result.get("ok"):
        save_shared_email(email)
        return True, "free_check_email"

    reason = result.get("reason", "")
    reason_map = {
        "INVALID_EMAIL": "free_error_invalid_email",
        "DEVICE_ALREADY_USED": "free_error_device_used",
        "RATE_LIMITED": "free_error_rate_limited",
        "ALREADY_REGISTERED": "free_error_already_registered",
        "EMAIL_SEND_FAILED": "free_error_send_failed",
    }
    return False, reason_map.get(reason, "free_error_send_failed")
