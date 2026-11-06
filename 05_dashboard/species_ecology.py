"""
Ecological context for each species, written to species_ecology.parquet:

- Global IUCN Red List category (Wikidata P141).
- Migration strategy, breeding habitats and diet (Storchová & Hořák 2018 for
  European breeders, AVONET for everything else; see reference/README.md).
- Patterns observed in TABMON itself: the hour of peak vocal activity, the
  share of activity at night, and the months the species is active per country.
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("tabmon.species_ecology")

REFERENCE = Path(__file__).parent / "reference"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "TABMON-dashboard/0.1 (https://www.nina.no/english/TABMON; benjamin.cretois@nina.no)"

IUCN_CODES = {
    "least concern": "LC",
    "near threatened": "NT",
    "vulnerable": "VU",
    "endangered": "EN",
    "endangered status": "EN",
    "critically endangered": "CR",
    "extinct in the wild": "EW",
    "extinct species": "EX",
    "data deficient": "DD",
}

HABITATS = {
    "Deciduous.forest": "deciduous forest",
    "Coniferous.forest": "coniferous forest",
    "Woodland": "woodland",
    "Shrub": "scrub",
    "Savanna": "savanna",
    "Tundra": "tundra",
    "Grassland": "grassland",
    "Mountain.meadows": "mountain meadows",
    "Reed": "reedbeds",
    "Swamps": "swamps",
    "Desert": "desert",
    "Freshwater": "freshwater",
    "Marine": "sea",
    "Rocks": "rocky areas",
    "Human.settlements": "towns & villages",
}
DIETS = {
    "Arthropods_B": "insects & spiders",
    "Other.invertebrates_B": "other invertebrates",
    "Granivore_B": "seeds",
    "Frugivore_B": "fruit",
    "Folivore_B": "leaves & shoots",
    "Fish_B": "fish",
    "Other.vertebrates_B": "small vertebrates",
    "Carrion_B": "carrion",
    "Omnivore_B": "omnivorous",
}
AVONET_MIGRATION = {1: "resident", 2: "partial", 3: "migrant"}
NIGHT_HOURS = (23, 0, 1, 2, 3)


def fetch_iucn(species: list[str], batch: int = 150) -> dict[str, str]:
    """Scientific name -> IUCN category code, via Wikidata."""
    out: dict[str, str] = {}
    for i in range(0, len(species), batch):
        names = " ".join(json.dumps(s) for s in species[i : i + batch])
        query = f"""
            SELECT ?name ?statusLabel WHERE {{
              VALUES ?name {{ {names} }}
              ?item wdt:P225 ?name; wdt:P141 ?status.
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }}"""
        url = f"{WIKIDATA_SPARQL}?{urllib.parse.urlencode({'query': query, 'format': 'json'})}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    rows = json.load(resp)["results"]["bindings"]
                break
            except Exception as exc:  # noqa: BLE001 - retry any Wikidata request failure
                log.warning("Wikidata query failed (%s), retrying", exc)
                time.sleep(2 ** (attempt + 1))
        else:
            continue
        for r in rows:
            code = IUCN_CODES.get(r["statusLabel"]["value"].lower())
            if code:
                out[r["name"]["value"]] = code
    return out


def migration_from_storchova(row: dict) -> str | None:
    if row.get("Long.distance.migrant") == "1":
        return "long_distance"
    if row.get("Short.distance.migrant") == "1":
        return "short_distance"
    if row.get("Facultative.migrant") == "1":
        return "partial"
    if row.get("Sedentary") == "1":
        return "resident"
    return None


def traits_for(species: str, storchova: dict, avonet: dict) -> dict:
    s = storchova.get(species)
    a = avonet.get(species)
    if s:
        return {
            "migration": migration_from_storchova(s),
            "habitats": [label for col, label in HABITATS.items() if s.get(col) == "1"],
            "diet": [label for col, label in DIETS.items() if s.get(col) == "1"],
            "lifestyle": a.get("Primary.Lifestyle") if a else None,
            "trait_source": "Storchová & Hořák 2018",
        }
    if a:
        mig = a.get("Migration")
        return {
            "migration": AVONET_MIGRATION.get(int(float(mig))) if mig else None,
            "habitats": [a["Habitat"].lower()] if a.get("Habitat") else [],
            "diet": [a["Trophic.Niche"].lower()] if a.get("Trophic.Niche") else [],
            "lifestyle": a.get("Primary.Lifestyle"),
            "trait_source": "AVONET (Tobias et al. 2022)",
        }
    return {
        "migration": None,
        "habitats": [],
        "diet": [],
        "lifestyle": None,
        "trait_source": None,
    }


def _read_reference(con) -> tuple[dict, dict]:
    def load(name: str, key: str) -> dict:
        rel = con.execute(
            f"SELECT * FROM read_csv('{REFERENCE / name}', header = true, all_varchar = true)"
        )
        cols = [d[0] for d in rel.description]
        return {
            r[cols.index(key)]: dict(zip(cols, r, strict=True)) for r in rel.fetchall()
        }

    return load("storchova_horak_2018.csv", "scientificNameStd"), load(
        "avonet_birdlife.csv", "Species1"
    )


def observed_patterns(con, out: str) -> dict[str, dict]:
    """Diel peak, night share and active months per country, from TABMON data."""
    diel = con.execute(
        f"""
        WITH occ AS (SELECT DISTINCT species, site_id FROM read_parquet('{out}/phenology.parquet')),
        ef AS (
            SELECT o.species, e.hour, sum(e.recorded_minutes) AS m
            FROM read_parquet('{out}/effort.parquet') e JOIN occ o USING (site_id)
            GROUP BY ALL
        ),
        ph AS (
            SELECT species, hour, sum(n_detections) AS n
            FROM read_parquet('{out}/phenology.parquet') GROUP BY ALL
        )
        SELECT ef.species, ef.hour, COALESCE(ph.n, 0) / (ef.m / 60) AS rate
        FROM ef LEFT JOIN ph USING (species, hour)
        WHERE ef.m > 0
        """
    ).fetchall()
    monthly = con.execute(
        f"""
        WITH dep AS (
            SELECT DISTINCT site_id, country FROM read_parquet('{out}/deployments.parquet')
        ),
        occ AS (SELECT DISTINCT species, site_id FROM read_parquet('{out}/phenology.parquet')),
        ef AS (
            SELECT o.species, d.country, month(e.date) AS mo, sum(e.recorded_minutes) AS m
            FROM read_parquet('{out}/effort.parquet') e
            JOIN occ o USING (site_id) JOIN dep d USING (site_id)
            GROUP BY ALL
        ),
        ph AS (
            SELECT p.species, d.country, month(p.date) AS mo, sum(p.n_detections) AS n
            FROM read_parquet('{out}/phenology.parquet') p JOIN dep d USING (site_id)
            GROUP BY ALL
        )
        SELECT ef.species, ef.country, ef.mo, COALESCE(ph.n, 0) AS n, ef.m
        FROM ef LEFT JOIN ph USING (species, country, mo)
        WHERE ef.m >= 600
        """
    ).fetchall()

    result: dict[str, dict] = {}
    by_species: dict[str, dict[int, float]] = {}
    for sp, hour, rate in diel:
        by_species.setdefault(sp, {})[hour] = rate
    for sp, hours in by_species.items():
        total = sum(hours.values())
        if total <= 0:
            continue
        result[sp] = {
            "peak_hour": max(hours, key=hours.get),
            "night_share": sum(hours.get(h, 0) for h in NIGHT_HOURS) / total,
        }

    months: dict[str, dict[str, dict[int, float]]] = {}
    for sp, country, mo, n, m in monthly:
        months.setdefault(sp, {}).setdefault(country, {})[mo] = n / (m / 60)
    for sp, countries in months.items():
        active = {}
        for country, rates in countries.items():
            peak = max(rates.values())
            # Ignore countries where the species is barely detected at all.
            if peak <= 0 or sum(1 for r in rates.values() if r > 0) < 2:
                continue
            active[country] = sorted(mo for mo, r in rates.items() if r >= 0.15 * peak)
        result.setdefault(sp, {})["active_months"] = json.dumps(active)
    return result


def build_species_ecology(con, out: str) -> None:
    log.info("species ecology (traits, IUCN, observed patterns)")
    species = [
        r[0]
        for r in con.execute(
            f"SELECT species FROM read_parquet('{out}/species.parquet')"
        ).fetchall()
    ]
    storchova, avonet = _read_reference(con)
    iucn = fetch_iucn(species)
    if not iucn:  # Wikidata unavailable: keep the previous values
        try:
            iucn = dict(
                con.execute(
                    f"SELECT species, iucn FROM read_parquet('{out}/species_ecology.parquet') "
                    f"WHERE iucn IS NOT NULL"
                ).fetchall()
            )
        except Exception:  # noqa: BLE001 - retain prior cache when unavailable
            iucn = {}
    observed = observed_patterns(con, out)
    log.info(
        "IUCN for %d/%d species, traits for %d",
        len(iucn),
        len(species),
        sum(1 for s in species if s in storchova or s in avonet),
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE species_ecology (
            species VARCHAR, iucn VARCHAR, migration VARCHAR, habitats VARCHAR[],
            diet VARCHAR[], lifestyle VARCHAR, trait_source VARCHAR,
            peak_hour INTEGER, night_share DOUBLE, active_months VARCHAR
        )
        """
    )
    rows = []
    for sp in species:
        t = traits_for(sp, storchova, avonet)
        o = observed.get(sp, {})
        rows.append(
            [
                sp,
                iucn.get(sp),
                t["migration"],
                t["habitats"],
                t["diet"],
                t["lifestyle"],
                t["trait_source"],
                o.get("peak_hour"),
                o.get("night_share"),
                o.get("active_months"),
            ]
        )
    con.executemany(
        "INSERT INTO species_ecology VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    con.execute(
        f"COPY species_ecology TO '{out}/species_ecology.parquet' (FORMAT parquet)"
    )
