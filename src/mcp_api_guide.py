"""What an agent driving AI Edit needs to know, in two forms.

``WORKFLOW`` is the machine-readable shape: call order, what costs money, what
is slow, what needs a zone. ``GUIDE`` is the same ground in prose, aimed at
getting good results rather than merely valid ones.

Both are read by ``mcp_api``: ``capabilities()`` returns the first, ``guide()``
returns the second. Keep them in step with each other.
"""
from __future__ import annotations

# Groups a caller can reason about without reading every method name.
WORKFLOW = {
    "start_here": "get_status",
    "typical_order": [
        "get_status",
        "get_credits",
        "set_zone",
        "get_presets",
        "attach_reference",
        "markup",
        "set_resolution",
        "generate",
        "generation_status",
        "select_version",
        "vectorize",
        "finish_session",
    ],
    "costs_credits": ["generate"],
    "slow_poll_required": {
        "generate": "generation_status",
    },
    "needs_a_zone": [
        "generate",
        "markup",
        "attach_reference",
    ],
    "makes_a_network_call": [
        "get_credits",
        "get_account",
        "rename_session",
        "delete_session",
        "list_generations (only with refresh=True)",
        "add_favorite_prompt (mirrors the star, best effort)",
        "remove_favorite_prompt (mirrors the star, best effort)",
    ],
    "permanent": ["delete_session"],
    "free_and_local": ["vectorize"],
    "read_only": [
        "capabilities",
        "guide",
        "get_status",
        "get_account",
        "get_credits",
        "get_presets",
        "get_preset",
        "get_preset_families",
        "get_preset_family",
        "get_prompt_guidance",
        "get_resolutions",
        "get_top_picks",
        "get_zone",
        "get_session",
        "get_generation",
        "generation_status",
        "list_favorite_prompts",
        "list_generations",
        "list_recent_prompts",
        "list_references",
        "list_sessions",
        "list_versions",
        "markup_status",
        "search_presets",
    ],
    "never_available_here": (
        "Signing in, entering an activation key and accepting terms are the "
        "user's own actions and have no method here. When get_status() reports "
        "the plugin is not ready, hand its action_required line to the user and "
        "wait."
    ),
    "one_at_a_time": (
        "AI Edit holds one zone and one run. A second generate() while one is "
        "running is refused, not queued. Never resubmit a run that looks slow: "
        "the first one is still going and a second one charges the user again."
    ),
}

GUIDE = """\
AI Edit, for an agent driving it

WHAT IT IS
AI Edit redraws a piece of the map. You give it an area and an instruction in
plain words, and it returns a new image, georeferenced and placed in the QGIS
project. Use it to change what imagery shows: clear the clouds, move the
season, remove parked cars, paint a proposed building in, colour a land cover
class flat so it can be traced. Do not use it to measure or extract geometry
from imagery as it stands.

FIRST FIVE MINUTES
1. get_status(). It answers offline and costs nothing. If it is not ready, read
   action_required out to the user and stop. You cannot sign anyone in.
2. get_credits() to see what is left. Every generate() spends some.
3. set_zone() to choose the ground.
4. generate() with the prompt.
5. generation_status() every few seconds until running is False.

THE ZONE
The zone is what gets edited, and everything else follows from it. Zoom the
canvas to what you want first, because the picture sent is what is visible at
that ground, at the detail the current view gives.

Keep the zone tight. One building, one field, one junction gives a far better
result than a whole town, because the same amount of detail is spread over less
ground. If the user wants a wide area changed, do several small zones rather
than one big one.

Shape matters. The result comes back in the shape of the zone, so a square or a
wide landscape zone fills the frame, while a tall narrow one comes back with
bands down the sides. Prefer square unless you have a reason.

set_zone(polygon_wkt=...) takes a free shape. The rectangle round your shape is
what the model is shown, and the result is then cut back to your shape, so the
map outside it stays exactly as it was. Reach for it when the thing you are
editing is not a rectangle and you do not want the corners touched.

WRITING THE PROMPT
Say what to change and what it should become. "Replace the parking lot with
grass and trees" works. "Improve this" does not.

One instruction at a time. Two unrelated changes in one prompt usually gets you
one of them done well and the other ignored. Run them as two edits, the second
built on the first.

Name what you can see. Colours, materials, seasons, times of day, whether
something is there or gone. Avoid words that need judgement, like "nicer" or
"realistic".

Do not describe the map, describe the change. The model already has the
picture.

Check get_prompt_guidance() for the minimum a prompt has to meet. A prompt
under it is refused before anything is spent, which is free but wastes a round
trip.

The library is worth reading before you write anything. get_top_picks() and
search_presets() return prompts already written and tested for common jobs, and
each one carries a template id you pass to generate(). Start from one of those
and adjust it rather than writing from scratch.

WHEN A PICTURE BEATS MORE WORDS
Some things cannot be written down. A particular roof colour, a house style, a
legend, a rendering you are trying to match. attach_reference() sends a picture
along with the prompt, either a layer already in the project or an image file
on disk, and set the note on it to say what to take from it: "match these roof
colours", not "reference image".

Three rules that decide whether it works. Set the zone first, because a
reference is cropped to the same ground. Keep a reference layer hidden on the
map, because anything visible at the zone is already in the picture being
edited and sending it twice confuses the result. And never use a basemap or
another tile service as a reference, because those load a piece at a time and
come out blank.

References stay attached when you leave a session and come back, so call
clear_references() between two unrelated jobs.

WHEN DRAWING BEATS MORE WORDS
When the hard part is saying WHERE, draw it. markup(action="draw") puts a line,
an arrow or a ring on the map, and the model reads it as a pointer and leaves
no trace of it in the result. "Repaint the roof I circled" is shorter and more
reliable than three sentences of directions, and it is the only way to point at
one thing among several identical ones.

Marks live on the zone. One outside it is refused. markup(action="clear")
starts over, markup(action="undo") takes back the last one. You do not have to
call "done": the marks are picked up by the next generate() either way.

ITERATING
The result is a starting point more often than an answer. list_versions()
shows the lineage, index 0 being the original. select_version() picks the one
the next edit builds on, so you can go back to an earlier one and take another
route. Calling generate() again with the zone left alone continues the same
piece of work rather than starting a new one.

Two edits with the same prompt make two layers with the same name, and QGIS
adds a number to the second. Tell them apart by their version index rather than
by name.

HOW LONG IT TAKES
Long enough that any calling bridge with a short timeout will give up first.
The smallest output size usually comes back inside a minute. The largest takes a
few minutes. A timeout on your side does not stop the run: it is still going in
QGIS and it has already been charged.

So never resubmit. Poll generation_status() and wait. A second submission is a
second charge for a result the user is already getting.

Bigger is not better by default. The largest size costs more credits and takes
longer, and it only pays when the user actually needs the detail, for printing
or for tracing fine features. get_resolutions() lists what this account may
pick and what each one costs. Free accounts get the smallest size only.

TURNING A RESULT INTO DATA
vectorize() traces one flat colour of a result into editable polygons. It runs
on this machine, needs no account and no connection, and spends no credit.

It works when the thing you want is one flat colour, which is why the pattern
that pays is two steps: first an edit whose prompt asks for the target painted
in one solid colour, then vectorize() on that colour. Ask for a colour that
appears nowhere else in the picture.

If the user wants existing objects outlined from imagery as it stands, this is
the wrong plugin. AI Segmentation does that directly.

FINISHING
Nothing has to be saved. Every result is a layer in the QGIS project from the
moment it arrives, and the whole session is in the history. finish_session()
only ends the session and drops the zone. open_session() brings any of it back
later, on any machine the user signs in on.

WHAT YOU MUST NOT DO
Do not sign anyone in, write an activation key, or accept terms on the user's
behalf. There is no method for it here and that is deliberate.
Do not delete anything the user did not ask you to delete. delete_session()
cannot be undone.
Do not spend credits exploring. Read get_presets(), get_status() and
get_credits() as much as you like, they are free.
"""
