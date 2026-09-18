# 25. Every country is screened against its outline, and a reviewer can overrule the screen

Status: Accepted
Date: 2026-09-18

## Context

Before a location is put in a cohort, CASS checks that its coordinate lies in the
country its row names. The check exists because the 30 June 2026 extract held
five rows geocoded on the wrong continent, two of them at street precision with
no review flag. A geocoder can be confident and wrong.

Until cohort rules 1.2.0 the check was two hand-typed rectangles, one around
Indonesia and one around Nepal, written for the pilot. A row in any other country
got the answer "no check exists", and the rules treated that as unclassified. So
a correctly geocoded book in Bangladesh, Qatar, Turkey or Bhutan could be
imported but never promoted, and nothing the user could do would change that:
the fix was in the code.

Two faults made it worse:
- **Review decisions were ignored.** The review queue let a person move a row
  into a cohort, with a rationale, and the review screen counted it there.
  Promotion ran the rules again on the raw rows and ignored the decision. So
  there was no way round the screen from inside the application either.
- **Re-importing didn't re-read.** Importing the same file again returned the
  existing import, so a rules change never reached a file already imported.

Meanwhile, since ADR 0020, CASS has shipped Natural Earth's 1:10m country
outlines, which grids are clipped to. The outline for every country was already
on the platform; the screen didn't use it.

## Decision

- **The screen uses the country outlines, for every country.**
  - **What passes:** a coordinate passes when its cell (0.0125°, about 1.4 km)
    touches the country's land widened by 5 km. That is the rule a grid clipped to
    land keeps its cells by, so a location that passes is one that country's grid
    can hold.
  - **Territories drawn inside another country:** every officially assigned ISO
    3166-1 code has an outline. Natural Earth draws eleven of them inside another
    country's outline, such as Réunion inside France and Svalbard inside Norway.
    Those codes are screened against that outline.
  - **The Maldives:** the Maldives' outline omits most of its islands, so a
    Maldivian coordinate is checked against the extent of the islands drawn. This
    is the same reason its seed grid does not clip.
- **The rules take the screen as an argument.** `cass_extract` defines what a
  screen must answer; the outlines live in `cass_keys.land`. Every call to assign
  cohorts must name a screen, so no path can skip the check.
- **Nothing is blocked for want of a check.** The only unclassified outcomes are
  ones the user can resolve, and each says how:
  - **A coordinate outside its country:** correct the coordinate or the code, or
    confirm the row in review if it is right.
  - **A code that isn't an ISO code:** correct the Country column. The message
    suggests `GB` for `UK` and `GR` for `EL`.
  - **No coordinates, or an unrecognised precision:** as before.
- **The import reports what the screen found.** There is one finding per country
  code and cause, with the count and the rows. The findings are grouped rather
  than listed per row, as the intake groups its own, so a sheet whose latitudes
  have all lost their sign produces one finding per country, not one per row. The
  import review page now lists every workbook finding. Before this change it
  listed none.
- **Promotion selects by the cohort as it stands after review.** That is the
  rules' assignment at import, then any decision a person recorded with a
  rationale. The rules are not run again at promotion, so the rule version the
  lineage names is the one that applied. The lineage counts the locations a
  person placed.
- **The same file under newer rules is read again.** An import is unique per
  project, file checksum, parser version and cohort rule version, where it used
  to be unique per project and checksum. Importing an unchanged file under the
  same rules still returns the existing import. Under new rules the file is read
  into a new import, and the old one stays as the record of the earlier read.
  The review page says when an import was read under earlier rules.

## Evidence

- **KRE's six country templates (1,086 policies):** every coordinate is inside its
  country's outline or within 5 km of it. Bangladesh has 136 inside and 6 near
  the coast, Qatar 74 and 10, Turkey 27 and 2, Bhutan 34 inside, Indonesia 533
  and 17, and Nepal 245 and 2.
- **The 30 June extract (224 locations):** the outline flags exactly the five rows
  the rectangles flagged, so the cohorts stay 61, 44, 114 and 5.
- **Places the rectangles got wrong:** Lucknow, 90 km into India, sat inside
  Nepal's rectangle and is outside Nepal's outline. Beirut, Singapore and Dili
  are outside Indonesia. Small Indonesian islands (Pulau Pramuka, Karimunjawa,
  the Gili islands, Enggano) are inside.
- **Offshore sites:** Halul Island's terminal, 90 km off Qatar, is outside. That
  is the case the review decision is for.
- **Speed:** 20,000 points spread across Indonesia take about two seconds.

## Alternatives considered

- **Remove the check and rely on the user.** Rejected. The five rows on the
  wrong continent show the error is real, and two of them would have entered the
  automated cohort. A check that can always be overruled by a person with a
  reason keeps the protection without the dead end.
- **Keep rectangles and add one per country.** Rejected. Hand-typed boxes need a
  code change for every new country, the problem this record removes, and a box
  generous enough for a coast lets in neighbours: Nepal's admitted northern
  India.
- **Test the exact polygon, with a distance to the nearest edge.** Rejected as
  the default. It is slower per point and would disagree with the grids, which
  measure land by cells.

## Consequences

- **Any country CASS can build a grid for can now be imported and promoted.**
  Running it still needs a model version with hazard for that country.
- **The outline is stricter than the rectangles were near borders.** A coordinate
  a few kilometres over a border now fails. That is the purpose of the check, and
  a person who knows better can confirm the row.
- **Land the 1:10m map does not draw can be flagged.** Offshore platforms and
  small islands far from any drawn coast are examples. The review decision is
  the way through, and the Problems page says so.
- **Cohort rules are 1.2.0.** Imports read under 1.1.0 keep their cohorts until
  the file is imported again.

## Revisit if

- **Offshore or remote-island exposure becomes common enough that confirming
  rows one at a time is a burden.** A per-portfolio allowance, or a finer
  outline for the countries concerned, would then earn its place.
