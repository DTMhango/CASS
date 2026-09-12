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

import { ApiError } from "@/api/client";
import {
  useImportResults,
  usePortfolioImports,
  useRecordDecision,
  useReviewQueue,
} from "@/api/hooks";
import type { ImportResults, QueuedLocation, UUID } from "@/api/types";
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

export function ImportReview() {
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
        <PageHeader
          title="Import review"
          description="Eligibility, missing model inputs and the review backlog"
        />
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
                  {item.source_filename || item.source_checksum.slice(0, 12)}
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
        </>
      ) : null}
    </>
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
          <dt>Source checksum</dt>
          <dd className="mono">{batch.source_checksum.slice(0, 24)}…</dd>
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
