"""Playful progress-bar messages, curated per language.

Each language has its own set of messages instead of mechanical translations.
Direct translation of jokes (e.g. "Pixel diplomacy in progress...") reads
awkwardly, so each locale has idiomatic phrasing of the same spirit.

Three phases pace the rotation across the generation lifetime:
    EARLY (t < 0.3) - warming up
    MID   (0.3-0.75) - working
    LATE  (>= 0.75)  - finishing

The worker picks a phase by elapsed/estimated ratio and rotates within it.
"""
from __future__ import annotations

from .i18n import get_locale

_MESSAGES: dict[str, dict[str, list[str]]] = {
    "en": {
        "early": [
            "Waking up the AI...",
            "Summoning pixels...",
            "Booting the imagination engine...",
            "Warming up neural networks...",
            "Loading the good brushes...",
            "Stretching before the sprint...",
            "Calibrating the vibes...",
            "Dusting off the satellite dish...",
            "Politely knocking on the GPU...",
            "Rolling up sleeves...",
        ],
        "mid": [
            "Teaching geography to a robot...",
            "Convincing clouds to move...",
            "Negotiating with terrain...",
            "Pixel diplomacy in progress...",
            "Consulting the map gods...",
            "Rearranging atoms one by one...",
            "Whispering to satellites...",
            "Painting with math...",
            "Having a deep talk with the pixels...",
            "Redrawing reality, hold on...",
            "The AI is squinting at your map...",
            "Crunching landscapes like cereal...",
            "Rewriting cartography textbooks...",
            "Arguing with the render engine...",
            "Bending light to our will...",
            "Making the impossible merely improbable...",
            "Assembling tiny map elves...",
            "Applying imagination at scale...",
        ],
        "late": [
            "Almost there, just a few more pixels...",
            "Putting the finishing touches...",
            "Quality control in progress...",
            "One last coat of paint...",
            "Polishing the result...",
            "The AI says it's happy with this one...",
            "Just tidying up the edges...",
            "Final pixel inspection...",
            "Wrapping it up nicely...",
            "Any second now...",
        ],
    },
    "fr": {
        "early": [
            "On réveille l'IA...",
            "Invocation des pixels...",
            "Démarrage du moteur d'imagination...",
            "Échauffement des réseaux neuronaux...",
            "Sortie des bons pinceaux...",
            "On s'étire avant le sprint...",
            "Calibrage des ondes...",
            "Dépoussiérage de l'antenne satellite...",
            "On toque poliment à la porte du GPU...",
            "On retrousse les manches...",
        ],
        "mid": [
            "On enseigne la géographie à un robot...",
            "On convainc les nuages de bouger...",
            "Négociations avec le terrain...",
            "Diplomatie pixelaire en cours...",
            "Consultation des dieux de la carte...",
            "Réarrangement des atomes un par un...",
            "On chuchote aux satellites...",
            "On peint à grands coups de maths...",
            "Discussion approfondie avec les pixels...",
            "On redessine la réalité, patience...",
            "L'IA scrute votre carte...",
            "On croque les paysages comme des céréales...",
            "Réécriture des manuels de cartographie...",
            "On débat avec le moteur de rendu...",
            "On plie la lumière à notre volonté...",
            "On rend l'impossible juste improbable...",
            "Rassemblement des petits lutins cartographes...",
            "Application de l'imagination à grande échelle...",
        ],
        "late": [
            "Presque fini, encore quelques pixels...",
            "Dernières retouches en cours...",
            "Contrôle qualité en cours...",
            "Une dernière couche de peinture...",
            "Polissage du résultat...",
            "L'IA est satisfaite de celui-là...",
            "On lisse les bords...",
            "Inspection finale des pixels...",
            "On emballe joliment...",
            "Plus qu'une seconde...",
        ],
    },
    "es": {
        "early": [
            "Despertando a la IA...",
            "Invocando los píxeles...",
            "Arrancando el motor de la imaginación...",
            "Calentando las redes neuronales...",
            "Sacando los buenos pinceles...",
            "Estirando antes del sprint...",
            "Calibrando las vibras...",
            "Limpiando la antena satelital...",
            "Tocando con suavidad a la puerta de la GPU...",
            "Arremangándonos...",
        ],
        "mid": [
            "Enseñando geografía a un robot...",
            "Convenciendo a las nubes de moverse...",
            "Negociando con el terreno...",
            "Diplomacia de píxeles en marcha...",
            "Consultando a los dioses del mapa...",
            "Reordenando átomos uno por uno...",
            "Susurrando a los satélites...",
            "Pintando con matemáticas...",
            "Charla profunda con los píxeles...",
            "Redibujando la realidad, paciencia...",
            "La IA entrecierra los ojos ante tu mapa...",
            "Triturando paisajes como cereal...",
            "Reescribiendo los manuales de cartografía...",
            "Discutiendo con el motor de renderizado...",
            "Doblando la luz a nuestra voluntad...",
            "Volviendo lo imposible apenas improbable...",
            "Reuniendo a los duendes cartógrafos...",
            "Aplicando imaginación a gran escala...",
        ],
        "late": [
            "Casi listo, faltan unos píxeles...",
            "Dando los toques finales...",
            "Control de calidad en marcha...",
            "Una última mano de pintura...",
            "Puliendo el resultado...",
            "La IA está contenta con este...",
            "Afinando los bordes...",
            "Inspección final de píxeles...",
            "Empaquetando con esmero...",
            "Falta un segundo...",
        ],
    },
    "pt": {
        "early": [
            "Acordando a IA...",
            "Invocando os pixels...",
            "Ligando o motor da imaginação...",
            "Aquecendo as redes neurais...",
            "Pegando os bons pincéis...",
            "Alongando antes do sprint...",
            "Calibrando as vibrações...",
            "Tirando o pó da antena de satélite...",
            "Batendo gentilmente na porta da GPU...",
            "Arregaçando as mangas...",
        ],
        "mid": [
            "Ensinando geografia a um robô...",
            "Convencendo as nuvens a se moverem...",
            "Negociando com o terreno...",
            "Diplomacia de pixels em andamento...",
            "Consultando os deuses do mapa...",
            "Reorganizando átomos um por um...",
            "Sussurrando aos satélites...",
            "Pintando com matemática...",
            "Conversa séria com os pixels...",
            "Redesenhando a realidade, segure firme...",
            "A IA está apertando os olhos no seu mapa...",
            "Mastigando paisagens como cereal...",
            "Reescrevendo os livros de cartografia...",
            "Discutindo com o motor de renderização...",
            "Curvando a luz à nossa vontade...",
            "Tornando o impossível apenas improvável...",
            "Reunindo os duendes cartógrafos...",
            "Aplicando imaginação em larga escala...",
        ],
        "late": [
            "Quase lá, só mais alguns pixels...",
            "Dando os toques finais...",
            "Controle de qualidade em andamento...",
            "Mais uma demão de tinta...",
            "Polindo o resultado...",
            "A IA gostou desse aqui...",
            "Só ajustando as bordas...",
            "Inspeção final dos pixels...",
            "Empacotando com capricho...",
            "Mais um segundinho...",
        ],
    },
}


def _resolve_lang() -> str:
    """Return one of {"en","fr","es","pt"} based on the QGIS locale."""
    code = (get_locale() or "en").lower()
    if code in _MESSAGES:
        return code
    return "en"


def get_phase_messages(phase: str) -> list[str]:
    """Return the message pool for `phase` in the user's locale.

    `phase` must be one of "early", "mid", "late". Falls back to English
    when the locale is unsupported, and to an empty list when the phase
    name is unknown (no crash on programmer error)."""
    return list(_MESSAGES[_resolve_lang()].get(phase) or _MESSAGES["en"].get(phase) or [])
