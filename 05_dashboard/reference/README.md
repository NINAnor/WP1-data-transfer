# Reference trait data

Slimmed copies of two published datasets, used to describe each species'
ecology on the dashboard. Please cite them when reusing the dashboard output.

## storchova_horak_2018.csv
Columns: scientific name, migration strategy flags, breeding-habitat flags and
breeding-season diet flags (`_B`).

Storchová L, Hořák D (2018) Life-history characteristics of European birds.
*Global Ecology and Biogeography* 27: 400–406. https://doi.org/10.1111/geb.12709
Data: https://doi.org/10.5061/dryad.n6k3n (CC0). Copied via the `traitdata`
R package (RS-eco/traitdata, `eubirds`).

## avonet_birdlife.csv
Species averages (BirdLife taxonomy): habitat, habitat density, migration
(1 sedentary, 2 partial, 3 migratory), trophic level/niche, primary lifestyle, mass.

Tobias JA et al. (2022) AVONET: morphological, ecological and geographical data
for all birds. *Ecology Letters* 25: 581–597. https://doi.org/10.1111/ele.13898
Data: https://doi.org/10.6084/m9.figshare.16586228 (CC BY 4.0).

## IUCN status
Fetched at build time from Wikidata (property P141, IUCN conservation status),
which mirrors the global IUCN Red List category.
