# AI Edit for QGIS [![QGIS](https://img.shields.io/badge/QGIS-3.22+-93b023?style=flat-square&logo=qgis&logoColor=white)](https://qgis.org) [![Windows](https://img.shields.io/badge/Windows-0078D6?style=flat-square&logo=windows&logoColor=white)]() [![macOS](https://img.shields.io/badge/macOS-000000?style=flat-square&logo=apple&logoColor=white)]() [![Linux](https://img.shields.io/badge/Linux-FCC624?style=flat-square&logo=linux&logoColor=black)]()

Show a project before it exists, on the aerial imagery you already have open in QGIS.

Select an area, write what you want, and get the result back as a georeferenced layer on the same extent and CRS as your source imagery, tagged as AI-generated. Documentation and tutorials: https://terra-lab.ai/ai-edit

<table>
  <tr>
    <td align="center"><img src="https://terra-lab.ai/images/ai-edit/before-site-plan.webp" width="380" alt="Orthophoto of a block, before"><br><sub>Orthophoto</sub></td>
    <td align="center"><img src="https://terra-lab.ai/images/ai-edit/after-site-plan.webp" width="380" alt="The same block redrawn as a clean site plan"><br><sub>"Redraw as a clean site plan"</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="https://terra-lab.ai/images/ai-edit/before-greening-streets.webp" width="380" alt="A street, before"><br><sub>Street today</sub></td>
    <td align="center"><img src="https://terra-lab.ai/images/ai-edit/after-greening-streets.webp" width="380" alt="The same street planted with trees"><br><sub>"Plant the street with trees"</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="https://terra-lab.ai/images/ai-edit/before-flooding.webp" width="380" alt="A riverside area, before"><br><sub>Riverside area</sub></td>
    <td align="center"><img src="https://terra-lab.ai/images/ai-edit/after-flooding.webp" width="380" alt="The same area under a flood scenario"><br><sub>"Show a flood scenario"</sub></td>
  </tr>
</table>

## What it does

- **Works on the layer you already have**: GeoTIFF, JPG, PNG, and online basemaps (WMS, XYZ tiles). Vector layers and other rasters can guide the result as references.
- **The most used case is the site plan**: an orthophoto redrawn as a clean illustrative plan for a client file or a presentation. Then redevelopments, planted streets, densified blocks, parks, solar farms, flood and sea level scenarios, enhanced or upscaled aerials, clouds and cars removed, land cover drawn as flat colours.
- **Guide the result**: add reference images or layers, sketch directly on the map, iterate on a result, and compare before and after with a slider.
- **Vectorize**: a generated map with flat colours becomes editable polygons (one class per colour), exported to GeoPackage, Shapefile or GeoJSON.
- **100+ ready-made prompts**, ranked by what people actually run, or write your own.
- Output up to 4K, aligned on the source imagery, with the AI-generated tag kept in the file.

## What it is not

AI Edit previews. It does not measure, classify or certify.

- **Not a document for a permit.** The output has no dimensions and no legal value. A site plan for a planning application (plan de masse, block plan, plot plan) still needs to be drawn and dimensioned.
- **Not a detector.** For building, tree or parcel outlines with real geometry, use [AI Segmentation](https://github.com/TerraLabAI/QGIS_AI-Segmentation), which is built for that.
- **Not a prediction.** A flood or sea level scenario is an illustration, not a hydraulic model. Keep the AI-generated tag when you share a result.

## Install

QGIS 3.22 or later, on Windows, macOS or Linux. In QGIS, open *Plugins > Manage and Install Plugins*, search "AI Edit", install. No GPU needed: the model runs on TerraLab servers, not on your machine. A free tier needs no card. Plans and limits: https://terra-lab.ai/ai-edit

## Data & privacy

To generate an edit, the area you select and your prompt are sent to our cloud
for AI processing. The plugin also sends usage statistics linked to your
account (which features you use, errors, versions; never your imagery, layers
or coordinates). You can turn this off in *Account Settings*, and delete your
account from there too. A notice says all this the first time you open the
plugin, before anything is sent. See our
[Privacy Policy](https://terra-lab.ai/privacy-policy).

## Read more

- [Turn a satellite image into a site plan](https://terra-lab.ai/blog/satellite-image-to-site-plan)
- [Flood simulation in QGIS](https://terra-lab.ai/blog/flood-simulation-qgis)
- [Redesign a street on the orthophoto](https://terra-lab.ai/blog/street-redesign-qgis)
- Plugin page on the QGIS repository: https://plugins.qgis.org/plugins/AI_Edit/
- Bugs and requests: https://github.com/TerraLabAI/QGIS_AI-Edit/issues

License: GPLv2.
