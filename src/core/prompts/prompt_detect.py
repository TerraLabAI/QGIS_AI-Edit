"""Free-form prompt intent detectors (vector CTA, seg context, guidance).

Split out of prompt_presets.py (which re-exports the three detect_* entry
points so existing callers keep importing through the facade).

Every table here mirrors a rule the server applies to the same prompt, and
nothing enforced that they stayed in step: a server-side wording change meant
a plugin release before the two agreed again. Each table now also accepts
served patterns, added to the shipped ones and never replacing them, so the
two sides can be brought back in step with a deploy. The colour the CTA shows
is served the same way, for the same reason.

These run on every keystroke, so a served pattern is compiled once per served
list, one that does not compile is dropped, and none of them is ever handed
more than a bounded slice of the prompt.
"""
from __future__ import annotations

import re

from ..config_store import get_export_dial_seq, get_export_dial_str

# Bounds on the served side. Twelve patterns is far more than a rule needs, a
# pattern longer than this is not a phrasing but a program, and no served
# pattern ever sees more than the first part of a prompt: runtime then has a
# ceiling whatever the pattern does, which no amount of inspecting it would
# give us.
_MAX_SERVED_PATTERNS = 12
_MAX_PATTERN_CHARS = 120
_MAX_SERVED_SCAN_CHARS = 1000
# Nested repetition ("(a+)+", "(ab*)*"): the shape whose runtime explodes on a
# long input. Cheap to spot, and no legitimate phrasing rule needs it.
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*{]|[+*]{2,}")

_compiled_extras: dict[tuple[str, tuple[str, ...]], tuple] = {}


def _served_patterns(cfg_key: str) -> tuple:
    """Extra patterns served under ``cfg_key``, compiled.

    Cached against the exact list served, so a keystroke costs a dict lookup
    and a config refresh recompiles once. Anything that does not compile, is
    over-long, or repeats a repetition is dropped: a bad entry costs its own
    rule and never the prompt box."""
    raw = get_export_dial_seq(cfg_key, (), max_len=_MAX_SERVED_PATTERNS)
    if not raw:
        return ()
    key = (cfg_key, raw)
    cached = _compiled_extras.get(key)
    if cached is not None:
        return cached
    compiled = []
    for pattern in raw:
        if len(pattern) > _MAX_PATTERN_CHARS:
            continue
        if _NESTED_QUANTIFIER_RE.search(pattern):
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except (re.error, RecursionError, OverflowError, ValueError):
            continue
    out = tuple(compiled)
    # A cap on the cache too: the config changes rarely, and an unbounded dict
    # here would be a slow leak across a long session.
    if len(_compiled_extras) < 32:
        _compiled_extras[key] = out
    return out


def _matches(rx, cfg_key: str, text: str) -> bool:
    """True when the shipped pattern matches, or any served one does."""
    if rx.search(text):
        return True
    if not text:
        return False
    head = text[:_MAX_SERVED_SCAN_CHARS]
    for extra in _served_patterns(cfg_key):
        try:
            if extra.search(head):
                return True
        except Exception:  # nosec B112 - a served rule is never worth a crash
            continue
    return False

# Free-form detection-intent matcher. Mirrors the server-side preprompt rule
# that paints a 2-color #FF0000 / #FFFFFF map when a prompt asks to segment,
# detect, or vectorize ONE feature type without naming colors. Keep these
# regexes in sync with the website preprompt; both must trigger on the same
# prompts, otherwise the swatch color in the CTA will not match what the
# model actually paints.
#
# Coverage is en / fr / es / pt (the four user-prompt languages we support).
# Stems are written so a single match captures infinitive, imperative, and
# past-participle conjugations (segment / segments / segmenting / segmenter /
# segmente / segmenté / segmentar / segmenta / segmentado / etc.).
#
# A phrasing missing from a table can be added under prompt_rules.detect_verb
# without a release; see _matches above.


_VERB_TAIL = r"[a-zçéèêàôîïùûœáâãíóôõúüñ]*"  # any conjugation / suffix

_FREEFORM_DETECT_VERB_RX = re.compile(
    r"\b("
    # segment / segmenter / segmentar (en/fr/es/pt)
    r"segment|"
    # detect / detection / détecter / detectar (en/fr/es/pt, accent optional)
    r"d[eé]tect|"
    # find / found / trouver / encontrar / encuentr (en/fr/es/pt)
    r"find|found|trouv|encontr|encuentr|"
    # locate / localiser / localizar (en/fr/es/pt)
    r"locat|localis|localiz|"
    # identify / identifier / identificar (en/fr/es/pt)
    r"identif|"
    # extract / extraire / extrait / extraer / extrair (en/fr/es/pt)
    r"extract|extrai|extra[íe][rtz]?|"
    # isolate / isoler / aislar / isolar (en/fr/es/pt)
    r"isolat|isol|aisl|"
    # mask / masquer / mascarar / enmascarar (en/fr/es/pt)
    r"mask|masqu|mascar|enmascar|"
    # outline / contourer / contornear / contornar (en/fr/es/pt)
    r"outlin|contour|contorn|"
    # highlight / surligner / resaltar / destacar (en/fr/es/pt)
    r"highlight|surlign|resalt|destac|"
    # trace / tracer / trazar / traçar (en/fr/es/pt)
    r"trac|traz|traç|"
    # delineate / délimiter / delimitar (en/fr/es/pt)
    r"delineat|d[eé]limit|"
    # vectorize / vectoriser / vectorizar / vetorizar (pt drops c) +
    # typo variants (vecorize, vetorize, vectorise). [ct]{1,2} catches "ct",
    # "t" (pt vetorizar), and "c" (vecoriz typo).
    r"v[ea][ct]{1,2}or[iy][sz]|"
    # polygonize / polygoniser / poligonizar (en/fr/es/pt)
    r"polygoni[sz]|poligoni[sz]|"
    # demarcate / démarquer / demarcar (fr/es/pt mainly)
    r"demarc|d[eé]marqu|"
    # mark / marquer / marcar (last because broad, but covered by tail guards)
    r"marqu|marc"
    r")" + _VERB_TAIL + r"\b",
    re.IGNORECASE,
)

_FREEFORM_COLOR_OR_HEX_RX = re.compile(
    r"#[0-9A-Fa-f]{3,8}\b|"
    r"\b("
    # english
    r"red|white|black|blue|green|yellow|orange|pink|purple|gray|grey|"
    r"brown|beige|magenta|cyan|silver|gold|golden|"
    # french (with optional plural/feminine endings)
    r"rouges?|blan[cs]he?s?|noires?|bleu(?:e|s|es)?|verte?s?|jaunes?|"
    r"oranges?|roses?|violet(?:te|s|tes)?|grise?s?|marrons?|bruns?|brunes?|"
    r"argent[ée]e?s?|dor[ée]e?s?|mauves?|"
    # spanish
    r"rojos?|blancos?|blancas?|negros?|negras?|azules?|verdes?|amarillos?|amarillas?|"
    r"naranjas?|rosas?|morados?|moradas?|marr[oó]n(?:es)?|grises?|plateados?|"
    # portuguese
    r"vermelhos?|vermelhas?|brancos?|brancas?|pretos?|pretas?|amarelos?|amarelas?|"
    r"laranjas?|roxos?|roxas?|cinzas?|marrons?|castanhos?|castanhas?|"
    r"dourados?|prateados?"
    r")\b",
    re.IGNORECASE,
)

# Land cover / land use phrasing. When present, the server applies a 4-class
# default (red urban, green vegetation, blue water, gray bare) so the
# single-color CTA does not fit. Skip those. Multi-class enumerations
# ("classify into", "classes :", "categorias :") also skip because the server
# respects user-listed classes and may paint multiple colors.
_FREEFORM_LULC_RX = re.compile(
    r"\b("
    # english
    r"land[- ]?use|land[- ]?cover|landuse|landcover|lulc|"
    # french
    r"occupation\s+du\s+sol|occupation\s+des\s+sols|usage\s+du\s+sol|"
    r"utilisation\s+des\s+sols|couverture\s+du\s+sol|couverture\s+des\s+sols|"
    # spanish
    r"uso\s+del\s+suelo|cobertura\s+del\s+suelo|"
    # portuguese
    r"uso\s+do\s+solo|cobertura\s+do\s+solo|mapeamento\s+de\s+uso|"
    # multi-class hints in all 4 langs
    r"classif|classes\s*:|cat[ée]gories\s*:|categorias\s*:|categor[íi]as\s*:"
    r")",
    re.IGNORECASE,
)

# Inferred output color when the server falls back to the 2-color default.
_FREEFORM_VECTOR_COLOR = "#FF0000"
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def freeform_vector_color() -> str:
    """The color the CTA swatch shows for an inferred 2-color output.

    Served, because it has to equal what the server actually paints: if that
    default ever moves, the swatch has to move with it the same day or the CTA
    offers a color the raster does not contain. Anything that is not a six
    digit hex color keeps the shipped one."""
    served = get_export_dial_str("prompt_rules.vector_color", _FREEFORM_VECTOR_COLOR)
    return served.upper() if _HEX_COLOR_RE.match(served) else _FREEFORM_VECTOR_COLOR


def detect_freeform_vector_intent(prompt_text: str) -> str | None:
    """Return the inferred output color when a free-form prompt looks like a
    single-target detection, segmentation, or vectorization request. Returns
    None when the prompt names colors, hex codes, or land cover keywords
    (those bypass the server's 2-color default so the CTA swatch would not
    match what the model paints).

    Call this only after lookup_template_by_prompt returns None, so a real
    preset match always wins. Stays in sync with the server preprompt in the
    website config; update both together.
    """
    if not prompt_text:
        return None
    text = prompt_text.strip()
    if not text:
        return None
    if _matches(_FREEFORM_LULC_RX, "prompt_rules.lulc", text):
        return None
    if _matches(_FREEFORM_COLOR_OR_HEX_RX, "prompt_rules.color_word", text):
        return None
    if not _matches(_FREEFORM_DETECT_VERB_RX, "prompt_rules.detect_verb", text):
        return None
    return freeform_vector_color()


# Flat-color / map-style phrasing that neither the detect-verb nor the LULC
# matcher covers ("solid flat colours", "binary mask", "semantic map", ...).
# Grounded in the manual segmentation prompts observed in production.
_SEG_CONTEXT_STYLE_RX = re.compile(
    r"\b(flat|solid|uniform)\s+colou?rs?|couleurs?\s+(unies?|plates?)|aplats?|"
    r"colores?\s+(planos?|s[óo]lidos?)|cores?\s+(chapadas?|s[óo]lidas?)|"
    r"binary\s+(mask|map)|semantic\s+(map|segmentation)|worldcover|palette",
    re.IGNORECASE,
)


def detect_seg_context(prompt_text: str) -> bool:
    """True when the prompt reads like a segmentation / land-cover /
    color-classification request, regardless of named colors or class counts.

    Deliberately broader than detect_freeform_vector_intent (which must
    predict the exact color the server paints): this flag only RELAXES the
    flat-output detector on the downloaded result (vectorize_detect), so
    recall beats precision. It never surfaces the CTA on its own.
    """
    if not prompt_text:
        return False
    text = prompt_text.strip()
    if not text:
        return False
    return (
        _matches(_FREEFORM_LULC_RX, "prompt_rules.lulc", text)
        or _matches(_FREEFORM_DETECT_VERB_RX, "prompt_rules.detect_verb", text)
        or _matches(_SEG_CONTEXT_STYLE_RX, "prompt_rules.seg_style", text)
    )


# Off-rails prompt guidance. Detects, with high precision, the ways users
# misuse the tool so the UI can show a soft, non-blocking hint that steers
# them back onto a path that produces a good result. Grounded in real user
# prompts: ~11% ask for a vector file / digitization, many ask for
# measurements or counts, some talk to it like a chatbot. All of those
# disappoint as a plain image edit.
#
# Precision over recall on purpose: a false positive nags a user whose prompt
# was actually fine, which is worse than staying silent. Valid instructions
# (find / detect / segment / add / remove ...) must NEVER trigger a hint.

# User wants a vector FILE / digitization, not an image. The redirect points
# at the existing "Vectorize this result" CTA. Anchored on explicit format
# names, digitize verbs, "... as polygons", and coordinate requests so plain
# edit prompts ("draw buildings") never match.
_GUIDANCE_VECTOR_FILE_RX = re.compile(
    r"\.shp\b|\bshapefile|\bshape\s?file|\bshape\s?data|"
    r"\bgeojson\b|\bgeo-?json\b|\.kml\b|\bkml\b|\.dxf\b|\bdxf\b|"
    r"\bvector\s+file\b|fichier\s+vecteur|archivo\s+vectorial|arquivo\s+vetorial|"
    r"\bdigiti[sz]\w*|\bdigitali[sz]\w*|\bnum[ée]ris\w*|"
    # transform verb (any en/fr/es/pt conjugation) ... to ... vector/shapefile/polygons
    r"(?:convert\w*|export\w*|turn|transform\w*|change|passer|convert[ai]\w*|"
    r"exporta\w*|converter|converte\w*|converti\w*|cambiar|cambia\w*|mudar)"
    r"[^.\n]{0,30}\b(?:to|into|in|en|a|para|num?)\s+"
    r"(?:an?\s+|un[ae]?\s+|um[a]?\s+|des\s+|los\s+|las\s+)?"
    r"(?:vect|shapefile|pol[yíi]gon\w*)|"
    r"\b(?:to|into)\s+(?:an?\s+)?(?:[\w-]+\s+){0,3}vectors?\b|"
    r"\bvector\s+pol[yíi]gon\w*|(?:as|into|to|en|a|em)\s+pol[yíi]gon\w*|"
    # create/draw/produce ... polygons / point|line dataset
    r"(?:create|need|want|draw|make|generate|trace|produce|cr[ée]\w*|"
    r"g[ée]n[ée]r\w*|trac\w*|produi\w*|dibuj\w*|desenh\w*)"
    r"[^.\n]{0,30}\b(?:pol[yíi]gon\w*|point\s+(?:feature|dataset|layer)|"
    r"line\s+(?:feature|dataset|layer))|"
    r"(?:generate|give|return|get|extract|export|create|need)"
    r"[^.\n]{0,30}\bcoordinates?\b|\bcoordonn[ée]es\b|\bcoordenadas\b",
    re.IGNORECASE,
)

# User wants a measurement or a count of features. The model can't measure or
# count, but segment -> Vectorize -> QGIS gives area and feature count per
# polygon. Restricted to unambiguous counting words and measurement units, so
# location phrasing ("this area", "area of interest") never matches.
_GUIDANCE_MEASURE_RX = re.compile(
    # counting words across en / fr / es / pt / it / id, with conjugations.
    r"\bhow\s+many\b|\bhow\s+much\b|\bnumber\s+of\b|\bcounts?\b|\bcounting\b|"   # en
    r"\bcombien\b|\bnombre\s+d|\bcompt(?:er|ez|e-|age|é)|\bd[ée]nombr|"          # fr
    r"\bcu[áa]nt[oa]s?\b|\bn[úu]mero\s+de\b|\bcantidad\s+de\b|\bcuent[ao]s?\b|"  # es
    r"\bcont(?:ar|eo|ad[oa]s?)\b|"                                              # es contar/conteo
    r"\bquant[oa]s?\b|\bquantidade\s+de\b|\bcontagem\b|\bcont(?:ar|e[-\s])|"     # pt
    r"\bquant[ie]\b|\bnumero\s+di\b|\bjumlah\b|\bberapa\b|"                      # it / id
    # explicit measurement: units and area phrasing.
    r"\b(?:acreages?|acres|hectares?|superfic\w*)\b|"
    r"\bsquare\s+(?:met\w+|kilomet\w+)\b|\b[mk]m2\b|m²|km²|"
    r"\btotal\s+area\b|\bhow\s+much\s+area\b|\barea\s+in\s+(?:ha|m2|km2|hectares|acres)\b|"
    r"\bquelle\s+(?:est\s+)?la\s+(?:surface|superfic\w*)\b",
    re.IGNORECASE,
)

# User talks to the tool like a chatbot / GIS agent: asks about files, asks
# why it did something, asks where data came from. These phrasings essentially
# never appear in a genuine image-edit instruction, so matching is safe.
_GUIDANCE_META_QA_RX = re.compile(
    r"\b(can\s+you\s+see|do\s+you\s+see|are\s+you\s+able\s+to\s+see|"
    r"puedes\s+ver|peux-tu\s+voir|"
    r"why\s+did|why\s+is|why\s+does|pourquoi|por\s+qu[ée]|perch[ée]|"
    r"where\s+did|where\s+do\s+you|da\s+dove|de\s+d[oó]nde|"
    r"trovami|find\s+me\s+the\s+(?:file|certificate|document|name)|"
    r"what\s+is\s+the\s+name|qu'est-ce\s+que)\b",
    re.IGNORECASE,
)


def detect_prompt_guidance(prompt_text: str, has_template: bool = False) -> str | None:
    """Classify an off-rails free-form prompt for the soft guidance hint.

    Returns one of:
      "vector_file" - user asked for a shapefile / vector / digitization; the
                      tool outputs an image, so point them at Vectorize.
      "measure"     - user wants an area or a feature count; segment then
                      Vectorize, and QGIS measures/counts the polygons.
      "qa"          - user talks to the tool like a chatbot; the model paints,
                      it can't answer questions.
      None          - prompt looks like a legitimate edit instruction, or a
                      template drives it; stay silent.

    High precision by design: never returns non-None for a valid edit /
    detect / segment instruction. Used only for a non-blocking inline hint;
    generation is never blocked.
    """
    if has_template:
        return None
    text = (prompt_text or "").strip()
    if len(text) < 4:
        return None
    if _matches(_GUIDANCE_VECTOR_FILE_RX, "prompt_rules.guidance_vector_file", text):
        return "vector_file"
    if _matches(_GUIDANCE_MEASURE_RX, "prompt_rules.guidance_measure", text):
        return "measure"
    if _matches(_GUIDANCE_META_QA_RX, "prompt_rules.guidance_qa", text):
        return "qa"
    return None
