# GEM model manifest — v2026.0.0

Downloaded from the official GEM GitHub repositories on 2026-09-11.

## Pinned repositories

- Global Exposure Model
  - Source: https://github.com/gem/global_exposure_model.git
  - Tag: `v2026.0.0`
  - Commit: `c3add51f4e56f9d10477c8f6b5e24fd89fe089a1`
- Global Vulnerability Model
  - Source: https://github.com/gem/global_vulnerability_model.git
  - Tag: `v2026.0.0`
  - Commit: `5974372ac3f4a99f25d0649eb030fbe596f23b36`

Both repositories were clean immediately after cloning. The detached HEAD state is intentional because the directories are pinned to release tags.

## Pilot-country availability

The public exposure repository contains Indonesia and Nepal national, Adm1 and taxonomy summary CSVs and associated figures. It does **not** contain the spatially disaggregated approximately 1 km exposure archives or the country vulnerability-mapping CSVs. Both ship with the licensed download obtained through the [GEM licence request page](https://www.globalquakemodel.org/license-request/global-exposure-model).

The vulnerability repository contains structural, non-structural, contents and fatalities XMLs for both Indonesia and Nepal.

## Outstanding licensed assets

Commercial use has been cleared, but the files below still have to be requested and downloaded; clearance is not delivery. CASS reads them the moment they are placed beside the summaries.

| File | Where it goes | What it changes |
| --- | --- | --- |
| `Vulnerability_mapping_IDN.csv` | `global_exposure_model/Southeast_Asia/Indonesia/` | Replaces the reconstructed macro-class grouping in `cass_converter.enrichment.macro_class` with GEM's own exposure-taxonomy to vulnerability-function mapping. Each vulnerability function then receives exactly the replacement cost that maps to it, instead of an equal share of its macro class. Read it with `read_vulnerability_mapping` and apply it with `apply_vulnerability_mapping`; `mapping_coverage` reports how much of the country's value it places. |
| `Vulnerability_mapping_NPL.csv` | `global_exposure_model/South_Asia/Nepal/` | As above. It matters more for Nepal, where 45 residential taxonomies fall into four macro classes and the equal split inside each is at its crudest. |
| Spatially disaggregated exposure (~1 km, `csv.gz`) | either country folder | Not yet read by CASS. It would allow the stock prior to be conditioned on administrative area rather than on the country as a whole, which is the next thing that would narrow a mixture. |

Until they arrive the build runs on the reconstructed grouping and says so: every `StockPrior` carries `uses_exact_weights`, and it is `false`.

## SHA-256 — pilot-country machine-readable files

```text
global_exposure_model\South_Asia\Nepal\summaries\Exposure_Summary_Adm0.csv  7DC0E7062682694123F04B20B2B0183557ABE9E8FB1DE5C279DE7B55384BA4F6
global_exposure_model\South_Asia\Nepal\summaries\Exposure_Summary_Adm1.csv  47C5B679678D02BC28565C8D0D205AF08AA1856DD74FC538E9800D0083E361C9
global_exposure_model\South_Asia\Nepal\summaries\Exposure_Summary_Taxonomy.csv  6F1F7F5157E6551AD35EC92FAD3D2FCCAE72F2F5C3212291ED975EF54B2812C2
global_exposure_model\Southeast_Asia\Indonesia\summaries\Exposure_Summary_Adm0.csv  5AFDF96C3165018BE105E84535B1FB44C8A903D0501CA21B1886C3F5B28B1103
global_exposure_model\Southeast_Asia\Indonesia\summaries\Exposure_Summary_Adm1.csv  E79865BC555AF46AEE42437E12AE29BE30FEC3D8C7626CE9A353A95EA9BB5F59
global_exposure_model\Southeast_Asia\Indonesia\summaries\Exposure_Summary_Taxonomy.csv  D992696F5791CACE28F478CCEBD874435266230C0CB5F874281D8D699B6EACFD
global_vulnerability_model\South_Asia\Nepal\vulnerability_contents.xml  7948349EE09EA960419CB59DEF4CC6CB9D094B8E7AA4CCDAA0D91C9058862285
global_vulnerability_model\South_Asia\Nepal\vulnerability_fatalities.xml  DEB9471503B76EF237DBC6FBB9F7241F045948E0848696D456CBF02280C352C5
global_vulnerability_model\South_Asia\Nepal\vulnerability_nonstructural.xml  07D9916E3C417F34205B12C496DA40F0F1AB3C607C4AC22A9EC0CEB7C75719EE
global_vulnerability_model\South_Asia\Nepal\vulnerability_structural.xml  6A44A6230143BE39135A19A91B0F05CAFE0D486E7AC5D6E617205243F39295DF
global_vulnerability_model\Southeast_Asia\Indonesia\vulnerability_contents.xml  47E4A5C8081DA3E5A83326EA3D59160E89A81EC04B42BE1C7AA9FE0EFA471E39
global_vulnerability_model\Southeast_Asia\Indonesia\vulnerability_fatalities.xml  4F4B7AD1AFB521A93B94536F51BBFB2103F253251B27FDA4612E17043318257E
global_vulnerability_model\Southeast_Asia\Indonesia\vulnerability_nonstructural.xml  5562E205B382B9DFA14FC58A735EEF8280346D18824E33547D0190640F2ABF6B
global_vulnerability_model\Southeast_Asia\Indonesia\vulnerability_structural.xml  00D68AFBD382F15B7A61918F1FFB0B0577DFDFA41D5577275F3D6DA0DDB14410
```
