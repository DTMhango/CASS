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
 * Every number is typed as text and sent as text. These are decimals somebody
 * compares between versions, and a float would quietly change them.
 */

import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { useBuildGrid, useGridEstimate, useGrids } from "@/api/hooks";
import type {
  GridArea,
  GridEstimate,
  GridRefinement,
  GridSpecificationInput,
} from "@/api/types";
import {
  Button,
  Card,
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

export function GridBuilder() {
  const grids = useGrids();
  const build = useBuildGrid();
  const [specification, setSpecification] = useState<GridSpecificationInput>(EMPTY);
  const [questions, setQuestions] = useState("");

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
      description="A fixed, versioned grid for one country, generated from what its specification says. Tiles state what is modelled, so everything outside them is outside by construction rather than by a filter nobody can inspect."
    >
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
          . It is a draft: nothing is approved by building it.
        </Notice>
      ) : null}

      <div className="grid-builder__row">
        <Field label="Country" htmlFor="grid-country" required hint="ISO alpha-2, such as PH.">
          <TextInput
            id="grid-country"
            value={specification.country_code}
            onChange={(event) => set("country_code", event.target.value.toUpperCase())}
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

      <div className="grid-builder__row">
        <Field
          label="Base resolution (degrees)"
          htmlFor="grid-resolution"
          required
          hint="Cost is quadratic: halving it quadruples the cells."
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

      <Cost estimate={estimate.data} countable={countable} />

      <h3 className="grid-builder__heading">Tiles</h3>
      <p className="muted">
        What is modelled. A domain stated as its tiles spends no calculation on
        open ocean, and nothing outside them is silently dropped.
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
        replaces the base cells beneath it rather than overlapping them.
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
 * What the specification would generate, while it can still be changed.
 *
 * The cost of a resolution is invisible in the box it is typed into: a tenth of
 * a degree over Indonesia is tens of thousands of cells and a hundredth is five
 * million, and the only way to find out used to be to press the button and wait
 * for a refusal. This is the same count the build guards against, so nothing
 * moves underneath a specification this calls buildable.
 *
 * It is a count, not a duration. What a finer grid actually costs is paid later
 * -- in the hazard calculation and the loss run -- and this platform has no
 * honest basis yet for putting a time on that.
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
        A base resolution and one complete tile are enough to count what this would
        generate.
      </p>
    );
  }

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
          ? ". Refined cells replace the base cells beneath them, and both are counted here."
          : "."}
        {estimate.incomplete > 0
          ? ` ${estimate.incomplete} area${estimate.incomplete === 1 ? " is" : "s are"} not counted, being still unfinished.`
          : ""}
      </p>

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
    <div className="grid-builder__row">
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
