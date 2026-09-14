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

The public exposure repository contains Indonesia and Nepal national, Adm1 and taxonomy summary CSVs and associated figures. It does **not** contain the spatially disaggregated approximately 1 km exposure archives, which ship with the licensed download obtained through the [GEM licence request page](https://www.globalquakemodel.org/license-request/global-exposure-model). The taxonomy mapping, first thought to ship only with that download, is in the public repository; see below.

CASS holds both models under GEM Foundation's written permission for the data and models it makes publicly available ([ADR 15](../../../platform/docs/adr/0015-research-tool-and-gem-permission.md)), which superseded the internal-use basis of [ADR 7](../../../platform/docs/adr/0007-internal-use-licence-basis.md). Anything redistributed still credits the authors and GEM and carries the CC BY-NC-SA 4.0 share-alike terms.

The vulnerability repository contains structural, non-structural, contents and fatalities XMLs for both Indonesia and Nepal.

## Taxonomy mapping

GEM's exposure-taxonomy to vulnerability-function mapping is published in the
public repository, at `global_exposure_model/World/summaries/Vulnerability_mapping_country.csv`
rather than in the country folders. It is keyed by ISO alpha-3 (`IDN`, `NPL`),
carries a weight per target, and covers 100% of both pilot countries'
replacement cost with taxonomy strings matching the exposure summaries exactly.

CASS reads it by default. `cass_converter.pilot_enrichment.load` applies it, and
`StockPrior.uses_exact_weights` records that it did.

It matters. Without it CASS falls back to reconstructing GEM's macro classes
from the vulnerability taxonomy strings, which divides a class's value equally
among its functions and can mis-bin one entirely: the fallback gave Nepal's
`CR/LFINF/CDM+ERL/H:4/COM` a nominal 0.02% of commercial concrete value because
it reads as ductile and the summary reports no ductile commercial stock, where
GEM's mapping gives it 28.78%.

## Still outstanding

| Asset | Route | What it would change |
| --- | --- | --- |
| Spatially disaggregated exposure (~1 km, `csv.gz`) | [GEM licence request](https://www.globalquakemodel.org/license-request/global-exposure-model) | Not read by CASS. It would let the stock prior be conditioned on administrative area rather than on the country as a whole, which is the next thing that would narrow a mixture -- Jakarta's commercial stock is not Indonesia's. |

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
