/**
 * Building a country's area-peril grid from a specification (section 6).
 *
 * The grid is fixed, versioned and independent of any portfolio, and until now
 * it arrived either as one of two prototypes compiled into the platform or as a
 * cell file somebody generated elsewhere. Neither is something a modeller can
 * do for the country in front of them, and a cell file arrives without the
 * reasoning that produced it.
 *
 * So this asks for the specification instead: the tiles that say what is
 * modelled, the resolution, the areas refined and -- beside each -- why. The
 * cells are generated from that, so the artefact a reviewer argues with is the
 * one the platform keeps, and the geometry cannot drift away from it.
 *
 * A specification can start from a seed CASS ships for a country, and can keep
 * only the cells that touch the country's land and lie near anywhere somebody
 * lives or something is built. Every cell dropped is a hazard site no
 * calculation spends time on.
 *
 * Every number is typed as text and sent as text. These are decimals somebody
 * compares between versions, and a float would quietly change them.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import {
  useBuildGrid,
  useGridCountries,
  useGridEstimate,
  useGridSeeds,
  useGrids,
  useLoadGridSeed,
} from "@/api/hooks";
import type {
  GridArea,
  GridDomain,
  GridEstimate,
  GridRefinement,
  GridSpecificationInput,
} from "@/api/types";
import {
  Button,
  Card,
  Combobox,
  EmptyState,
  Field,
  Notice,
  TextArea,
  TextInput,
} from "@/components/primitives";
import { formatCount } from "@/lib/format";

import "./GridBuilder.css";

const EMPTY_TILE: GridArea = {
  name: "",
  reason: "",
  min_latitude: "",
  max_latitude: "",
  min_longitude: "",
  max_longitude: "",
};

const EMPTY_REFINEMENT: GridRefinement = { ...EMPTY_TILE, resolution_deg: "" };

/**
 * Both on, at 5 km, for a specification started from nothing. Measured against
 * KRE's geocoded book: a 5 km coast buffer and a 5 km settlement buffer kept
 * every location that was really in its country.
 */
const DEFAULT_DOMAIN: GridDomain = {
  clip_to_land: true,
  coast_buffer_km: "5",
  skip_unsettled: true,
  settlement_buffer_km: "5",
};

const EMPTY: GridSpecificationInput = {
  country_code: "",
  version: "",
  label: "",
  base_resolution_deg: "",
  mapping_tolerance_km: "0",
  tiles: [{ ...EMPTY_TILE }],
  refinements: [],
  open_questions: [],
  notes: "",
  domain: DEFAULT_DOMAIN,
};

/** The four fields an area needs before anything can be counted inside it. */
const CORNERS = ["min_latitude", "max_latitude", "min_longitude", "max_longitude"] as const;

/**
 * A value a moment after somebody stops changing it.
 *
 * The count is worth a request; the count of every intermediate number typed on
 * the way to "0.05" is not.
 */
function useSettled<T>(value: T, delay = 400): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** Megabytes as a person reads them. */
function megabytes(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} GB` : `${formatCount(value)} MB`;
}

export function GridBuilder() {
  const grids = useGrids();
  const build = useBuildGrid();
  const countries = useGridCountries();
  const [specification, setSpecification] = useState<GridSpecificationInput>(EMPTY);
  const [questions, setQuestions] = useState("");
  const [loadedSeed, setLoadedSeed] = useState<string | null>(null);

  // What the specification on the screen would generate, kept in step with it.
  const settled = useSettled(specification);
  const countable =
    settled.base_resolution_deg.trim() !== "" &&
    settled.tiles.some((tile) => CORNERS.every((corner) => tile[corner].trim() !== ""));
  const estimate = useGridEstimate(settled, countable);

  // Only where the count is in and says so: an estimate that failed to arrive
  // must not stand between a modeller and a build the server would accept.
  const overLimit = estimate.data?.estimated === true && !estimate.data.within_limit;

  const refusal = build.error as ApiError | null;
  const built = build.data;

  function set<K extends keyof GridSpecificationInput>(
    key: K,
    value: GridSpecificationInput[K],
  ) {
    setSpecification((current) => ({ ...current, [key]: value }));
  }

  function setDomain<K extends keyof GridDomain>(key: K, value: GridDomain[K]) {
    setSpecification((current) => ({
      ...current,
      domain: { ...current.domain, [key]: value },
    }));
  }

  function setTile(index: number, key: keyof GridArea, value: string) {
    setSpecification((current) => {
      const tiles = current.tiles.map((tile, position) =>
        position === index ? { ...tile, [key]: value } : tile,
      );
      return { ...current, tiles };
    });
  }

  function setRefinement(index: number, key: keyof GridRefinement, value: string) {
    setSpecification((current) => {
      const refinements = current.refinements.map((item, position) =>
        position === index ? { ...item, [key]: value } : item,
      );
      return { ...current, refinements };
    });
  }

  const ready =
    specification.country_code.trim() !== "" &&
    specification.version.trim() !== "" &&
    specification.label.trim() !== "" &&
    specification.base_resolution_deg.trim() !== "";

  return (
    <Card
      title="Build a grid"
      description="A fixed, versioned grid for one country, generated from what its specification says. Tiles name the regions that are modelled, and the grid can keep only the cells that touch the country's land and lie near where people live or build."
    >
      <SeedPicker
        onLoad={(code, seed) => {
          setSpecification({ ...EMPTY, ...seed, domain: { ...DEFAULT_DOMAIN, ...seed.domain } });
          setQuestions((seed.open_questions ?? []).join("\n"));
          setLoadedSeed(code);
          build.reset();
        }}
        loaded={loadedSeed}
      />

      {refusal ? (
        <Notice tone="error" title="The grid was not built">
          {refusal.message}
        </Notice>
      ) : null}

      {built ? (
        <Notice tone="ok" title={`${built.grid.reference} built`}>
          {formatCount(built.summary.cells)} cells,{" "}
          {formatCount(built.summary.cells_at_base_resolution)} of them at the base
          resolution
          {Object.entries(built.summary.cells_by_refinement).map(
            ([name, count]) => `, ${formatCount(count)} in ${name}`,
          )}
          .
          {built.summary.removed_as_sea
            ? ` ${formatCount(built.summary.removed_as_sea)} cells over the sea were left out.`
            : ""}
          {built.summary.removed_as_unsettled
            ? ` ${formatCount(built.summary.removed_as_unsettled)} cells of empty land were left out.`
            : ""}{" "}
          It is a draft: nothing is approved by building it.
        </Notice>
      ) : null}

      <div className="grid-builder__row form-row">
        <Field
          label="Country"
          htmlFor="grid-country"
          required
          hint="Its two-letter code. The land clip reads this country's outline."
        >
          <Combobox
            id="grid-country"
            value={specification.country_code}
            onChange={(value) => set("country_code", value)}
            placeholder="Type a name or a code"
            options={(countries.data ?? []).map((country) => ({
              value: country.code,
              label: `${country.name} (${country.code})`,
              keywords: [country.code],
              detail: country.seeded ? "A seed grid ships for this country" : undefined,
            }))}
          />
        </Field>
        <Field label="Version" htmlFor="grid-version" required hint="Identifiers are stable within a version, so a change of geometry is a new one.">
          <TextInput
            id="grid-version"
            value={specification.version}
            onChange={(event) => set("version", event.target.value)}
          />
        </Field>
        <Field label="Label" htmlFor="grid-label" required>
          <TextInput
            id="grid-label"
            value={specification.label}
            onChange={(event) => set("label", event.target.value)}
          />
        </Field>
      </div>

      <div className="grid-builder__row form-row">
        <Field
          label="Base resolution (degrees)"
          htmlFor="grid-resolution"
          required
          hint="The size of a cell. 0.1 is about 11 km, 0.025 about 2.8 km, 0.0125 about 1.4 km. Halving it quadruples the cells."
        >
          <TextInput
            id="grid-resolution"
            value={specification.base_resolution_deg}
            onChange={(event) => set("base_resolution_deg", event.target.value)}
          />
        </Field>
        <Field
          label="Mapping tolerance (km)"
          htmlFor="grid-tolerance"
          hint="Beyond this a near miss is reported rather than snapped."
        >
          <TextInput
            id="grid-tolerance"
            value={specification.mapping_tolerance_km}
            onChange={(event) => set("mapping_tolerance_km", event.target.value)}
          />
        </Field>
      </div>

      <h3 className="grid-builder__heading">What the grid keeps</h3>
      <p className="muted">
        Tiles are rectangles and a country is not, so a rectangle drawn around
        islands holds more sea than land. These leave out the cells nothing
        insured can be in. Each cell left out is one less place every hazard
        calculation on this grid has to compute.
      </p>
      <div className="grid-builder__row form-row">
        <label className="grid-builder__check">
          <input
            type="checkbox"
            checked={specification.domain.clip_to_land}
            onChange={(event) => setDomain("clip_to_land", event.target.checked)}
          />
          Keep only cells that touch the country&rsquo;s land
        </label>
        <Field
          label="Coast buffer (km)"
          htmlFor="grid-coast-buffer"
          hint="Cells this close to the coast are kept too. The outline is accurate to about 5 km."
        >
          <TextInput
            id="grid-coast-buffer"
            value={specification.domain.coast_buffer_km}
            disabled={!specification.domain.clip_to_land}
            onChange={(event) => setDomain("coast_buffer_km", event.target.value)}
          />
        </Field>
      </div>
      <div className="grid-builder__row form-row">
        <label className="grid-builder__check">
          <input
            type="checkbox"
            checked={specification.domain.skip_unsettled}
            onChange={(event) => setDomain("skip_unsettled", event.target.checked)}
          />
          Skip land with no buildings or people nearby
        </label>
        <Field
          label="Settlement buffer (km)"
          htmlFor="grid-settlement-buffer"
          hint="Cells this close to any building or resident are kept. At 5 km none of KRE's geocoded locations were left out."
        >
          <TextInput
            id="grid-settlement-buffer"
            value={specification.domain.settlement_buffer_km}
            disabled={!specification.domain.skip_unsettled}
            onChange={(event) => setDomain("settlement_buffer_km", event.target.value)}
          />
        </Field>
      </div>

      <Cost estimate={estimate.data} countable={countable} />

      <h3 className="grid-builder__heading">Tiles</h3>
      <p className="muted">
        The regions that are modelled, each with the reason it is one. Nothing
        outside every tile is in the grid, so a location there is reported rather
        than silently dropped.
      </p>
      {specification.tiles.map((tile, index) => (
        <AreaFields
          key={`tile-${index}`}
          prefix={`Tile ${index + 1}`}
          id={`tile-${index}`}
          area={tile}
          onChange={(key, value) => setTile(index, key, value)}
        />
      ))}
      <Button
        variant="secondary"
        onClick={() => set("tiles", [...specification.tiles, { ...EMPTY_TILE }])}
      >
        Add a tile
      </Button>

      <h3 className="grid-builder__heading">Refinements</h3>
      <p className="muted">
        Areas modelled more finely, each with the reason it is one. A refinement
        replaces the base cells beneath it rather than overlapping them, so its
        edges must be multiples of the base resolution and its resolution must
        divide the base exactly: 0.0125 inside a 0.025 base, for example.
      </p>
      {specification.refinements.map((refinement, index) => (
        <div key={`refinement-${index}`}>
          <AreaFields
            prefix={`Refinement ${index + 1}`}
            id={`refinement-${index}`}
            area={refinement}
            onChange={(key, value) => setRefinement(index, key, value)}
          />
          <Field
            label={`Refinement ${index + 1} resolution (degrees)`}
            htmlFor={`refinement-${index}-resolution`}
            required
          >
            <TextInput
              id={`refinement-${index}-resolution`}
              value={refinement.resolution_deg}
              onChange={(event) =>
                setRefinement(index, "resolution_deg", event.target.value)
              }
            />
          </Field>
        </div>
      ))}
      <Button
        variant="secondary"
        onClick={() =>
          set("refinements", [...specification.refinements, { ...EMPTY_REFINEMENT }])
        }
      >
        Add a refinement
      </Button>

      <h3 className="grid-builder__heading">What it leaves open</h3>
      <Field
        label="Open questions"
        htmlFor="grid-questions"
        hint="One per line. They are carried with the grid, so silence is never read as resolution."
      >
        <TextArea
          id="grid-questions"
          rows={3}
          value={questions}
          onChange={(event) => setQuestions(event.target.value)}
        />
      </Field>
      <Field label="Notes" htmlFor="grid-notes">
        <TextArea
          id="grid-notes"
          rows={2}
          value={specification.notes}
          onChange={(event) => set("notes", event.target.value)}
        />
      </Field>

      <div className="grid-builder__actions">
        <Button
          variant="primary"
          onClick={() =>
            build.mutate({
              ...specification,
              open_questions: questions
                .split("\n")
                .map((line) => line.trim())
                .filter(Boolean),
            })
          }
          disabled={!ready || build.isPending || overLimit}
          title={
            overLimit
              ? "This specification is over the cell limit, and the build would refuse it."
              : undefined
          }
        >
          {build.isPending ? "Building" : "Build the grid"}
        </Button>
      </div>

      <h3 className="grid-builder__heading">Grids in the registry</h3>
      {grids.data && grids.data.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Grid</th>
              <th scope="col">Cells</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {grids.data.map((grid) => (
              <tr key={grid.id}>
                <th scope="row">
                  <span className="mono">{grid.reference}</span>
                  <span className="muted"> {grid.label}</span>
                </th>
                <td>{formatCount(grid.cell_count)}</td>
                <td>{grid.publication_state}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No grid is registered yet" />
      )}
    </Card>
  );
}

/**
 * Where a specification can start: one of the seeds CASS ships.
 *
 * Loading a seed fills the form and nothing else. What is built from it is built
 * by whoever presses the button, after changing whatever they choose to.
 */
function SeedPicker({
  onLoad,
  loaded,
}: {
  onLoad: (code: string, seed: GridSpecificationInput) => void;
  loaded: string | null;
}) {
  const seeds = useGridSeeds();
  const load = useLoadGridSeed();
  const [chosen, setChosen] = useState("");
  const failure = load.error as ApiError | null;

  if (!seeds.data?.length) return null;

  return (
    <div className="grid-builder__seed">
      <h3 className="grid-builder__heading">Start from a country</h3>
      <p className="muted">
        CASS ships a grid specification for {seeds.data.length} countries: tiles for
        the whole country, its largest cities refined where the budget needs it,
        and the sea and empty land left out. Loading one replaces what is in the
        form below; nothing is built until you build it.
      </p>
      <div className="grid-builder__row form-row">
        <Field label="Seed" htmlFor="grid-seed">
          <Combobox
            id="grid-seed"
            value={chosen}
            onChange={setChosen}
            placeholder="Choose a country"
            options={seeds.data.map((seed) => ({
              value: seed.country_code,
              label: seed.label,
              keywords: [seed.country_code],
              detail: `${formatCount(seed.cells)} cells at ${seed.base_resolution_deg}°${
                seed.refinements ? `, ${seed.refinements} cities refined` : ""
              }`,
            }))}
          />
        </Field>
        <div className="grid-builder__seed-action">
          <Button
            variant="secondary"
            disabled={!chosen}
            busy={load.isPending}
            onClick={() =>
              load.mutate(chosen, {
                onSuccess: (seed) => onLoad(chosen, seed.specification),
              })
            }
          >
            Load into the form
          </Button>
        </div>
      </div>
      {failure ? (
        <Notice tone="error" title="The seed was not loaded">
          {failure.message}
        </Notice>
      ) : null}
      {loaded ? (
        <Notice tone="ok" title="Seed loaded">
          The {loaded} seed is in the form. Change anything you want before building
          it; its reasons and open questions came with it.
        </Notice>
      ) : null}
    </div>
  );
}

/**
 * What the specification would generate, while it can still be changed.
 *
 * The cost of a resolution is invisible in the box it is typed into: a tenth of
 * a degree over Indonesia is tens of thousands of cells and a hundredth is five
 * million, and the only way to find out used to be to press the button and wait
 * for a refusal. This is the same count the build guards against, so nothing
 * moves underneath a specification this calls buildable -- exact once the
 * specification is whole, with the sea and empty land already taken out.
 *
 * Beside the count is what the grid's hazard would store, per thousand simulated
 * years, as a ceiling measured on the strongest shaking CASS has computed. What
 * a calculation takes in time is not shown: the measurements so far do not give
 * an honest basis for it at national scale.
 */
function Cost({
  estimate,
  countable,
}: {
  estimate: GridEstimate | undefined;
  countable: boolean;
}) {
  if (!countable || !estimate?.estimated) {
    return (
      <p className="grid-builder__cost muted">
        A country, a base resolution and one complete tile are enough to count what
        this would generate.
      </p>
    );
  }

  const removed = estimate.removed_as_sea + estimate.removed_as_unsettled;

  return (
    <div className="grid-builder__cost">
      <p className="grid-builder__count">
        {formatCount(estimate.cells)} cells
        {estimate.is_upper_bound ? " at most" : ""}
      </p>
      <p className="muted">
        {formatCount(estimate.cells_from_tiles)} at the base resolution
        {estimate.cells_by_refinement.map(
          (item) =>
            `, ${formatCount(item.cells)} in ${item.name} at ${item.resolution_deg}°`,
        )}
        {estimate.is_upper_bound
          ? ". Refined cells replace the base cells beneath them, and both are counted here, as is anything the domain would leave out."
          : "."}
        {estimate.incomplete > 0
          ? ` ${estimate.incomplete} area${estimate.incomplete === 1 ? " is" : "s are"} not counted, being still unfinished.`
          : ""}
      </p>
      {estimate.exact && removed > 0 ? (
        <p className="muted">
          Of the {formatCount(estimate.candidates)} cells the tiles span,{" "}
          {estimate.removed_as_sea > 0
            ? `${formatCount(estimate.removed_as_sea)} over the sea`
            : ""}
          {estimate.removed_as_sea > 0 && estimate.removed_as_unsettled > 0 ? " and " : ""}
          {estimate.removed_as_unsettled > 0
            ? `${formatCount(estimate.removed_as_unsettled)} on empty land`
            : ""}{" "}
          are left out.
        </p>
      ) : null}
      {estimate.storage ? (
        <p className="muted">
          Its hazard would store at most {megabytes(estimate.storage.hazard_set_mb_per_thousand_years)}{" "}
          for every thousand simulated years, and a model package built on it at most{" "}
          {megabytes(estimate.storage.package_mb_per_thousand_years)} more while it is
          in use.
        </p>
      ) : null}

      {estimate.uncovered_land && estimate.uncovered_land.cells > 0 ? (
        <Notice tone="warning" title="Some of the country's land is in no tile">
          {formatCount(estimate.uncovered_land.cells)} cells of land at the base
          resolution lie outside every tile, and a location there would be reported
          as outside the grid. The largest pieces are near{" "}
          {estimate.uncovered_land.examples
            .map((item) => `${item.latitude}, ${item.longitude}`)
            .join("; ")}
          . Widen a tile or add one to take them in.
        </Notice>
      ) : null}

      {estimate.within_limit ? null : (
        <Notice tone="warning" title="More cells than this installation builds">
          The limit is {formatCount(estimate.limit)}. Resolution is quadratic: halving
          it quadruples the count. Coarsen the base resolution, narrow the tiles, or
          refine a smaller area.
        </Notice>
      )}

      {estimate.problems.length > 0 ? (
        <Notice tone="warning" title="The build would refuse this">
          <ul className="grid-builder__problems">
            {estimate.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}

function AreaFields({
  prefix,
  id,
  area,
  onChange,
}: {
  prefix: string;
  id: string;
  area: GridArea;
  onChange: (key: keyof GridArea, value: string) => void;
}) {
  const fields: { key: keyof GridArea; label: string }[] = [
    { key: "name", label: "name" },
    { key: "reason", label: "reason" },
    { key: "min_latitude", label: "minimum latitude" },
    { key: "max_latitude", label: "maximum latitude" },
    { key: "min_longitude", label: "minimum longitude" },
    { key: "max_longitude", label: "maximum longitude" },
  ];
  return (
    <div className="grid-builder__row form-row">
      {fields.map((field) => (
        <Field
          key={field.key}
          label={`${prefix} ${field.label}`}
          htmlFor={`${id}-${field.key}`}
        >
          <TextInput
            id={`${id}-${field.key}`}
            value={area[field.key]}
            onChange={(event) => onChange(field.key, event.target.value)}
          />
        </Field>
      ))}
    </div>
  );
}
