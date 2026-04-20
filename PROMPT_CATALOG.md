# AI Edit — Prompt Catalog

Curated prompts for the AI Edit QGIS plugin, organized by category and profession.
Each prompt is designed for real GIS workflows on satellite or drone imagery.

---

## Clean

Remove unwanted elements to reveal what's beneath.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 1 | `Remove clouds and atmospheric haze, reveal clear terrain beneath` | Recover usable imagery from cloudy satellite captures without reordering | Remote sensing analyst |
| 2 | `Remove all shadows to reveal ground features and surface details` | Eliminate shadow occlusion from low sun-angle captures for accurate feature digitizing | Surveyor / GIS technician |
| 3 | `Remove trees and vegetation, reveal bare ground and topography` | Expose terrain under canopy for grading analysis, earthwork estimation, or archaeological prospection | Civil engineer / Archaeologist |
| 4 | `Remove existing buildings and structures, show cleared empty land` | Visualize a pre-construction or post-demolition site state for a development proposal | Real estate developer / Architect |

---

## Add

Add features and infrastructure to the scene.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 5 | `Add rows of mature trees along both sides of the main road` | Visualize a street tree planting project for a municipal grant application | Landscape architect |
| 6 | `Add new residential buildings to the empty area` | Show a proposed housing development to stakeholders at a public hearing | Urban planner |
| 7 | `Add solar panel arrays on all visible rooftops` | Illustrate a district-wide rooftop solar program for a feasibility report | Energy consultant |
| 8 | `Add a paved road with sidewalks connecting the two neighborhoods` | Present a new road infrastructure project to a municipal council | Transportation engineer |
| 9 | `Transform the vacant lot into a public park with walking paths, trees, and open lawn` | Produce a before/after visual for a community consultation on a new park | Landscape architect / Urban planner |

---

## Enhance

Improve image quality and clarity.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 10 | `Enhance this low-resolution satellite image into a sharp, detailed aerial photograph` | Turn a low-quality Google Earth screenshot into a presentation-grade site photo when no drone flight is available | Architect / Real estate developer |

---

## Transform

Change the entire visual style of the imagery.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 11 | `Transform into a clean architectural site plan with building footprints outlined and surrounding context simplified` | Generate a site plan base layer from aerial imagery for a design presentation board | Architect |
| 12 | `Turn the entire image into a black-and-white ink drawing with fine line work` | Produce an artistic base map for an architecture competition entry or publication layout | Architect / Urban designer |
| 13 | `Transform into a nighttime aerial view with streetlights glowing and building windows illuminated` | Visualize a public lighting masterplan showing how a neighborhood would look at night | Lighting designer / Urban planner |

---

## Visualize

Simplified, color-coded, or analytical views following standard cartographic conventions.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 14 | `Transform into a simplified flat-color land use map: yellow for residential, red for commercial, purple for industrial, dark green for forest, light green for parks, brown for agriculture, blue for water, gray for roads` | Produce a quick land use overview for a planning report without running a full classification pipeline | GIS analyst / Urban planner |
| 15 | `Create a figure-ground diagram: all buildings in solid dark, everything else in white` | Analyze urban fabric density and building morphology following the Nolli map convention | Urban designer / Architect |
| 16 | `Create a top-down technical blueprint view of the main building` | Generate a schematic footprint diagram from aerial imagery for a preliminary feasibility study | Architect |
| 17 | `Simplify into a minimal cartographic style with clean outlines, muted tones, and no photographic texture` | Produce a clean neutral base map suitable for overlaying vector GIS data layers | Cartographer / GIS analyst |
| 18 | `Transform into a beautiful stylized isometric map with soft colors, clean shapes, and elegant typography — like a high-end urban illustration` | Produce a striking hero visual for a project presentation, website banner, or competition board | Architect / Urban designer / Communications |

---

## Outline

Delineate and highlight specific features for quick visual inventory or digitizing reference.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 19 | `Outline all buildings with bright red borders on the original image` | Quick building inventory or base layer for manual digitizing in QGIS | GIS technician / Urban planner |
| 20 | `Outline all water bodies — lakes, rivers, and ponds — with blue borders on the original image` | Delineate hydrographic features for a watershed study or flood mapping | Hydrologist |
| 21 | `Outline all tree canopy areas with green borders on the original image` | Estimate canopy coverage for an urban tree inventory or environmental assessment | Arborist / Environmental planner |
| 22 | `Outline all roads and paved surfaces with yellow borders on the original image` | Map impervious surfaces for a stormwater runoff analysis | Stormwater engineer |
| 23 | `Outline all agricultural parcels with distinct colored borders on the original image` | Delineate field boundaries for a crop monitoring or land registry project | Agronomist / Land surveyor |

---

## Simulate

Hypothetical scenarios, future visions, and what-if visualizations.

| # | Prompt | Use case | Profession |
|---|--------|----------|------------|
| 24 | `Show this coastal area partially flooded with realistic rising sea water` | Communicate sea-level-rise impact to elected officials and coastal residents | Climate scientist / Coastal planner |
| 25 | `Replace the parking lot with a dense urban forest of mature trees` | Visualize a depaving and green infrastructure proposal for urban heat island reduction | Environmental planner |
| 26 | `Transform the empty field into a completed development with a modern building` | Show investors or a planning committee what a finished project would look like from above | Real estate developer |
| 27 | `Transform the industrial zone into a mixed-use neighborhood with housing, shops, and green spaces` | Illustrate a long-term urban renewal vision for a master plan document | Urban planner |
| 28 | `Show this agricultural area converted into a solar farm with rows of photovoltaic panels` | Produce a visual for a renewable energy permitting application or environmental impact study | Energy consultant |

---

## Prompting Tips

Best practices for writing effective prompts with the AI Edit plugin:

1. **Be specific about what changes** — "Add mature oak trees along the north side of the road" works better than "add some trees"
2. **Describe the end result, not the process** — "Show a completed park with paths and a pond" rather than "first add grass, then add paths"
3. **Reference visible features** — Use landmarks in the image: "near the river", "on the large rooftop", "in the empty lot on the left"
4. **One concept per prompt** — Complex multi-step edits work best as sequential single prompts rather than one long instruction
5. **Specify style when transforming** — "ink drawing", "flat-color map", "blueprint", "site plan" give distinct and predictable results
6. **Use spatial language** — "top-down view", "along the main road", "in the center of the image" help the model understand what to target
