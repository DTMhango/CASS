"""ISO 3166-1: the alpha-3 code GEM writes, and the alpha-2 code CASS is keyed by.

GEM's exposure model names a country by alpha-3 in every summary it publishes
(``ID_0``), and its taxonomy mapping is keyed the same way. The rest of CASS --
an OED schedule's ``CountryCode``, a vulnerability set, a grid -- is keyed by
alpha-2. Asking somebody to type both beside a country they have already chosen
invites exactly the error that matters: Indonesia's functions registered as
Zambia's, weighted by Zambia's mapping.

So neither is typed. The alpha-3 is read from the release, and the alpha-2 is
looked up here. This is the published standard rather than a list CASS keeps
of its own, and it is not a guess: every code maps to exactly one other.

Generated from the ISO 3166-1 data Debian's iso-codes project maintains, as
packaged in pycountry 24.6.1. The 215 countries GEM v2026.0.0 publishes all
resolve through it, Kosovo aside; see ``USER_ASSIGNED``.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Officially assigned ISO 3166-1 codes, alpha-3 to alpha-2.
OFFICIAL: Mapping[str, str] = {
    "ABW": "AW",  # Aruba
    "AFG": "AF",  # Afghanistan
    "AGO": "AO",  # Angola
    "AIA": "AI",  # Anguilla
    "ALA": "AX",  # Åland Islands
    "ALB": "AL",  # Albania
    "AND": "AD",  # Andorra
    "ARE": "AE",  # United Arab Emirates
    "ARG": "AR",  # Argentina
    "ARM": "AM",  # Armenia
    "ASM": "AS",  # American Samoa
    "ATA": "AQ",  # Antarctica
    "ATF": "TF",  # French Southern Territories
    "ATG": "AG",  # Antigua and Barbuda
    "AUS": "AU",  # Australia
    "AUT": "AT",  # Austria
    "AZE": "AZ",  # Azerbaijan
    "BDI": "BI",  # Burundi
    "BEL": "BE",  # Belgium
    "BEN": "BJ",  # Benin
    "BES": "BQ",  # Bonaire, Sint Eustatius and Saba
    "BFA": "BF",  # Burkina Faso
    "BGD": "BD",  # Bangladesh
    "BGR": "BG",  # Bulgaria
    "BHR": "BH",  # Bahrain
    "BHS": "BS",  # Bahamas
    "BIH": "BA",  # Bosnia and Herzegovina
    "BLM": "BL",  # Saint Barthélemy
    "BLR": "BY",  # Belarus
    "BLZ": "BZ",  # Belize
    "BMU": "BM",  # Bermuda
    "BOL": "BO",  # Bolivia
    "BRA": "BR",  # Brazil
    "BRB": "BB",  # Barbados
    "BRN": "BN",  # Brunei Darussalam
    "BTN": "BT",  # Bhutan
    "BVT": "BV",  # Bouvet Island
    "BWA": "BW",  # Botswana
    "CAF": "CF",  # Central African Republic
    "CAN": "CA",  # Canada
    "CCK": "CC",  # Cocos (Keeling) Islands
    "CHE": "CH",  # Switzerland
    "CHL": "CL",  # Chile
    "CHN": "CN",  # China
    "CIV": "CI",  # Côte d'Ivoire
    "CMR": "CM",  # Cameroon
    "COD": "CD",  # Congo, The Democratic Republic of the
    "COG": "CG",  # Congo
    "COK": "CK",  # Cook Islands
    "COL": "CO",  # Colombia
    "COM": "KM",  # Comoros
    "CPV": "CV",  # Cabo Verde
    "CRI": "CR",  # Costa Rica
    "CUB": "CU",  # Cuba
    "CUW": "CW",  # Curaçao
    "CXR": "CX",  # Christmas Island
    "CYM": "KY",  # Cayman Islands
    "CYP": "CY",  # Cyprus
    "CZE": "CZ",  # Czechia
    "DEU": "DE",  # Germany
    "DJI": "DJ",  # Djibouti
    "DMA": "DM",  # Dominica
    "DNK": "DK",  # Denmark
    "DOM": "DO",  # Dominican Republic
    "DZA": "DZ",  # Algeria
    "ECU": "EC",  # Ecuador
    "EGY": "EG",  # Egypt
    "ERI": "ER",  # Eritrea
    "ESH": "EH",  # Western Sahara
    "ESP": "ES",  # Spain
    "EST": "EE",  # Estonia
    "ETH": "ET",  # Ethiopia
    "FIN": "FI",  # Finland
    "FJI": "FJ",  # Fiji
    "FLK": "FK",  # Falkland Islands (Malvinas)
    "FRA": "FR",  # France
    "FRO": "FO",  # Faroe Islands
    "FSM": "FM",  # Micronesia, Federated States of
    "GAB": "GA",  # Gabon
    "GBR": "GB",  # United Kingdom
    "GEO": "GE",  # Georgia
    "GGY": "GG",  # Guernsey
    "GHA": "GH",  # Ghana
    "GIB": "GI",  # Gibraltar
    "GIN": "GN",  # Guinea
    "GLP": "GP",  # Guadeloupe
    "GMB": "GM",  # Gambia
    "GNB": "GW",  # Guinea-Bissau
    "GNQ": "GQ",  # Equatorial Guinea
    "GRC": "GR",  # Greece
    "GRD": "GD",  # Grenada
    "GRL": "GL",  # Greenland
    "GTM": "GT",  # Guatemala
    "GUF": "GF",  # French Guiana
    "GUM": "GU",  # Guam
    "GUY": "GY",  # Guyana
    "HKG": "HK",  # Hong Kong
    "HMD": "HM",  # Heard Island and McDonald Islands
    "HND": "HN",  # Honduras
    "HRV": "HR",  # Croatia
    "HTI": "HT",  # Haiti
    "HUN": "HU",  # Hungary
    "IDN": "ID",  # Indonesia
    "IMN": "IM",  # Isle of Man
    "IND": "IN",  # India
    "IOT": "IO",  # British Indian Ocean Territory
    "IRL": "IE",  # Ireland
    "IRN": "IR",  # Iran
    "IRQ": "IQ",  # Iraq
    "ISL": "IS",  # Iceland
    "ISR": "IL",  # Israel
    "ITA": "IT",  # Italy
    "JAM": "JM",  # Jamaica
    "JEY": "JE",  # Jersey
    "JOR": "JO",  # Jordan
    "JPN": "JP",  # Japan
    "KAZ": "KZ",  # Kazakhstan
    "KEN": "KE",  # Kenya
    "KGZ": "KG",  # Kyrgyzstan
    "KHM": "KH",  # Cambodia
    "KIR": "KI",  # Kiribati
    "KNA": "KN",  # Saint Kitts and Nevis
    "KOR": "KR",  # South Korea
    "KWT": "KW",  # Kuwait
    "LAO": "LA",  # Laos
    "LBN": "LB",  # Lebanon
    "LBR": "LR",  # Liberia
    "LBY": "LY",  # Libya
    "LCA": "LC",  # Saint Lucia
    "LIE": "LI",  # Liechtenstein
    "LKA": "LK",  # Sri Lanka
    "LSO": "LS",  # Lesotho
    "LTU": "LT",  # Lithuania
    "LUX": "LU",  # Luxembourg
    "LVA": "LV",  # Latvia
    "MAC": "MO",  # Macao
    "MAF": "MF",  # Saint Martin (French part)
    "MAR": "MA",  # Morocco
    "MCO": "MC",  # Monaco
    "MDA": "MD",  # Moldova
    "MDG": "MG",  # Madagascar
    "MDV": "MV",  # Maldives
    "MEX": "MX",  # Mexico
    "MHL": "MH",  # Marshall Islands
    "MKD": "MK",  # North Macedonia
    "MLI": "ML",  # Mali
    "MLT": "MT",  # Malta
    "MMR": "MM",  # Myanmar
    "MNE": "ME",  # Montenegro
    "MNG": "MN",  # Mongolia
    "MNP": "MP",  # Northern Mariana Islands
    "MOZ": "MZ",  # Mozambique
    "MRT": "MR",  # Mauritania
    "MSR": "MS",  # Montserrat
    "MTQ": "MQ",  # Martinique
    "MUS": "MU",  # Mauritius
    "MWI": "MW",  # Malawi
    "MYS": "MY",  # Malaysia
    "MYT": "YT",  # Mayotte
    "NAM": "NA",  # Namibia
    "NCL": "NC",  # New Caledonia
    "NER": "NE",  # Niger
    "NFK": "NF",  # Norfolk Island
    "NGA": "NG",  # Nigeria
    "NIC": "NI",  # Nicaragua
    "NIU": "NU",  # Niue
    "NLD": "NL",  # Netherlands
    "NOR": "NO",  # Norway
    "NPL": "NP",  # Nepal
    "NRU": "NR",  # Nauru
    "NZL": "NZ",  # New Zealand
    "OMN": "OM",  # Oman
    "PAK": "PK",  # Pakistan
    "PAN": "PA",  # Panama
    "PCN": "PN",  # Pitcairn
    "PER": "PE",  # Peru
    "PHL": "PH",  # Philippines
    "PLW": "PW",  # Palau
    "PNG": "PG",  # Papua New Guinea
    "POL": "PL",  # Poland
    "PRI": "PR",  # Puerto Rico
    "PRK": "KP",  # North Korea
    "PRT": "PT",  # Portugal
    "PRY": "PY",  # Paraguay
    "PSE": "PS",  # Palestine, State of
    "PYF": "PF",  # French Polynesia
    "QAT": "QA",  # Qatar
    "REU": "RE",  # Réunion
    "ROU": "RO",  # Romania
    "RUS": "RU",  # Russian Federation
    "RWA": "RW",  # Rwanda
    "SAU": "SA",  # Saudi Arabia
    "SDN": "SD",  # Sudan
    "SEN": "SN",  # Senegal
    "SGP": "SG",  # Singapore
    "SGS": "GS",  # South Georgia and the South Sandwich Islands
    "SHN": "SH",  # Saint Helena, Ascension and Tristan da Cunha
    "SJM": "SJ",  # Svalbard and Jan Mayen
    "SLB": "SB",  # Solomon Islands
    "SLE": "SL",  # Sierra Leone
    "SLV": "SV",  # El Salvador
    "SMR": "SM",  # San Marino
    "SOM": "SO",  # Somalia
    "SPM": "PM",  # Saint Pierre and Miquelon
    "SRB": "RS",  # Serbia
    "SSD": "SS",  # South Sudan
    "STP": "ST",  # Sao Tome and Principe
    "SUR": "SR",  # Suriname
    "SVK": "SK",  # Slovakia
    "SVN": "SI",  # Slovenia
    "SWE": "SE",  # Sweden
    "SWZ": "SZ",  # Eswatini
    "SXM": "SX",  # Sint Maarten (Dutch part)
    "SYC": "SC",  # Seychelles
    "SYR": "SY",  # Syria
    "TCA": "TC",  # Turks and Caicos Islands
    "TCD": "TD",  # Chad
    "TGO": "TG",  # Togo
    "THA": "TH",  # Thailand
    "TJK": "TJ",  # Tajikistan
    "TKL": "TK",  # Tokelau
    "TKM": "TM",  # Turkmenistan
    "TLS": "TL",  # Timor-Leste
    "TON": "TO",  # Tonga
    "TTO": "TT",  # Trinidad and Tobago
    "TUN": "TN",  # Tunisia
    "TUR": "TR",  # Türkiye
    "TUV": "TV",  # Tuvalu
    "TWN": "TW",  # Taiwan
    "TZA": "TZ",  # Tanzania
    "UGA": "UG",  # Uganda
    "UKR": "UA",  # Ukraine
    "UMI": "UM",  # United States Minor Outlying Islands
    "URY": "UY",  # Uruguay
    "USA": "US",  # United States
    "UZB": "UZ",  # Uzbekistan
    "VAT": "VA",  # Holy See (Vatican City State)
    "VCT": "VC",  # Saint Vincent and the Grenadines
    "VEN": "VE",  # Venezuela
    "VGB": "VG",  # Virgin Islands, British
    "VIR": "VI",  # Virgin Islands, U.S.
    "VNM": "VN",  # Vietnam
    "VUT": "VU",  # Vanuatu
    "WLF": "WF",  # Wallis and Futuna
    "WSM": "WS",  # Samoa
    "YEM": "YE",  # Yemen
    "ZAF": "ZA",  # South Africa
    "ZMB": "ZM",  # Zambia
    "ZWE": "ZW",  # Zimbabwe
}

#: Codes outside the standard that GEM uses anyway. ISO reserves the X block for
#: exactly this, and Kosovo's XK/XKX pair is the one the European Commission, the
#: World Bank and GEM itself use. It is kept apart so nobody mistakes it for an
#: official assignment.
USER_ASSIGNED: Mapping[str, str] = {
    "XKX": "XK",  # Kosovo
}

ALPHA_2: Mapping[str, str] = {**OFFICIAL, **USER_ASSIGNED}


def alpha_2(alpha_3: str) -> str:
    """The alpha-2 code for an alpha-3 one, or ``""`` where there is none."""
    return ALPHA_2.get(str(alpha_3 or "").strip().upper(), "")
