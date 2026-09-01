# Driving AI Edit from an AI agent

This plugin is built to be driven by whatever AI assistant you already use with
QGIS. If you have installed a QGIS MCP server, it can run AI Edit for you. You
do not need anything from us, and we do not ship an MCP server of our own: we
plug into yours.

There are two ways in. Use the first one if you can.

## 1. Processing algorithms

The plugin registers a Processing provider with the id `terraedit`. Any tool
that runs QGIS Processing sees it, including the Processing Toolbox, the
Graphical Modeler, batch mode, `processing.run()`, and every MCP server with a
`run_processing` or `execute_processing` tool.

Run this one first. It is instant and it costs nothing.

    processing.run("terraedit:editstatus", {})

It answers whether AI Edit can work right now, and if not, what the user has to
do. It also names the algorithms that do the work, so an agent that can run an
algorithm but cannot list one still finds its way.

### terraedit:editstatus

Check the AI Edit status, credits and plan before you generate anything.

| | |
|---|---|
| Inputs | none |
| Outputs | `INSTALLED`, `READY`, `STATE`, `ACTION_REQUIRED`, `PLAN`, `CREDITS_REMAINING`, `BUSY`, `NEXT_ALGORITHMS` |

`STATE` is `READY`, `NEEDS_ACTIVATION` or `NO_PANEL`. When it is not `READY`,
read `ACTION_REQUIRED` out to the user and stop: signing in is their action, and
no algorithm here can do it for them.

### terraedit:generate

Redraw a map area from a prompt. **This is the call that spends credits.**

| | |
|---|---|
| Inputs | `EXTENT` map area, `PROMPT` text, `RESOLUTION` optional output size, `TEMPLATE` optional preset id |
| Outputs | `SUBMITTED`, `STATE`, `RESULT_LAYERS`, `STATUS` |

A generation takes 30 to 120 seconds, longer at the larger output sizes. **Your
bridge will very likely time out before it finishes.** That does not stop
anything: the run keeps going inside QGIS, the result still arrives as a layer,
and the credits have already been spent.

So when a call times out, or when you are unsure whether a run is still going,
run `terraedit:editstatus` and read `BUSY`. **Never submit a second time.** A
second submission is a second charge for a result the user is already getting.

Only one generation runs at a time. Submitting while one is going returns
`SUBMITTED` false and starts nothing.

### terraedit:vectorize

Trace one flat colour of a result image into editable polygons.

| | |
|---|---|
| Inputs | `SOURCE` raster layer, `TARGET_COLOR`, `TOLERANCE` optional, `CLASS_LABEL` optional, `OUTPUT` vector destination |
| Outputs | `OUTPUT`, `FEATURE_COUNT`, `STATUS` |

This one runs on your own machine with GDAL. Nothing is sent anywhere, no
account is needed, and no credit is spent, so it works signed out and offline.

It pays best in two steps: first a generation whose prompt asks for the target
painted in one solid colour that appears nowhere else, then this algorithm on
that colour.

Every output is a plain string or number, so it survives a bridge that turns
results into text before sending them back.

## 2. import terralab

If your MCP server only offers arbitrary code execution, the plugin publishes a
module you can import by name from the running QGIS Python:

    import terralab
    print(terralab.capabilities())

`capabilities()` answers what TerraLab plugins are installed, whether each is
ready, and how to call it. `terralab.help()` says the same thing in prose.

    import terralab
    print(terralab.edit.get_status())
    print(terralab.edit.guide())
    print(terralab.edit.generate(prompt="remove the clouds"))
    print(terralab.edit.generation_status())

The same handle is available the long way round, if you prefer it:

    import qgis.utils
    api = qgis.utils.plugins["AI_Edit"].mcp_api

Three habits save a wasted round trip with most code-execution tools.

Wrap every result in `print()`, because they return captured stdout and nothing
else. Make each snippet self-contained, because most of them start from a fresh
namespace on every call. And if you touch `qgis.utils`, import it first: several
servers pre-bind the name `qgis` to the `Qgis` class rather than the module, so
`qgis.utils.plugins` fails until you write `import qgis.utils`.

## What you can call

This route is wider than the Processing one: everything a person can do in the
panel is here. Two calls describe the rest, so nothing has to be guessed.

    print(api.capabilities())   # api version, method list, and the workflow shape
    print(api.guide())          # prose advice on getting good results

`capabilities()["workflow"]` says what to call first, the usual order, which
calls cost money, which are slow and must be polled, and which need a zone.

| Method | What it does |
|---|---|
| `get_status()` | Is the plugin ready, is a run in flight, what plan is the user on |
| `capabilities()` | The API version, the method list, and the workflow shape |
| `guide()` | Plain text on how to get good results, not merely valid ones |
| `get_account()` | Plan, usage and what this account may ask for. Read only |
| `get_credits()` | What is left this month |
| `set_zone(bbox=None, polygon_wkt=None, crs=None)` | Choose the ground, as a rectangle or a free shape |
| `get_zone()` / `clear_zone()` | Read or drop the zone |
| `get_presets()` / `get_preset(id)` | The ready-made prompts, by category |
| `search_presets(query)` / `get_top_picks()` | Find a preset, or read the shortlist |
| `get_preset_families()` / `get_preset_family(key)` | Browse the catalogue by family |
| `get_prompt_guidance()` | The limits a prompt has to meet, and how to write one |
| `list_favorite_prompts()` / `add_favorite_prompt()` / `remove_favorite_prompt()` | The starred prompts |
| `list_recent_prompts()` | The prompts sent from this machine |
| `attach_reference(layer_name=None, path=None, note="")` | Send a picture with the prompt. `layer_name` also takes a well-known basemap name (`Satellite`, `Streets`, `Terrain`, `Hillshade`, `OpenStreetMap`), added to the project hidden when it is not there and named back under `added_layer` |
| `list_references()` / `set_reference_note()` / `remove_reference()` / `clear_references()` | Manage those pictures |
| `markup(action, geometry_wkt=None, color=None, shape=None)` | Draw on the zone to show the AI where to act |
| `markup_status()` | How many marks are on the zone |
| `get_resolutions()` / `set_resolution(label)` | The output sizes this account may ask for |
| `generate(prompt, bbox=None, ..., polygon_wkt=None)` | Start a generation |
| `generation_status()` | Poll a run and collect its result layers |
| `cancel()` | Stop the run in flight |
| `list_versions()` / `select_version(index)` | The results so far, and which one to build on |
| `compare(on=True)` | The before and after slider on the map |
| `finish_session(index=None)` | End the session. Results are already layers |
| `vectorize(target_rgb, ...)` | Trace one colour of a result into polygons |
| `list_sessions()` / `get_session(id)` / `list_generations()` / `get_generation(id)` | Past work |
| `open_session(session_id or request_id)` | Reopen past work and carry on editing it |
| `add_generation_to_map(request_id)` | Bring a past result back as a layer |
| `rename_session(id, title)` / `delete_session(id)` | Name or remove one past session |

Every method returns a plain dictionary and never raises. A failure comes back
under the key `_error`. The API carries a version number and a method list, so
you can ask what a build supports instead of guessing.

Nothing here signs a user in, writes an activation key, or accepts terms. Those
are the user's own actions. `get_status()` reports what they have to do by hand,
and that is where this API stops.

`delete_session()` is permanent and takes one session id. There is no method
here that empties a whole history.

## How a generation runs

`generate()` returns as soon as the work is submitted. It does not block. Poll
`generation_status()` until `running` is false, then read `result_layers`.

Only one generation runs at a time. If one is already going, `generate()`
answers `{"busy": True}` and starts nothing. Do not submit again: poll instead.
Leaving `bbox` empty keeps the zone already selected, which is how you iterate
on a result without losing its history.

## What it costs

Each generation is billed, and the free plan includes a set number every month.
`get_credits()` reports what is left before you spend anything. Free accounts
are limited to the smallest output size. Everything else listed above is free to
call, including `vectorize()`, which runs on your own machine.

Pro raises the limits: https://terra-lab.ai/pricing

## If two servers fight over a port

Most QGIS MCP plugins listen on `127.0.0.1:9876`, so two of them enabled in the
same QGIS profile will collide. If your MCP server cannot reach QGIS, check
whether another MCP plugin is already holding the port, and move one of them.

## Questions

https://github.com/TerraLabAI/QGIS_AI-Edit/issues
