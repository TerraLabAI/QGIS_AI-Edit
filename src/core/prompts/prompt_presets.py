"""Prompt catalog facade backed by the server-side catalog.

The plugin no longer ships a hardcoded preset list. All presets, prompts,
and Top Picks come from /api/ai-edit/presets via prompt_presets_client.
This module wraps the cached catalog for the dialog (and other callers)
in a stable shape, falling back to empty themed shells when no cache is
available (first install, offline).
"""
from __future__ import annotations

import re
from typing import Any

from qgis.PyQt.QtCore import QSettings

from ..i18n import tr


def _normalize_for_match(s: str) -> str:
    """Collapse whitespace so reformatted prompts still match the source."""
    return re.sub(r"\s+", " ", (s or "")).strip()


_HEX_PAREN_RX = re.compile(r"\(#[0-9A-Fa-f]{3,6}\)")
_SENTENCE_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_BULLET_LINE_RX = re.compile(r"^\s*[-*•]\s+")
_ITEM_SEP_RX = re.compile(r"^\s*,?\s*(?:and\s+|et\s+|y\s+|e\s+)?", re.IGNORECASE)
_LEAD_VERB_RX = re.compile(
    r"\b("
    r"render|draw|show|paint|color|colour|fill|mark|highlight|label|outline|map|"
    r"depict|illustrate|display|simulate|trace|"
    r"rendre|dessiner|afficher|peindre|colorier|colorer|remplir|marquer|cartographier|"
    r"renderizar|dibujar|mostrar|pintar|colorear|rellenar|marcar|mapear|"
    r"desenhar|colorir|preencher|destacar"
    r")\b",
    re.IGNORECASE,
)


def _find_top_level_comma(text: str) -> int | None:
    depth = 0
    for i, c in enumerate(text):
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            return i
    return None


def _split_lead_from_first_item(first_item: str) -> tuple[str | None, str]:
    """Pull a lead-in off the first item so the bulleted list reads as a
    parallel structure. Lead carries the verb when one is found, so each
    bullet can drop into the same grammar."""
    m = _LEAD_VERB_RX.search(first_item)
    if m:
        lead = first_item[: m.end()].rstrip()
        rest = first_item[m.end():].lstrip(" ,;:")
        if rest:
            return lead, rest
    comma_pos = _find_top_level_comma(first_item)
    if comma_pos is not None and comma_pos > 0:
        lead = first_item[:comma_pos].rstrip()
        rest = first_item[comma_pos + 1:].lstrip()
        if rest:
            return lead, rest
    return None, first_item


def _bulletize_color_list(sentence: str) -> str | None:
    """Turn a comma-separated color list into a bulleted block.

    Returns None when the sentence has fewer than 2 hex codes - those keep
    their prose form so we don't bulletize single-color rules."""
    hex_matches = list(_HEX_PAREN_RX.finditer(sentence))
    if len(hex_matches) < 2:
        return None

    items: list[str] = []
    last = 0
    for m in hex_matches:
        chunk = sentence[last:m.end()]
        if items:
            chunk = _ITEM_SEP_RX.sub("", chunk, count=1)
        items.append(chunk.strip())
        last = m.end()
    trailing = sentence[last:].strip()

    lead, first_rest = _split_lead_from_first_item(items[0])
    items[0] = first_rest

    bullet_block = "\n".join(f"- {it}" for it in items)
    if lead:
        lead = lead.rstrip(",. ").strip()
        result = f"{lead}:\n\n{bullet_block}" if lead else bullet_block
    else:
        result = bullet_block

    if trailing:
        trailing = re.sub(r"^[.,;:\s]+", "", trailing)
        if trailing:
            result = f"{result}\n\n{trailing}"
    return result


def _format_text_block(block: str) -> list[str]:
    """Split a prose block into formatted paragraphs (each its own string).
    Sentences with 2+ hex codes become bullet lists; the rest stay as prose."""
    out: list[str] = []
    for raw in _SENTENCE_SPLIT_RX.split(block):
        sentence = raw.strip()
        if not sentence:
            continue
        bulleted = _bulletize_color_list(sentence)
        out.append(bulleted or sentence)
    return out


def format_template_prompt(prompt: str) -> str:
    """Lay out a template prompt for the textbox.

    Splits prose into one paragraph per sentence and turns any
    comma-separated color list into a bulleted block. Bullet groups that
    already ship in the source (server templates with explicit "\n- "
    lines) are preserved as-is so the source stays the source of truth."""
    if not prompt:
        return prompt
    text = prompt.strip()
    if not text:
        return text

    paragraphs: list[str] = []
    pending_text: list[str] = []
    pending_bullets: list[str] = []

    def flush_text() -> None:
        if pending_text:
            joined = " ".join(pending_text).strip()
            pending_text.clear()
            if joined:
                paragraphs.extend(_format_text_block(joined))

    def flush_bullets() -> None:
        if pending_bullets:
            paragraphs.append("\n".join(f"- {b}" for b in pending_bullets))
            pending_bullets.clear()

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_text()
            flush_bullets()
            continue
        if _BULLET_LINE_RX.match(line):
            flush_text()
            pending_bullets.append(_BULLET_LINE_RX.sub("", line, count=1).strip())
        else:
            flush_bullets()
            pending_text.append(line)
    flush_text()
    flush_bullets()

    return "\n\n".join(paragraphs)


_CATEGORY_LABELS = {
    "cartography": "Cartography",
    "landcover": "Land cover",
    "segment": "Segment",
    "climate": "Climate scenarios",
    "urban": "Urban scenarios",
    "energy": "Energy & solar",
    "cleanup": "Cleanup & enhance",
    "presentation": "Presentation renders",
    "forestry": "Forestry & vegetation",
    "agriculture": "Agriculture",
    "archaeology": "Archaeology & heritage",
    "geology": "Geology & mining",
    "hydrology": "Water & hydrology",
}

# High-level needs grouping the themed categories in the library sidebar.
# Mirrored with the website catalog (`needs` array + per-category `need`
# field); these local tables are the offline fallback, resolved through
# `get_need_groups` the same way category labels are.
_NEED_LABELS = {
    "classify": "Classify",
    "project": "Project",
    "render": "Render",
}

_NEED_TAGLINES = {
    "classify": "Analyze the territory: detect, segment, count, map",
    "project": "Simulate scenarios and possible futures",
    "render": "From raw imagery to presentation visuals",
}

_NEED_ORDER = ["classify", "project", "render"]

_CATEGORY_NEED = {
    "landcover": "classify",
    "segment": "classify",
    "forestry": "classify",
    "agriculture": "classify",
    "geology": "classify",
    "hydrology": "classify",
    "climate": "project",
    "urban": "project",
    "energy": "project",
    "cartography": "render",
    "cleanup": "render",
    "presentation": "render",
    "archaeology": "render",
}

# Grouped by need (classify -> project -> render) so the sidebar and search
# results walk the catalog in the same order the need groups display it.
_CATEGORY_ORDER = [
    "landcover",
    "segment",
    "forestry",
    "agriculture",
    "geology",
    "hydrology",
    "climate",
    "urban",
    "energy",
    "cartography",
    "cleanup",
    "presentation",
    "archaeology",
]


def _current_lang() -> str:
    """Return the 2-char language code matching the server label keys."""
    locale = QSettings().value("locale/userLocale", "en_US") or "en"
    short = locale[:2].lower()
    return short if short in ("en", "fr", "es", "pt") else "en"


def _pick_label(label_field: Any, fallback: str = "") -> str:
    """Return a string label from the server's polyglot `{en, fr, es, pt}`
    dict, the current locale first, else "en", else the fallback."""
    if isinstance(label_field, str):
        return label_field
    if isinstance(label_field, dict):
        lang = _current_lang()
        return label_field.get(lang) or label_field.get("en") or fallback
    return fallback


def _normalize_preset(preset: dict, source_category: str) -> dict:
    """Pull a server preset into the flat shape the dialog expects.

    `prompt` is a polyglot dict `{en, fr, es, pt}` on v3 server catalogs.
    Older string-only payloads still work via `_pick_label`'s str fallback.
    """
    return {
        "id": preset.get("id", ""),
        "label": _pick_label(preset.get("label"), preset.get("id", "")),
        "prompt": _pick_label(preset.get("prompt"), ""),
        "source_category": source_category,
        "top_pick": bool(preset.get("top_pick", False)),
        # Templates the server flags as fragile (model hallucinates often).
        # Plugin renders these under a separate "Experimental" disclosure
        # in each category page so the curated list stays trustworthy.
        "experimental": bool(preset.get("experimental", False)),
        "vector_color": preset.get("vector_color"),
        "demo_url_before": preset.get("demo_url_before"),
        "demo_url_after": preset.get("demo_url_after"),
    }


# Session-lifetime memo. `_cached_catalog` is called many times per
# result-render and per template lookup, and each call would otherwise
# parse the same JSON blob from QSettings. The memo is invalidated by
# `prompt_presets_client._write_cache` whenever a fresh server catalog
# lands on disk, so stale-after-refresh isn't a worry.
_CATALOG_MEMO: dict | None = None
_CATALOG_MEMO_LOADED = False


def _cached_catalog() -> dict | None:
    """Lazy + memoized read of the locally-cached server catalog."""
    global _CATALOG_MEMO, _CATALOG_MEMO_LOADED
    if _CATALOG_MEMO_LOADED:
        return _CATALOG_MEMO
    from .prompt_presets_client import read_cached_catalog_stale_ok

    _CATALOG_MEMO = read_cached_catalog_stale_ok()
    _CATALOG_MEMO_LOADED = True
    return _CATALOG_MEMO


def invalidate_catalog_memo() -> None:
    """Clear the session memo. Called from prompt_presets_client when a
    fresh server catalog is written so the next read sees it.
    """
    global _CATALOG_MEMO, _CATALOG_MEMO_LOADED
    _CATALOG_MEMO = None
    _CATALOG_MEMO_LOADED = False


def _iter_server_presets(catalog: dict | None):
    """Yield (category_key, raw_preset) pairs for every preset in `catalog`."""
    if not isinstance(catalog, dict):
        return
    for cat in catalog.get("categories", []) or []:
        if not isinstance(cat, dict):
            continue
        key = cat.get("key")
        if not isinstance(key, str):
            continue
        for p in cat.get("presets", []) or []:
            if isinstance(p, dict):
                yield key, p


def _iter_prompt_variants(prompt_field: Any):
    """Yield every language variant of a preset prompt. Accepts the v3
    polyglot `{en, fr, es, pt}` shape and the legacy plain-string shape."""
    if isinstance(prompt_field, str):
        if prompt_field:
            yield prompt_field
    elif isinstance(prompt_field, dict):
        for v in prompt_field.values():
            if isinstance(v, str) and v:
                yield v


def lookup_template_by_prompt(prompt_text: str) -> tuple[str, str] | None:
    """Return (template_id, label) when prompt_text equals a server preset
    after whitespace normalization. Matches across ALL language variants of
    the preset, so a French user running the French version of a template
    still gets tagged with the same canonical template_id as an English user.
    This is what makes per-template usage analytics language-agnostic."""
    norm = _normalize_for_match(prompt_text)
    if not norm:
        return None
    catalog = _cached_catalog()
    for _cat_key, p in _iter_server_presets(catalog):
        for variant in _iter_prompt_variants(p.get("prompt")):
            if _normalize_for_match(variant) == norm:
                label = _pick_label(p.get("label"), p.get("id", ""))
                return p.get("id", ""), label
    return None


def get_vector_hints(template_id: str) -> tuple[str | None, list[dict] | None]:
    """Return (vector_color, vector_classes) for a known template_id, or
    (None, None) if the template doesn't have either field set or the
    catalog cache is unavailable.

    Used at Generate-time to stash vectorize hints onto PipelineContext so
    the result panel can suggest "Extract red regions" with the swatch
    pre-filled. vector_classes (multi-class list) wins when both are set
    on the same preset; back-compat templates populate vector_color too.
    """
    if not template_id:
        return None, None
    catalog = _cached_catalog()
    for _cat_key, p in _iter_server_presets(catalog):
        if p.get("id") == template_id:
            classes = p.get("vector_classes")
            if not isinstance(classes, list) or not classes:
                classes = None
            color = p.get("vector_color")
            if not isinstance(color, str) or not color:
                color = None
            return color, classes
    return None, None


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
    if _FREEFORM_LULC_RX.search(text):
        return None
    if _FREEFORM_COLOR_OR_HEX_RX.search(text):
        return None
    if not _FREEFORM_DETECT_VERB_RX.search(text):
        return None
    return _FREEFORM_VECTOR_COLOR


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
    if _GUIDANCE_VECTOR_FILE_RX.search(text):
        return "vector_file"
    if _GUIDANCE_MEASURE_RX.search(text):
        return "measure"
    if _GUIDANCE_META_QA_RX.search(text):
        return "qa"
    return None


def _build_prompt_lookup(catalog: dict | None) -> dict[str, dict]:
    """Map raw prompt text -> {label, category} for re-attaching template
    metadata to Recent/Favorites entries the user saved from a template.

    Indexes every language variant of every polyglot prompt, so a Recent
    entry saved in any language re-attaches to its template on next read."""
    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        label = _pick_label(p.get("label"), p.get("id", ""))
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if not key:
                continue
            lookup[key] = {"label": label, "category": cat_key}
    return lookup


def _build_preset_lookup(catalog: dict | None) -> dict[str, dict]:
    """Map raw prompt text -> the full normalized preset (id + label + demo
    image URLs). Lets a saved favorite re-render as the template's before/after
    preview card instead of a bare text card. Indexes every language variant."""
    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        norm = _normalize_preset(p, cat_key)
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if key and key not in lookup:
                lookup[key] = norm
    return lookup


def get_preset_by_id(preset_id: str, server_catalog: dict | None = None) -> dict | None:
    """Return the normalized preset (id + label + prompt) for `preset_id`.

    Used to prime a prompt programmatically (the empty-canvas onboarding
    pre-fills a land-cover preset). When `server_catalog` is None, falls back
    to the locally-cached catalog. Returns None when the catalog is
    unavailable (first install offline) or the id is absent, so callers can
    skip the prompt fill and degrade gracefully."""
    if not preset_id:
        return None
    if server_catalog is None:
        server_catalog = _cached_catalog()
    for cat_key, p in _iter_server_presets(server_catalog):
        if p.get("id") == preset_id:
            return _normalize_preset(p, cat_key)
    return None


def _build_recent_presets(catalog: dict | None) -> list[dict]:
    """Recent prompts from prompt_history, with template metadata re-attached
    when the prompt matches a known server preset."""
    from . import prompt_history

    lookup = _build_prompt_lookup(catalog)
    out: list[dict] = []
    for i, entry in enumerate(prompt_history.get_recent()):
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            continue
        ts = entry.get("ts") or ""
        meta = lookup.get(prompt)
        if meta:
            out.append({
                "id": f"recent_{i}",
                "label": meta["label"],
                "prompt": prompt,
                "source_category": meta["category"],
                "from_recent": True,
                "ts": ts,
            })
        else:
            out.append({
                "id": f"recent_{i}",
                "label": prompt,
                "prompt": prompt,
                "source_category": None,
                "from_recent": True,
                "ts": ts,
            })
    return out


def _build_user_favorites_presets(catalog: dict | None) -> list[dict]:
    """User-managed Favorites from prompt_history. A favorite that matches a
    curated template re-renders as that template's before/after preview card
    (full preset: id + demo images); a freeform saved prompt with no template
    match stays a text card. Both carry from_favorites for the origin pill."""
    from . import prompt_history

    lookup = _build_preset_lookup(catalog)
    out: list[dict] = []
    for i, entry in enumerate(prompt_history.get_favorites()):
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            continue
        full = lookup.get(prompt)
        if full is not None:
            preset = dict(full)
            preset["id"] = full.get("id") or f"fav_{i}"
            preset["prompt"] = prompt
            preset["from_favorites"] = True
            out.append(preset)
            continue
        # No matching template: the user's own saved prompt -> text card. Keep
        # any stored label/category so the pill still reads right; otherwise
        # show the prompt itself as the card title.
        stored_label = entry.get("label")
        out.append({
            "id": f"fav_{i}",
            "label": tr(stored_label) if stored_label else prompt,
            "prompt": prompt,
            "source_category": entry.get("source_category"),
            "from_favorites": True,
        })
    return out


def _build_top_picks(catalog: dict | None) -> list[dict]:
    """Top Picks in server order. Each entry references a preset by id; we
    resolve those ids back to full presets so the dialog can render them."""
    if not isinstance(catalog, dict):
        return []
    tp_ids = catalog.get("top_picks")
    if not isinstance(tp_ids, list):
        return []
    by_id: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        pid = p.get("id")
        if isinstance(pid, str) and pid:
            by_id[pid] = _normalize_preset(p, cat_key)
    out: list[dict] = []
    for tid in tp_ids:
        if isinstance(tid, str) and tid in by_id:
            out.append(by_id[tid])
    return out


def _find_server_category(catalog: dict | None, cat_key: str) -> dict | None:
    """Return the raw server category dict for `cat_key`, or None."""
    if not isinstance(catalog, dict):
        return None
    for cat in catalog.get("categories", []) or []:
        if isinstance(cat, dict) and cat.get("key") == cat_key:
            return cat
    return None


def _themed_category_label(cat_key: str, catalog: dict | None) -> str:
    """Resolve the user-facing category label.

    Prefers the server's polyglot label when available (single source of truth,
    so new categories don't need a plugin .ts update), and falls back to the
    plugin's local tr() table when offline or for the very first session."""
    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        resolved = _pick_label(cat.get("label"), "")
        if resolved:
            return resolved
    return tr(_CATEGORY_LABELS[cat_key])


def _build_themed_category(cat_key: str, catalog: dict | None) -> list[dict]:
    """All presets in `cat_key` from the server catalog (empty if unavailable)."""
    cat = _find_server_category(catalog, cat_key)
    if cat is None:
        return []
    return [
        _normalize_preset(p, cat_key)
        for p in (cat.get("presets") or [])
        if isinstance(p, dict)
    ]


def _category_need(cat_key: str, catalog: dict | None) -> str:
    """Need key for a category: the server's assignment first (so future
    categories land in the right group without a plugin update), else the
    local fallback table."""
    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        need = cat.get("need")
        if isinstance(need, str) and need in _NEED_LABELS:
            return need
    return _CATEGORY_NEED.get(cat_key, _NEED_ORDER[0])


def get_need_groups(server_catalog: dict | None = None) -> list[dict]:
    """Ordered need groups for the library sidebar.

    Each group is ``{key, label, tagline, categories: [cat_key, ...]}``.
    Labels and taglines prefer the server catalog's polyglot ``needs``
    entries and fall back to the local tables, mirroring how category
    labels resolve."""
    if server_catalog is None:
        server_catalog = _cached_catalog()

    server_needs: dict[str, dict] = {}
    if isinstance(server_catalog, dict):
        for entry in server_catalog.get("needs", []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                server_needs[entry["key"]] = entry

    groups: list[dict] = []
    for need_key in _NEED_ORDER:
        srv = server_needs.get(need_key) or {}
        groups.append({
            "key": need_key,
            "label": _pick_label(srv.get("label"), "") or tr(_NEED_LABELS[need_key]),
            "tagline": (
                _pick_label(srv.get("tagline"), "") or tr(_NEED_TAGLINES[need_key])
            ),
            "categories": [
                c for c in _CATEGORY_ORDER
                if _category_need(c, server_catalog) == need_key
            ],
        })
    return groups


def get_all_categories(server_catalog: dict | None = None) -> list[dict]:
    """Return all categories with translated labels.

    `server_catalog`: optional v2 catalog dict. When None, falls back to the
    locally-cached server catalog. With neither, themed categories render
    as empty shells (first install offline / pre-activation)."""
    if server_catalog is None:
        server_catalog = _cached_catalog()

    result: list[dict] = []

    result.append({
        "key": "recent",
        "label": tr("Recent"),
        "presets": _build_recent_presets(server_catalog),
    })

    result.append({
        "key": "user_favorites",
        "label": tr("Favorites"),
        "presets": _build_user_favorites_presets(server_catalog),
    })

    result.append({
        "key": "favorites",
        "label": tr("Top Picks"),
        "presets": _build_top_picks(server_catalog),
    })

    for cat_key in _CATEGORY_ORDER:
        result.append({
            "key": cat_key,
            "label": _themed_category_label(cat_key, server_catalog),
            "presets": _build_themed_category(cat_key, server_catalog),
        })

    return result
