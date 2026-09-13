/**
 * The import-results and review screen: work package 2.
 *
 * The brief asks this screen to show what came in, what is missing, how value
 * was allocated, what a review has changed and which mode the result may be
 * used in. It has one job underneath all of that, and it is worth stating:
 * make the cost of a gap visible before somebody runs a model over it.
 *
 * So nothing here reports a count on its own. Every gap carries the value
 * behind it and the consequence in the words the failure will actually appear
 * in -- "fail_v", "reaches candidates across several intensity measures" --
 * because "occupancy missing on 40 rows" reads like tidying and "USD 180m
 * reaches no vulnerability function" reads like the same fact told honestly.
 *
 * The review itself follows the brief's rule exactly. A decision needs a
 * rationale, it is recorded rather than applied to the source, and the queue
 * is ordered by value so an afternoon of review is spent where the money is.
 *
 * Storeys sit beside the cohort decision on purpose. They are the cheapest
 * lever the platform has: a risk with no storey count reaches vulnerability
 * candidates at four intensity measures and cannot become one Oasis function,
 * so it is refused until a scientific decision nobody has made yet. The same
 * risk at a stated height resolves to one. A reviewer already opening the row
 * to check where it is can answer that in the same pass.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useAcceptImport,
  useAllocationScenarios,
  useAssumptionCatalogue,
  useImportResults,
  useModelCatalogue,
  usePortfolioImports,
  usePromoteImport,
  useRecordDecision,
  useReviewQueue,
} from "@/api/hooks";
import type {
  AllocationScenario,
  BusinessMateriality,
  ImportResults,
  PortfolioImport,
  QueuedLocation,
  UUID,
} from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Disclosure,
  EmptyState,
  Field,
  MetricTile,
  Notice,
  PageHeader,
  Select,
  Spinner,
  TextInput,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatCount, formatMoney } from "@/lib/format";

import "./ImportReview.css";

/** How the four use modes are ordered on the page: least committed first. */
const MODE_ORDER = ["geometry", "technical_test", "research", "decision_use"];

const MODE_LABELS: Record<string, string> = {
  geometry: "Geometry only",
  technical_test: "Technical test",
  research: "Research",
  decision_use: "Decision use",
};

/** Plain names for the fields a risk can be missing. */
const FIELD_LABELS: Record<string, string> = {
  coordinates: "Coordinates",
  country: "Country",
  value: "Insured value",
  occupancy: "Occupancy",
  construction: "Construction",
  storeys: "Storeys",
};

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function ImportReview({ embedded = false }: { embedded?: boolean } = {}) {
  const { projectId } = useWorkingContext();
  const imports = usePortfolioImports(projectId);
  const [selected, setSelected] = useState<UUID | undefined>();

  const batchId = selected ?? imports.data?.[0]?.id;
  const results = useImportResults(batchId);
  const queue = useReviewQueue(batchId);

  if (imports.isLoading) return <Spinner label="Loading imports" />;

  if (!imports.data?.length) {
    return (
      <>
        {embedded ? null : <PageHeader
          title="Import review"
          description="Eligibility, missing model inputs and the review backlog"
        />}
        <EmptyState
          title="No portfolio has been imported yet"
          description="Import a workbook in the exposure workspace, and this screen will show what came in and what it is missing."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Import review"
        description="Eligibility, missing model inputs and the review backlog"
        actions={
          <Field label="Import" htmlFor="import-select">
            <Select
              id="import-select"
              value={batchId ?? ""}
              onChange={(event) => setSelected(event.target.value as UUID)}
            >
              {imports.data.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.source_filename || "Imported spreadsheet"}
                </option>
              ))}
            </Select>
          </Field>
        }
      />

      {results.isLoading ? <Spinner label="Reading the import" /> : null}
      {results.data ? (
        <>
          <Provenance results={results.data} />
          <StoreyLever results={results.data} />
          <MissingInputs results={results.data} />
          <Included results={results.data} />
          <Allocation results={results.data} />
          {batchId ? <AllocationScenarios batchId={batchId} /> : null}
          <UseModes results={results.data} />
          {batchId ? (
            <ReviewQueuePanel
              batchId={batchId}
              locations={queue.data?.locations ?? []}
              outstanding={queue.data?.outstanding ?? 0}
              outstandingValue={queue.data?.outstanding_value ?? 0}
              loading={queue.isLoading}
            />
          ) : null}
          {batchId ? (
            <PromotionPanel
              batchId={batchId}
              batch={imports.data.find((item) => item.id === batchId)}
              results={results.data}
              outstanding={queue.data?.outstanding ?? 0}
            />
          ) : null}
        </>
      ) : null}
    </>
  );
}

/**
 * Does the allocation assumption matter?
 *
 * Not "how does the value divide" -- the allocation engine answers that
 * exactly, and it reconciles. The question here is whether the division
 * changes anything, and it only can where a business\'s sites fall in
 * different area-peril cells. Two warehouses in one cell can be split any way
 * at all and the model cannot tell the difference.
 *
 * So the comparison needs a grid, which is why it needs a model version, and
 * why nothing is shown until one is chosen. Guessing a default would produce a
 * materiality figure against a grid nobody picked, which is worse than no
 * figure: somebody would quote it.
 *
 * Nothing is promoted from here. An analyst tries scenarios freely and then
 * promotes the one they choose, which is the separate audited act below.
 */
function AllocationScenarios({ batchId }: { batchId: UUID }) {
  const models = useModelCatalogue();
  const [modelId, setModelId] = useState("");
  const scenarios = useAllocationScenarios(batchId, modelId || undefined);

  const error = scenarios.error as ApiError | null;
  const data = scenarios.data;
  const materiality = data?.materiality;

  return (
    <Card
      title="Does the allocation assumption matter?"
      description="Where a business reports a total but no site values, the split is an assumption. This says how much of the book that assumption is economically live for."
    >
      <Field
        label="Compare against"
        htmlFor="scenario-model"
        hint="A model version, because the comparison needs its area-peril grid."
      >
        <Select
          id="scenario-model"
          value={modelId}
          onChange={(event) => setModelId(event.target.value)}
        >
          <option value="">Select a model version</option>
          {models.data?.map((item) => (
            <option key={item.id} value={item.id}>
              {item.reference}
            </option>
          ))}
        </Select>
      </Field>

      {!modelId ? (
        <p className="muted">
          Nothing is computed until a model version is chosen. A materiality figure
          against a grid nobody picked is worse than none, because somebody would
          quote it.
        </p>
      ) : null}

      {scenarios.isLoading ? <Spinner label="Allocating under each scenario" /> : null}

      {error ? (
        <Notice tone="error" title="The comparison could not be produced">
          {error.message}
        </Notice>
      ) : null}

      {data && materiality ? (
        <>
          {/* The property that makes this a sensitivity rather than a set of
              different portfolios. If it ever failed, every number below would
              be answering a different question. */}
          {data.total_holds_across_scenarios ? null : (
            <Notice tone="error" title="The scenarios do not carry the same money">
              One of these scenarios changed the total insured value, so they are
              not variants of one portfolio and the movement below cannot be read
              as a sensitivity.
            </Notice>
          )}

          <div className="scenarios__metrics">
            <MetricTile
              label="Value where the assumption is live"
              value={formatMoney(materiality.material_tiv)}
              footnote={`${percent(materiality.material_share)} of the selection`}
            />
            <MetricTile
              label="Businesses affected"
              value={formatCount(materiality.businesses_where_allocation_is_material)}
              footnote={`of ${formatCount(materiality.businesses)} multi-location`}
            />
            <MetricTile label="Grid" value={data.grid} />
          </div>

          {materiality.businesses_where_allocation_is_material === 0 ? (
            <Notice tone="ok" title="The allocation assumption changes nothing here">
              Every multi-location business either reports its own site values or
              has all its sites in one area-peril cell. Choosing a different
              allocation cannot change the loss.
            </Notice>
          ) : null}

          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col" className="numeric">
                  Mapped value
                </th>
                <th scope="col" className="numeric">
                  Cells
                </th>
                <th scope="col">Reconciles</th>
              </tr>
            </thead>
            <tbody>
              {data.scenarios.map((scenario) => (
                <ScenarioRow key={scenario.method} scenario={scenario} />
              ))}
            </tbody>
          </table>

          <p className="muted scenarios__interpretation">{data.interpretation}</p>

          {materiality.detail.length > 0 ? (
            <Disclosure
              summary={`Business by business (${materiality.detail.length})`}
            >
              <ul className="scenarios__businesses">
                {materiality.detail.map((business) => (
                  <MaterialityRow key={business.business_id} business={business} />
                ))}
              </ul>
            </Disclosure>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

function ScenarioRow({ scenario }: { scenario: AllocationScenario }) {
  return (
    <tr>
      <th scope="row">
        {scenario.method.replace(/_/g, " ")}
        {scenario.baseline ? (
          <StatusBadge
            tone="ok"
            size="sm"
            detail="The maximum-ignorance allocation. It introduces no ranking the source does not support."
          >
            baseline
          </StatusBadge>
        ) : null}
      </th>
      <td className="numeric">{formatMoney(scenario.mapped_tiv)}</td>
      <td className="numeric">{formatCount(scenario.area_peril_count)}</td>
      <td>
        <StatusBadge tone={scenario.reconciles ? "ok" : "error"} size="sm">
          {scenario.reconciles ? "yes" : "no"}
        </StatusBadge>
      </td>
    </tr>
  );
}

function MaterialityRow({ business }: { business: BusinessMateriality }) {
  return (
    <li className="scenarios__business">
      <StatusBadge tone={business.material ? "warning" : "idle"} size="sm">
        {business.material ? "live" : "settled"}
      </StatusBadge>
      <span className="mono">{business.business_id}</span>
      <span className="numeric">{formatMoney(business.total_tiv)}</span>
      <span className="muted">{business.reason}</span>
    </li>
  );
}

/**
 * Accept the read, then turn a cohort into an exposure version.
 *
 * Two acts, and they are not the same one. Accepting says a person has looked
 * at the join report and the cohorts -- that what CASS made of the workbook is
 * what the workbook says. Promoting says which part of it to model and under
 * which assumptions, and produces an immutable version that an analysis can
 * run against.
 *
 * The assumptions are offered from the platform\'s own catalogue rather than
 * listed here, so a scenario the converter gains appears without a frontend
 * release. Every one of them is recorded on the version that results: the
 * point of promoting under a stated assumption is that a later reader can see
 * it and promote the same import differently.
 *
 * The outstanding review count is shown rather than enforced. A reviewer may
 * promote with decisions outstanding -- cohort B exists precisely so that
 * incomplete records can be excluded rather than block the book -- but they
 * should know they are doing it.
 */
function PromotionPanel({
  batchId,
  batch,
  results,
  outstanding,
}: {
  batchId: UUID;
  batch?: PortfolioImport;
  results: ImportResults;
  outstanding: number;
}) {
  const catalogue = useAssumptionCatalogue();
  const accept = useAcceptImport();
  const promote = usePromoteImport(batchId);

  const [name, setName] = useState("");
  const [cohort, setCohort] = useState("A");
  const [allocation, setAllocation] = useState("");
  const [split, setSplit] = useState("");
  const [occupancy, setOccupancy] = useState("");
  const [country, setCountry] = useState("");

  // A select shows its first option whether or not state holds it, so the
  // effective value is derived rather than read: otherwise the screen offers a
  // baseline allocation and refuses to act on it.
  const allocationValue =
    allocation ||
    catalogue.data?.allocation_methods?.find((item) => item.baseline)?.value ||
    catalogue.data?.allocation_methods?.[0]?.value ||
    "";

  const accepted = (batch?.state ?? results.batch.state) === "accepted";
  const error = (accept.error ?? promote.error) as ApiError | null;
  const countries = [
    ...new Set((results.included ?? []).map((item) => item.country_code).filter(Boolean)),
  ];
  const cohortCount = results.review.by_cohort?.[cohort] ?? 0;

  return (
    <Card
      title="Promote to a portfolio"
      description="What this import becomes, and under which stated assumptions."
    >
      {error ? (
        <Notice tone="error" title="Refused">
          {error.message}
        </Notice>
      ) : null}

      {outstanding > 0 ? (
        <Notice tone="warning" title={`${formatCount(outstanding)} location(s) still await a decision`}>
          You may promote without clearing the queue. Anything still unresolved stays
          outside cohort A, so it is excluded from the version rather than modelled on
          a guess.
        </Notice>
      ) : null}

      {!accepted ? (
        <>
          <p>
            Record that the join report and the cohort assignment have been reviewed.
            This is the acknowledgement that what CASS made of the workbook is what the
            workbook says.
          </p>
          <Button
            variant="primary"
            busy={accept.isPending}
            onClick={() => accept.mutate(batchId)}
          >
            Accept this import
          </Button>
        </>
      ) : (
        <>
          <div className="promotion__form">
            <Field
              label="Portfolio name"
              htmlFor="promote-name"
              hint="What the exposure version will be called."
            >
              <TextInput
                id="promote-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={results.batch.filename.replace(/\.[^.]+$/, "")}
              />
            </Field>

            <Field
              label="Cohort"
              htmlFor="promote-cohort"
              hint={`${formatCount(cohortCount)} location(s) in this cohort.`}
            >
              <Select
                id="promote-cohort"
                value={cohort}
                onChange={(event) => setCohort(event.target.value)}
              >
                {catalogue.data?.cohorts?.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Country"
              htmlFor="promote-country"
              hint="A model version covers one country, so a business with sites in two is excluded from both rather than split."
            >
              <Select
                id="promote-country"
                value={country}
                onChange={(event) => setCountry(event.target.value)}
              >
                <option value="">Every country in the import</option>
                {countries.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Allocation"
              htmlFor="promote-allocation"
              hint="How a multi-location policy divides where the schedule states a total but no breakdown."
            >
              <Select
                id="promote-allocation"
                value={allocationValue}
                onChange={(event) => setAllocation(event.target.value)}
              >
                {catalogue.data?.allocation_methods?.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                    {item.baseline ? " — baseline" : ""}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Coverage split"
              htmlFor="promote-split"
              hint="Used only where a risk states a total with no component breakdown."
            >
              <Select
                id="promote-split"
                value={split}
                onChange={(event) => setSplit(event.target.value)}
              >
                <option value="">
                  {catalogue.data?.default_coverage_split ?? "Platform default"}
                </option>
                {catalogue.data?.coverage_splits?.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                    {item.approved ? " — approved prior" : ""}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Occupancy"
              htmlFor="promote-occupancy"
              hint="Used only where a risk names none. A reported occupancy is never overwritten."
            >
              <Select
                id="promote-occupancy"
                value={occupancy}
                onChange={(event) => setOccupancy(event.target.value)}
              >
                <option value="">
                  {catalogue.data?.default_occupancy ?? "Platform default"}
                </option>
                {catalogue.data?.occupancy_assumptions?.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                    {item.approved ? " — approved prior" : ""}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <Button
            variant="primary"
            disabled={!name.trim() || !allocationValue}
            busy={promote.isPending}
            onClick={() =>
              promote.mutate({
                name: name.trim(),
                cohort,
                allocation_method: allocationValue,
                coverage_split: split || undefined,
                occupancy: occupancy || undefined,
                country: country || undefined,
              })
            }
            title={
              name.trim() && allocationValue
                ? "Produce an immutable exposure version from this selection."
                : "Name the version and choose an allocation first."
            }
          >
            Promote to an exposure version
          </Button>

          {promote.isSuccess ? (
            <Notice tone="ok" title="Promoted">
              {promote.data.name} v{promote.data.version} was created with{" "}
              {formatCount(promote.data.location_count)} location(s).{" "}
              <Link to="/exposure">
                Review it in the exposure workspace and publish it
              </Link>{" "}
              before an analysis uses it.
            </Notice>
          ) : null}
        </>
      )}
    </Card>
  );
}

/** What was read, and which versions of which rules read it. */
function Provenance({ results }: { results: ImportResults }) {
  const { batch, review } = results;
  return (
    <Card title="What was read">
      <div className="tiles">
        <MetricTile label="Policy rows" value={formatCount(batch.policy_row_count)} />
        <MetricTile label="Risk locations" value={formatCount(batch.risk_row_count)} />
        <MetricTile label="Total insured value" value={formatMoney(results.total_value)} />
        <MetricTile
          label="Awaiting review"
          value={formatCount(review.outstanding)}
          footnote={review.outstanding > 0 ? "a person owes a decision" : ""}
        />
      </div>
      <dl className="provenance">
        <div>
          <dt>Source file</dt>
          <dd>{batch.filename || "—"}</dd>
        </div>
        <div>
        </div>
        <div>
          <dt>Parser</dt>
          <dd className="mono">{batch.parser_version}</dd>
        </div>
        <div>
          <dt>Cohort rules</dt>
          <dd className="mono">{batch.cohort_rule_version}</dd>
        </div>
        <div>
          <dt>Review overlay</dt>
          <dd className="mono">{batch.overlay_version}</dd>
        </div>
      </dl>
      <p className="muted">
        A decision recorded on this screen never edits the source. It is stored
        beside it and applied when a version is promoted, so this import keeps
        saying what the workbook said.
      </p>
    </Card>
  );
}

/**
 * The storey gap, given its own panel because it is the largest single lever
 * on how much of the book can be modelled at all.
 */
function StoreyLever({ results }: { results: ImportResults }) {
  const { storeys } = results.review;
  const complete = storeys.unstated === 0;
  return (
    <Card title="Height, and what not knowing it costs">
      <div className="tiles">
        <MetricTile
          label="Stated in the schedule"
          value={formatCount(storeys.stated_in_source)}
        />
        <MetricTile
          label="Established in review"
          value={formatCount(storeys.established_in_review)}
        />
        <MetricTile
          label="Still unknown"
          value={formatCount(storeys.unstated)}
          footnote={complete ? "" : "cannot be one Oasis function"}
        />
        <MetricTile
          label="Value with no height"
          value={formatMoney(storeys.value_unstated)}
          footnote={complete ? "" : "refused until the representation is approved"}
        />
      </div>
      {complete ? (
        <Notice tone="ok">
          Every location carries a height, so every risk resolves to a single
          vulnerability function.
        </Notice>
      ) : (
        <Notice tone="warning">
          {percent(1 - storeys.value_stated_share)} of value sits on risks with
          no storey count. {storeys.note}
        </Notice>
      )}
    </Card>
  );
}

/** Every gap, with the value behind it and the failure it will produce. */
function MissingInputs({ results }: { results: ImportResults }) {
  const missing = results.missing_model_inputs;
  if (!missing.length) {
    return (
      <Card title="Missing model inputs">
        <Notice tone="ok">
          Every location states everything the model needs.
        </Notice>
      </Card>
    );
  }
  return (
    <Card title="Missing model inputs">
      <table className="grid">
        <thead>
          <tr>
            <th scope="col">Attribute</th>
            <th scope="col" className="numeric">
              Locations
            </th>
            <th scope="col" className="numeric">
              Value
            </th>
            <th scope="col">What it costs</th>
          </tr>
        </thead>
        <tbody>
          {missing.map((item) => (
            <tr key={item.field}>
              <th scope="row">{FIELD_LABELS[item.field] ?? item.field}</th>
              <td className="numeric">{formatCount(item.locations)}</td>
              <td className="numeric">{formatMoney(item.value)}</td>
              <td className="consequence">{item.consequence}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** What came in, by country, class and cohort. */
function Included({ results }: { results: ImportResults }) {
  const byCohort = results.review.by_cohort;
  return (
    <Card title="What was included">
      <div className="tiles">
        {Object.entries(byCohort).map(([cohort, count]) => (
          <MetricTile
            key={cohort}
            label={`Cohort ${cohort}`}
            value={formatCount(count)}
          />
        ))}
      </div>
      <Disclosure summary={`By country and class (${results.included.length})`}>
        <table className="grid">
          <thead>
            <tr>
              <th scope="col">Country</th>
              <th scope="col">Class of business</th>
              <th scope="col">Cohort</th>
              <th scope="col" className="numeric">
                Locations
              </th>
              <th scope="col" className="numeric">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {results.included.map((item, index) => (
              <tr key={`${item.country_code}-${item.class_of_business}-${index}`}>
                <td>{item.country_code}</td>
                <td>{item.class_of_business}</td>
                <td>{item.cohort}</td>
                <td className="numeric">{formatCount(item.locations)}</td>
                <td className="numeric">{formatMoney(item.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Disclosure>
    </Card>
  );
}

/** Multi-location businesses and repeated coordinates: where allocation bites. */
function Allocation({ results }: { results: ImportResults }) {
  const multi = results.multi_location_businesses;
  const repeated = results.repeated_coordinates;
  return (
    <Card title="Allocation and coordinate findings">
      <p>{results.allocation_note}</p>
      <div className="tiles">
        <MetricTile
          label="Multi-location businesses"
          value={formatCount(multi.length)}
        />
        <MetricTile
          label="Value under allocation"
          value={formatMoney(
            multi
              .filter((item) => !item.states_own_values)
              .reduce((total, item) => total + item.value, 0),
          )}
        />
        <MetricTile
          label="Repeated coordinates"
          value={formatCount(repeated.length)}
          footnote={repeated.length > 0 ? "confirm none is a city centroid" : ""}
        />
      </div>
      {repeated.length > 0 ? (
        <Disclosure summary={`Locations sharing a coordinate (${repeated.length})`}>
          <p className="muted">
            Two units of one business at one address are ordinary. A geocoder
            that fell back to a city centroid produces the same thing, and the
            two are indistinguishable without looking.
          </p>
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">Coordinate</th>
                <th scope="col" className="numeric">
                  Locations
                </th>
                <th scope="col">Businesses</th>
                <th scope="col" className="numeric">
                  Value
                </th>
              </tr>
            </thead>
            <tbody>
              {repeated.map((item) => (
                <tr key={item.coordinate}>
                  <td className="mono">{item.coordinate}</td>
                  <td className="numeric">{formatCount(item.count)}</td>
                  <td>{item.businesses.join(", ")}</td>
                  <td className="numeric">{formatMoney(item.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Disclosure>
      ) : null}
    </Card>
  );
}

/** The four modes, because the difference is not visible in the number. */
function UseModes({ results }: { results: ImportResults }) {
  const ordered = useMemo(
    () =>
      [...results.use_modes].sort(
        (a, b) => MODE_ORDER.indexOf(a.mode) - MODE_ORDER.indexOf(b.mode),
      ),
    [results.use_modes],
  );
  return (
    <Card title="What a result from this import may be used for">
      <ol className="modes">
        {ordered.map((item) => (
          <li key={item.mode}>
            <strong>{MODE_LABELS[item.mode] ?? item.mode}</strong>
            <span>{item.meaning}</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

interface QueueProps {
  batchId: UUID;
  locations: QueuedLocation[];
  outstanding: number;
  outstandingValue: number;
  loading: boolean;
}

/** The backlog a person owes, largest value first. */
function ReviewQueuePanel({
  batchId,
  locations,
  outstanding,
  outstandingValue,
  loading,
}: QueueProps) {
  if (loading) return <Spinner label="Loading the review queue" />;
  return (
    <Card title="Review queue">
      <div className="tiles">
        <MetricTile label="Awaiting a decision" value={formatCount(outstanding)} />
        <MetricTile label="Value awaiting" value={formatMoney(outstandingValue)} />
      </div>
      {locations.length === 0 ? (
        <Notice tone="ok">Nothing is waiting on a person.</Notice>
      ) : (
        <>
          <p className="muted">
            Ordered by value. An afternoon of review is worth more spent on the
            risks that carry the money than on whichever business sorts first.
          </p>
          <ul className="queue">
            {locations.map((item) => (
              <QueueRow key={item.id} batchId={batchId} location={item} />
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

/** One risk, and the two decisions worth making while looking at it. */
function QueueRow({
  batchId,
  location,
}: {
  batchId: UUID;
  location: QueuedLocation;
}) {
  const decide = useRecordDecision(batchId);
  const [field, setField] = useState("storeys");
  const [value, setValue] = useState("");
  const [rationale, setRationale] = useState("");

  const error = decide.error
    ? decide.error instanceof ApiError
      ? decide.error.message
      : String(decide.error)
    : "";

  function submit(event: React.FormEvent) {
    event.preventDefault();
    decide.mutate(
      {
        locationId: location.id,
        field,
        value: field === "storeys" && value === "" ? null : value,
        rationale,
      },
      { onSuccess: () => setRationale("") },
    );
  }

  return (
    <li className="queue__row">
      <div className="queue__head">
        <span className="queue__id">
          {location.business_id} / {location.location_number}
          {location.primary_location ? <em> primary</em> : null}
        </span>
        <span className="queue__value">
          {location.total_insured_value === null
            ? "value allocated"
            : formatMoney(location.total_insured_value)}
        </span>
      </div>
      <dl className="queue__facts">
        <div>
          <dt>Coordinate</dt>
          <dd className="mono">{location.coordinate || "none"}</dd>
        </div>
        <div>
          <dt>Precision</dt>
          <dd>{location.precision || "unstated"}</dd>
        </div>
        <div>
          <dt>Cohort</dt>
          <dd>
            {location.cohort}
            {location.cohort_reason ? (
              <span className="muted"> — {location.cohort_reason}</span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>Storeys</dt>
          <dd>
            {location.storeys ?? "unknown"}
            {location.storeys_are_reviewed ? (
              <span className="muted"> — established in review</span>
            ) : null}
          </dd>
        </div>
      </dl>

      <form className="queue__form" onSubmit={submit}>
        <Field label="Decision" htmlFor={`${location.id}-field`}>
          <Select
            id={`${location.id}-field`}
            value={field}
            onChange={(event) => setField(event.target.value)}
          >
            <option value="storeys">Storeys</option>
            <option value="cohort">Cohort</option>
            <option value="review_state">Review outcome</option>
          </Select>
        </Field>
        <Field label="Value" htmlFor={`${location.id}-value`}>
          <TextInput
            id={`${location.id}-value`}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={field === "storeys" ? "e.g. 6" : "e.g. A"}
          />
        </Field>
        <Field
          label="Rationale"
          htmlFor={`${location.id}-rationale`}
          hint="What was established, and from what. This is what an auditor reads."
          required
        >
          <TextInput
            id={`${location.id}-rationale`}
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            required
          />
        </Field>
        <Button type="submit" disabled={decide.isPending}>
          {decide.isPending ? "Recording…" : "Record decision"}
        </Button>
      </form>

      {error ? <Notice tone="error">{error}</Notice> : null}

      {location.history.length > 0 ? (
        <Disclosure summary={`Decisions so far (${location.history.length})`}>
          <ol className="history">
            {location.history.map((entry, index) => (
              <li key={`${entry.decided_at}-${index}`}>
                <strong>{entry.field}</strong> {entry.from || "—"} → {entry.to || "—"}
                <span className="muted">
                  {" "}
                  {entry.decided_by} on {entry.decided_at.slice(0, 10)}
                </span>
                <p>{entry.rationale}</p>
              </li>
            ))}
          </ol>
        </Disclosure>
      ) : null}
    </li>
  );
}
