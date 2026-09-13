/**
 * The rows of a portfolio, readable and correctable in place.
 *
 * A portfolio used to be four files you could attach and never see. A wrong
 * occupancy code meant going back to the spreadsheet, fixing it there, and
 * re-uploading -- with nothing on the platform able to show that the file that
 * came back was the file that was fixed.
 *
 * Every field here is built from the column's own schema: a coded column is a
 * list to pick from, a money column takes numbers within its range, a date
 * column takes a date. What cannot be typed cannot be saved, and what the
 * server still refuses comes back against the field that caused it.
 *
 * A published portfolio is read-only, because a run points at it. Correcting
 * one makes the next version, which is the only honest way to change something
 * a result already rests on.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import {
  useCorrectExposure,
  useEditRow,
  usePortfolioRows,
  useRemoveRow,
} from "@/api/hooks";
import type { ExposureVersion, PortfolioRow, RowColumn } from "@/api/types";
import {
  Button,
  Card,
  EmptyState,
  Notice,
  Select,
  Spinner,
  TextInput,
} from "@/components/primitives";
import { formatCount } from "@/lib/format";

import "./PortfolioRows.css";

const PAGE = 25;

const KIND_LABELS: Record<string, string> = {
  location: "Locations",
  account: "Policies",
  reins_info: "Reinsurance contracts",
  reins_scope: "Reinsurance scope",
};

/**
 * Which columns show before a person asks for the rest.
 *
 * Not "the first ten": the first ten of the OED location file are identifiers
 * and flags, and the occupancy and the values -- what an analyst actually
 * checks a row against -- sit past them. So the lead is chosen by what the
 * column is: what identifies the risk, what it is made of, and what it is
 * worth.
 */
const LEAD_NAMES = new Set([
  "AccNumber",
  "LocNumber",
  "CountryCode",
  "Latitude",
  "Longitude",
  "OccupancyCode",
  "ConstructionCode",
  "NumberOfStoreys",
  "LocCurrency",
  "PolNumber",
  "LayerNumber",
  "ReinsNumber",
  "ReinsName",
  "ReinsType",
]);

function leadColumns(columns: RowColumn[]): RowColumn[] {
  const lead = columns.filter((column) => LEAD_NAMES.has(column.name) || column.is_tiv);
  return lead.length ? lead : columns.slice(0, 8);
}

export function PortfolioRows({ version }: { version: ExposureVersion }) {
  const [kind, setKind] = useState("location");
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);

  const page = usePortfolioRows(version.id, kind, { offset, limit: PAGE, search });
  const correct = useCorrectExposure(version.id);

  if (page.isLoading) return <Spinner label="Reading the portfolio" />;
  const data = page.data;
  if (!data) {
    return (
      <Card title="Portfolio rows">
        <EmptyState
          title="Nothing to show yet"
          description="Attach the source files above and this is what CASS reads from them."
        />
      </Card>
    );
  }

  const allColumns = data.columns ?? [];
  const columns = showAll ? allColumns : leadColumns(allColumns);
  const kinds = data.attached?.length ? data.attached : ["location"];
  const error = correct.error as ApiError | null;

  return (
    <Card
      title="Portfolio rows"
      description={
        data.editable
          ? "What CASS reads from the files. Correct a row here and the file is rewritten from it."
          : "What CASS reads from the files. This version is published, so it cannot change."
      }
      padded={false}
      actions={
        data.editable ? null : (
          <Button
            size="sm"
            busy={correct.isPending}
            onClick={() => correct.mutate()}
            title="Copy this portfolio into the next version, where it can be corrected."
          >
            Correct in a new version
          </Button>
        )
      }
    >
      <div className="rows__controls">
        <div className="rows__kinds" role="group" aria-label="Which file to show">
          {kinds.map((item) => (
            <button
              key={item}
              type="button"
              className={`rows__kind ${item === kind ? "rows__kind--active" : ""}`.trim()}
              onClick={() => {
                setKind(item);
                setOffset(0);
                setEditing(null);
              }}
            >
              {KIND_LABELS[item] ?? item}
            </button>
          ))}
        </div>
        <TextInput
          aria-label="Search the rows"
          placeholder="Search"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setOffset(0);
          }}
        />
        <Button size="sm" variant="ghost" onClick={() => setShowAll((value) => !value)}>
          {showAll ? "Fewer columns" : `All ${allColumns.length} columns`}
        </Button>
      </div>

      {error ? (
        <div className="rows__message">
          <Notice tone="error" title="Not corrected">
            {error.message}
          </Notice>
        </div>
      ) : null}
      {correct.isSuccess ? (
        <div className="rows__message">
          <Notice tone="ok" title={`Version ${correct.data.version} created`}>
            It holds the same rows and can be corrected. Select it on the left.
          </Notice>
        </div>
      ) : null}

      <div className="rows__scroll">
        <table className="data-table rows__table">
          <thead>
            <tr>
              <th scope="col" className="numeric">
                Row
              </th>
              {columns.map((column) => (
                <th key={column.name} scope="col" title={column.help}>
                  {column.label}
                  {column.required ? <abbr title="Required"> *</abbr> : null}
                </th>
              ))}
              {data.editable ? <th scope="col" /> : null}
            </tr>
          </thead>
          <tbody>
            {(data.rows ?? []).map((row) =>
              editing === row.row_number ? (
                <EditableRow
                  key={row.row_number}
                  version={version}
                  kind={kind}
                  row={row}
                  columns={columns}
                  onDone={() => setEditing(null)}
                />
              ) : (
                <tr key={row.row_number}>
                  <td className="numeric muted">{row.row_number}</td>
                  {columns.map((column) => (
                    <td key={column.name} className={cellClass(column)}>
                      {row.values[column.name] || <span className="muted">—</span>}
                    </td>
                  ))}
                  {data.editable ? (
                    <td>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(row.row_number)}>
                        Correct
                      </Button>
                    </td>
                  ) : null}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>

      <div className="rows__footer">
        <span className="muted">
          {search
            ? `${formatCount(data.count)} of ${formatCount(data.total)} row(s) match`
            : `${formatCount(data.total)} row(s)`}
        </span>
        <div className="rows__pager">
          <Button
            size="sm"
            variant="ghost"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            Previous
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={offset + PAGE >= data.count}
            onClick={() => setOffset(offset + PAGE)}
          >
            Next
          </Button>
        </div>
      </div>
    </Card>
  );
}

function cellClass(column: RowColumn): string {
  return column.type === "money" || column.type === "integer" || column.type === "decimal"
    ? "numeric"
    : "";
}

function EditableRow({
  version,
  kind,
  row,
  columns,
  onDone,
}: {
  version: ExposureVersion;
  kind: string;
  row: PortfolioRow;
  columns: RowColumn[];
  onDone: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(row.values);
  const edit = useEditRow(version.id);
  const remove = useRemoveRow(version.id);
  const refusal = edit.error as ApiError | null;
  // Field-level refusals: the server names the column that would not take the
  // value, so the message lands under the field rather than above the row.
  const fields =
    (refusal?.body as { fields?: Record<string, string> } | undefined)?.fields ?? {};

  function save() {
    const changed = Object.fromEntries(
      Object.entries(values).filter(([name, value]) => row.values[name] !== value),
    );
    edit.mutate({ kind, row_number: row.row_number, values: changed }, { onSuccess: onDone });
  }

  return (
    <tr className="rows__editing">
      <td className="numeric muted">{row.row_number}</td>
      {columns.map((column) => (
        <td key={column.name}>
          <CellInput
            column={column}
            value={values[column.name] ?? ""}
            invalid={fields[column.name]}
            onChange={(next) => setValues({ ...values, [column.name]: next })}
          />
          {fields[column.name] ? (
            <p className="rows__error">{fields[column.name]}</p>
          ) : null}
        </td>
      ))}
      <td className="rows__actions">
        <Button size="sm" variant="primary" busy={edit.isPending} onClick={save}>
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="danger"
          busy={remove.isPending}
          onClick={() =>
            remove.mutate({ kind, row_number: row.row_number }, { onSuccess: onDone })
          }
          title="Remove this row from the portfolio."
        >
          Remove
        </Button>
      </td>
    </tr>
  );
}

/**
 * One cell, typed by its column.
 *
 * The constraint is the schema's, not this screen's guess at it, so a value the
 * validator would refuse is one the field will not accept in the first place.
 */
function CellInput({
  column,
  value,
  invalid,
  onChange,
}: {
  column: RowColumn;
  value: string;
  invalid?: string;
  onChange: (value: string) => void;
}) {
  const shared = {
    "aria-label": column.label,
    "aria-invalid": invalid ? true : undefined,
    value,
    onChange: (event: { target: { value: string } }) => onChange(event.target.value),
  };

  if (column.allowed) {
    return (
      <Select {...shared}>
        {column.required ? null : <option value="" />}
        {column.allowed.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </Select>
    );
  }

  if (column.type === "date") {
    return <TextInput type="date" {...shared} />;
  }

  if (
    ["money", "decimal", "integer", "rate", "latitude", "longitude"].includes(column.type)
  ) {
    return (
      <TextInput
        type="number"
        inputMode="decimal"
        step={column.type === "integer" ? 1 : "any"}
        min={column.minimum ?? undefined}
        max={column.maximum ?? undefined}
        {...shared}
      />
    );
  }

  return <TextInput {...shared} />;
}
