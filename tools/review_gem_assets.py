from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


EXPOSURE = Path(r"C:\Users\Daniel.Mhango\Documents\GlobalExposureModel\global_exposure_model")
VULNERABILITY = Path(r"C:\Users\Daniel.Mhango\Documents\GlobalVulnerabilityModel\global_vulnerability_model")
COUNTRIES = {
    "Indonesia": ("Southeast_Asia", "Indonesia", "IDN"),
    "Nepal": ("South_Asia", "Nepal", "NPL"),
}


def safe_float(series):
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def taxonomy_parts(value):
    parts = str(value).split("/")
    material = parts[0] if parts else "UNKNOWN"
    height = next((part for part in parts if part.startswith("H")), "UNKNOWN")
    occupancy = parts[-1] if parts else "UNKNOWN"
    return material, height, occupancy


def weighted_top(df, group_col, value_col, n=8):
    grouped = df.groupby(group_col, dropna=False)[value_col].sum().sort_values(ascending=False)
    total = grouped.sum()
    return [
        {
            "category": str(index),
            "value": round(float(value), 2),
            "share": round(float(value / total), 6) if total else 0,
        }
        for index, value in grouped.head(n).items()
    ]


def parse_vulnerability(path):
    root = ET.parse(path).getroot()
    functions = []
    for element in root.iter():
        if element.tag.endswith("vulnerabilityFunction"):
            imls = next((child for child in element if child.tag.endswith("imls")), None)
            functions.append(
                {
                    "id": element.attrib.get("id"),
                    "dist": element.attrib.get("dist"),
                    "imt": imls.attrib.get("imt") if imls is not None else None,
                }
            )
    return functions


def country_review(region, country, iso3):
    exposure_dir = EXPOSURE / region / country
    vulnerability_dir = VULNERABILITY / region / country
    frames = []
    file_summary = {}
    for occupancy in ("Res", "Com", "Ind"):
        path = exposure_dir / f"Exposure_{occupancy}_{country}_Adm1.csv"
        df = pd.read_csv(path)
        for col in ("BUILDINGS", "TOTAL_REPL_COST_USD", "TOTAL_AREA_SQM", "COST_STRUCTURAL_USD", "COST_NONSTRUCTURAL_USD", "COST_CONTENTS_USD"):
            df[col] = safe_float(df[col])
        df["FILE_OCCUPANCY"] = occupancy
        frames.append(df)
        file_summary[occupancy] = {
            "rows": int(len(df)),
            "admin1": int(df["ID_1"].nunique()),
            "taxonomies": int(df["TAXONOMY"].nunique()),
            "buildings": round(float(df["BUILDINGS"].sum()), 2),
            "replacement_cost_usd": round(float(df["TOTAL_REPL_COST_USD"].sum()), 2),
            "area_sqm": round(float(df["TOTAL_AREA_SQM"].sum()), 2),
        }

    all_df = pd.concat(frames, ignore_index=True)
    all_df[["MATERIAL", "HEIGHT", "OCC_DETAIL"]] = all_df["TAXONOMY"].apply(
        lambda value: pd.Series(taxonomy_parts(value))
    )
    totals = {
        "rows": int(len(all_df)),
        "admin1": int(all_df["ID_1"].nunique()),
        "taxonomies": int(all_df["TAXONOMY"].nunique()),
        "buildings": round(float(all_df["BUILDINGS"].sum()), 2),
        "replacement_cost_usd": round(float(all_df["TOTAL_REPL_COST_USD"].sum()), 2),
        "area_sqm": round(float(all_df["TOTAL_AREA_SQM"].sum()), 2),
    }
    occupancy = []
    for name, df in all_df.groupby("FILE_OCCUPANCY"):
        occupancy.append(
            {
                "occupancy": name,
                "building_share": round(float(df["BUILDINGS"].sum() / totals["buildings"]), 6),
                "cost_share": round(float(df["TOTAL_REPL_COST_USD"].sum() / totals["replacement_cost_usd"]), 6),
                "area_share": round(float(df["TOTAL_AREA_SQM"].sum() / totals["area_sqm"]), 6),
                "avg_cost_per_building": round(float(df["TOTAL_REPL_COST_USD"].sum() / df["BUILDINGS"].sum()), 2),
            }
        )

    nonres = all_df[all_df["FILE_OCCUPANCY"].isin(["Com", "Ind"])].copy()
    component_totals = {
        col: round(float(nonres[col].sum()), 2)
        for col in ("COST_STRUCTURAL_USD", "COST_NONSTRUCTURAL_USD", "COST_CONTENTS_USD")
    }
    component_denominator = sum(component_totals.values())
    component_shares = {
        key: round(value / component_denominator, 6) if component_denominator else 0
        for key, value in component_totals.items()
    }

    mapping_path = vulnerability_dir / f"taxonomy_mapping_{country}.csv"
    mapping = pd.read_csv(mapping_path)
    mapping["weight"] = pd.to_numeric(mapping["weight"], errors="coerce")
    mapping_grouped = mapping.groupby("taxonomy").agg(
        target_count=("conversion", "count"), weight_sum=("weight", "sum")
    )

    vulnerability_files = {}
    all_function_ids = set()
    for kind in ("structural", "nonstructural", "contents", "fatalities"):
        path = vulnerability_dir / f"vulnerability_{kind}.xml"
        funcs = parse_vulnerability(path)
        ids = {item["id"] for item in funcs}
        all_function_ids.update(ids)
        vulnerability_files[kind] = {
            "function_count": len(funcs),
            "imts": dict(sorted(Counter(item["imt"] for item in funcs).items())),
            "distributions": dict(sorted(Counter(item["dist"] for item in funcs).items())),
        }

    exposure_taxonomies = set(all_df["TAXONOMY"])
    mapped_taxonomies = set(mapping["taxonomy"])
    conversion_targets = set(mapping["conversion"])
    missing_mapping = exposure_taxonomies - mapped_taxonomies
    missing_target = conversion_targets - all_function_ids
    mapped_mask = all_df["TAXONOMY"].isin(mapped_taxonomies)

    return {
        "country": country,
        "iso3": iso3,
        "files": file_summary,
        "total": totals,
        "occupancy_distribution": sorted(occupancy, key=lambda x: x["occupancy"]),
        "nonresidential_component_shares": component_shares,
        "nonresidential_material_by_cost": weighted_top(nonres, "MATERIAL", "TOTAL_REPL_COST_USD"),
        "nonresidential_material_by_buildings": weighted_top(nonres, "MATERIAL", "BUILDINGS"),
        "nonresidential_height_by_cost": weighted_top(nonres, "HEIGHT", "TOTAL_REPL_COST_USD"),
        "nonresidential_top_taxonomies_by_cost": weighted_top(nonres, "TAXONOMY", "TOTAL_REPL_COST_USD", 12),
        "mapping": {
            "rows": int(len(mapping)),
            "source_taxonomies": int(mapping["taxonomy"].nunique()),
            "conversion_targets": int(mapping["conversion"].nunique()),
            "multi_target_sources": int((mapping_grouped["target_count"] > 1).sum()),
            "non_unit_weight_sources": int((mapping_grouped["weight_sum"].round(8) != 1).sum()),
            "exposure_taxonomy_coverage_count": round(float(mapped_mask.mean()), 6),
            "exposure_cost_coverage": round(float(all_df.loc[mapped_mask, "TOTAL_REPL_COST_USD"].sum() / totals["replacement_cost_usd"]), 6),
            "unmapped_exposure_taxonomies": sorted(missing_mapping),
            "conversion_targets_missing_from_all_vulnerability_files": sorted(missing_target),
        },
        "vulnerability": vulnerability_files,
    }


def main():
    result = {
        country: country_review(*values)
        for country, values in COUNTRIES.items()
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
