"""Build a test portfolio for the Jakarta-Bandung earthquake run.

Nothing here is a real book. It exists so the platform can be exercised end to
end: every location falls inside the region the hazard run computed, every
taxonomy resolves to a single intensity measure in the GEM mapping (so the
correlated-channel representation is not needed), and the financial terms are
simple enough to check by hand.

Values are US dollars. The portfolio is one currency because the Oasis
Financial Module does not calculate multi-currency terms and CASS refuses a
mixed-currency portfolio rather than converting silently.
"""

from __future__ import annotations

import csv
import pathlib
import random

HERE = pathlib.Path(__file__).parent
PORT = "IDJB01"
OED_VERSION = "4.0.0"

# Places inside the computed region (longitude 106.5 to 107.9, latitude -7.2 to
# -5.9), with the OED area code of the province each sits in: 31 DKI Jakarta,
# 32 Jawa Barat, 36 Banten. OED takes the code, not the name.
PLACES = [
    ("Jakarta Pusat", -6.186, 106.834, "31"),
    ("Jakarta Selatan", -6.261, 106.810, "31"),
    ("Jakarta Utara", -6.121, 106.887, "31"),
    ("Jakarta Barat", -6.168, 106.759, "31"),
    ("Jakarta Timur", -6.225, 106.900, "31"),
    ("Tangerang", -6.178, 106.630, "36"),
    ("Tangerang Selatan", -6.294, 106.711, "36"),
    ("Bekasi", -6.238, 106.995, "32"),
    ("Cikarang", -6.261, 107.152, "32"),
    ("Depok", -6.402, 106.794, "32"),
    ("Bogor", -6.595, 106.816, "32"),
    ("Sukabumi", -6.923, 106.928, "32"),
    ("Cianjur", -6.820, 107.142, "32"),
    ("Karawang", -6.305, 107.306, "32"),
    ("Purwakarta", -6.556, 107.443, "32"),
    ("Subang", -6.571, 107.760, "32"),
    ("Bandung", -6.917, 107.619, "32"),
    ("Cimahi", -6.872, 107.542, "32"),
    ("Lembang", -6.812, 107.617, "32"),
    ("Soreang", -7.030, 107.519, "32"),
]

# Taxonomies that resolve to exactly one intensity measure in the GEM mapping
# this model version carries: occupancy, construction, storeys, and the measure
# the keys service will route them to.
CLASSES = [
    ("1050", "5050", 2, "SA(0.3)", "Residential, wood frame, low-rise"),
    ("1050", "5100", 2, "PGA", "Residential, masonry, low-rise"),
    ("1050", "5100", 3, "PGA", "Residential, masonry, low-rise"),
    ("1100", "5050", 5, "SA(0.3)", "Commercial, wood frame, mid-rise"),
    ("1100", "5100", 3, "PGA", "Commercial, masonry, low-rise"),
    ("1100", "5150", 12, "SA(1.0)", "Commercial, concrete, high-rise"),
    ("1100", "5200", 6, "PGA", "Commercial, steel, mid-rise"),
    ("1150", "5150", 2, "PGA", "Industrial, concrete, low-rise"),
    ("1150", "5200", 3, "PGA", "Industrial, steel, low-rise"),
]

ACCOUNTS = [
    # reference, name, the classes it writes, how many locations, building TIV band
    ("ACC001", "Residential householders scheme", [0, 1, 2], 26, (180_000, 900_000)),
    ("ACC002", "Commercial property programme", [3, 4, 5, 6], 22, (4_000_000, 38_000_000)),
    ("ACC003", "Industrial and warehousing", [7, 8], 16, (6_000_000, 45_000_000)),
]


def money(value: float) -> str:
    return f"{round(value, 2):.2f}"


def locations(rng: random.Random) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for account, _name, class_indices, count, (low, high) in ACCOUNTS:
        for index in range(count):
            place, lat, lon, area = PLACES[(index * 3 + len(rows)) % len(PLACES)]
            occupancy, construction, storeys, _measure, _label = CLASSES[
                class_indices[index % len(class_indices)]
            ]
            building = rng.uniform(low, high)
            # Scattered within about 4 km of the place, so several cells of the
            # area-peril grid are exercised rather than one.
            latitude = round(lat + rng.uniform(-0.035, 0.035), 5)
            longitude = round(lon + rng.uniform(-0.035, 0.035), 5)
            contents = building * rng.uniform(0.25, 0.55)
            other = building * rng.uniform(0.0, 0.12)
            interruption = building * rng.uniform(0.1, 0.4) if account != "ACC001" else 0.0
            total = building + contents + other + interruption
            rows.append(
                {
                    "PortNumber": PORT,
                    "AccNumber": account,
                    "LocNumber": f"{account}-L{index + 1:03d}",
                    "BuildingID": 1,
                    "CountryCode": "ID",
                    "Latitude": latitude,
                    "Longitude": longitude,
                    "StreetAddress": f"{rng.randint(1, 220)} Jalan {place}",
                    "AreaCode": area,
                    "OccupancyCode": occupancy,
                    "ConstructionCode": construction,
                    "YearBuilt": rng.choice([1978, 1985, 1994, 2003, 2011, 2016, 2021]),
                    "NumberOfStoreys": storeys,
                    "LocPerilsCovered": "QEQ",
                    "BuildingTIV": money(building),
                    "OtherTIV": money(other),
                    "ContentsTIV": money(contents),
                    "BITIV": money(interruption),
                    "LocCurrency": "USD",
                    # A location deductible of 2.5% of total insured value on
                    # the commercial and industrial books, none on the
                    # householders scheme; a location limit only where the
                    # deductible sits, so the two are readable together.
                    "LocDed6All": money(0 if account == "ACC001" else total * 0.025),
                    "LocLimit6All": money(0 if account == "ACC001" else total * 0.8),
                    # OED requires the basis and the peril beside the amount.
                    # 0 is a flat monetary amount, which is what CASS calculates.
                    "LocPeril": "QEQ",
                    "LocDedType6All": 0,
                    "LocLimitType6All": 0,
                    "LocGroup": f"P{area}-{occupancy}",
                    "OEDVersion": OED_VERSION,
                }
            )
    return rows


ACCOUNT_ROWS = [
    # One straightforward policy, one two-layer tower, one policy with a large
    # annual aggregate-style limit, all incepting on the same date.
    {
        "PortNumber": PORT, "AccNumber": "ACC001", "AccCurrency": "USD",
        "PolNumber": "ACC001-P1", "PolPerilsCovered": "QEQ",
        "PolInceptionDate": "2026-01-01", "PolExpiryDate": "2026-12-31",
        "LayerNumber": 1, "LayerParticipation": "1.0",
        "LayerLimit": "0.00", "LayerAttachment": "0.00",
        "PolDed6All": "250000.00", "PolLimit6All": "40000000.00",
        "PolPeril": "QEQ", "PolDedType6All": 0, "PolLimitType6All": 0,
        "OEDVersion": OED_VERSION,
    },
    {
        "PortNumber": PORT, "AccNumber": "ACC002", "AccCurrency": "USD",
        "PolNumber": "ACC002-P1", "PolPerilsCovered": "QEQ",
        "PolInceptionDate": "2026-01-01", "PolExpiryDate": "2026-12-31",
        "LayerNumber": 1, "LayerParticipation": "1.0",
        "LayerLimit": "25000000.00", "LayerAttachment": "5000000.00",
        "PolDed6All": "1000000.00", "PolLimit6All": "0.00",
        "PolPeril": "QEQ", "PolDedType6All": 0, "PolLimitType6All": 0,
        "OEDVersion": OED_VERSION,
    },
    {
        "PortNumber": PORT, "AccNumber": "ACC002", "AccCurrency": "USD",
        "PolNumber": "ACC002-P1", "PolPerilsCovered": "QEQ",
        "PolInceptionDate": "2026-01-01", "PolExpiryDate": "2026-12-31",
        "LayerNumber": 2, "LayerParticipation": "0.5",
        "LayerLimit": "70000000.00", "LayerAttachment": "30000000.00",
        "PolDed6All": "0.00", "PolLimit6All": "0.00",
        "PolPeril": "QEQ", "PolDedType6All": 0, "PolLimitType6All": 0,
        "OEDVersion": OED_VERSION,
    },
    {
        "PortNumber": PORT, "AccNumber": "ACC003", "AccCurrency": "USD",
        "PolNumber": "ACC003-P1", "PolPerilsCovered": "QEQ",
        "PolInceptionDate": "2026-01-01", "PolExpiryDate": "2026-12-31",
        "LayerNumber": 1, "LayerParticipation": "0.75",
        "LayerLimit": "60000000.00", "LayerAttachment": "2500000.00",
        "PolDed6All": "500000.00", "PolLimit6All": "0.00",
        "PolPeril": "QEQ", "PolDedType6All": 0, "PolLimitType6All": 0,
        "OEDVersion": OED_VERSION,
    },
]

REINS_INFO_ROWS = [
    {
        "ReinsNumber": 1, "ReinsLayerNumber": 1,
        "ReinsName": "Indonesia property quota share",
        "ReinsPeril": "QEQ",
        "ReinsInceptionDate": "2026-01-01", "ReinsExpiryDate": "2026-12-31",
        "CededPercent": "0.3", "RiskLimit": "0.00", "RiskAttachment": "0.00",
        "OccLimit": "0.00", "OccAttachment": "0.00", "PlacedPercent": "1.0",
        "ReinsCurrency": "USD", "InuringPriority": 1, "ReinsType": "QS",
        "RiskLevel": "ACC", "UseReinsDates": 1, "OEDVersion": OED_VERSION,
    },
    {
        "ReinsNumber": 2, "ReinsLayerNumber": 1,
        "ReinsName": "Industrial per-risk excess",
        "ReinsPeril": "QEQ",
        "ReinsInceptionDate": "2026-01-01", "ReinsExpiryDate": "2026-12-31",
        "CededPercent": "1.0", "RiskLimit": "8000000.00", "RiskAttachment": "2000000.00",
        "OccLimit": "0.00", "OccAttachment": "0.00", "PlacedPercent": "0.9",
        "ReinsCurrency": "USD", "InuringPriority": 2, "ReinsType": "PR",
        "RiskLevel": "LOC", "UseReinsDates": 1, "OEDVersion": OED_VERSION,
    },
    {
        "ReinsNumber": 3, "ReinsLayerNumber": 1,
        "ReinsName": "Catastrophe excess, first layer",
        "ReinsPeril": "QEQ",
        "ReinsInceptionDate": "2026-01-01", "ReinsExpiryDate": "2026-12-31",
        "CededPercent": "1.0", "RiskLimit": "0.00", "RiskAttachment": "0.00",
        "OccLimit": "20000000.00", "OccAttachment": "5000000.00", "PlacedPercent": "1.0",
        "ReinsCurrency": "USD", "InuringPriority": 3, "ReinsType": "CXL",
        "RiskLevel": "", "UseReinsDates": 1, "OEDVersion": OED_VERSION,
    },
    {
        "ReinsNumber": 3, "ReinsLayerNumber": 2,
        "ReinsName": "Catastrophe excess, second layer",
        "ReinsPeril": "QEQ",
        "ReinsInceptionDate": "2026-01-01", "ReinsExpiryDate": "2026-12-31",
        "CededPercent": "1.0", "RiskLimit": "0.00", "RiskAttachment": "0.00",
        "OccLimit": "50000000.00", "OccAttachment": "25000000.00", "PlacedPercent": "0.85",
        "ReinsCurrency": "USD", "InuringPriority": 3, "ReinsType": "CXL",
        "RiskLevel": "", "UseReinsDates": 1, "OEDVersion": OED_VERSION,
    },
]

REINS_SCOPE_ROWS = [
    # The quota share takes the two commercial and industrial accounts; the
    # householders scheme is retained net.
    {"ReinsNumber": 1, "PortNumber": PORT, "AccNumber": "ACC002",
     "CededPercent": "0.3", "CountryCode": "ID", "OEDVersion": OED_VERSION},
    {"ReinsNumber": 1, "PortNumber": PORT, "AccNumber": "ACC003",
     "CededPercent": "0.3", "CountryCode": "ID", "OEDVersion": OED_VERSION},
    # The per-risk excess protects the industrial account only.
    {"ReinsNumber": 2, "PortNumber": PORT, "AccNumber": "ACC003",
     "CededPercent": "1.0", "CountryCode": "ID", "OEDVersion": OED_VERSION},
    # The catastrophe programme protects everything that is left.
    {"ReinsNumber": 3, "PortNumber": PORT,
     "CededPercent": "1.0", "CountryCode": "ID", "OEDVersion": OED_VERSION},
]


def write(name: str, rows: list[dict[str, object]]) -> pathlib.Path:
    path = HERE / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    rng = random.Random(20260913)
    location_rows = locations(rng)
    paths = [
        write("location.csv", location_rows),
        write("account.csv", ACCOUNT_ROWS),
        write("reinsinfo.csv", REINS_INFO_ROWS),
        write("reinsscope.csv", REINS_SCOPE_ROWS),
    ]
    total = sum(
        float(row[column])
        for row in location_rows
        for column in ("BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV")
    )
    print(f"{len(location_rows)} locations, total insured value USD {total:,.0f}")
    for path in paths:
        print(" ", path.name, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
