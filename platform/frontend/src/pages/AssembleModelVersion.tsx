/**
 * Pairing a grid with a vulnerability set into a model version.
 *
 * A grid says where a loss can be computed and a vulnerability set says how much
 * damage the shaking does. The model version is the pair, and it is what a run
 * names -- so this screen asks for the two halves and a version, and nothing
 * else. The scope statement and the limitations both halves carry are written
 * by the API from what was paired, because a form that asked somebody to retype
 * them would be inviting a version whose caveats do not match its parts.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import {
  useAssembleModelVersion,
  useGrids,
  useVulnerabilitySets,
} from "@/api/hooks";
import {
  Button,
  Card,
  Combobox,
  EmptyState,
  Field,
  Notice,
  TextInput,
} from "@/components/primitives";
import { formatCount } from "@/lib/format";

import "./GridBuilder.css";

export function AssembleModelVersion() {
  const grids = useGrids();
  const sets = useVulnerabilitySets();
  const assemble = useAssembleModelVersion();

  const [grid, setGrid] = useState("");
  const [vulnerabilitySet, setVulnerabilitySet] = useState("");
  const [version, setVersion] = useState("");
  const [label, setLabel] = useState("");

  const refusal = assemble.error as ApiError | null;
  const assembled = assemble.data;
  const ready = grid !== "" && vulnerabilitySet !== "" && version.trim() !== "";
  const halves = (grids.data?.length ?? 0) > 0 && (sets.data?.length ?? 0) > 0;

  return (
    <Card
      title="Assemble a model version"
      description="A grid and a vulnerability set for one country become the version a run names. Both halves must be for the same country: a version pairing one country's buildings with another's ground motion would calculate happily and mean nothing."
    >
      {refusal ? (
        <Notice tone="error" title="The model version was not assembled">
          {refusal.message}
        </Notice>
      ) : null}

      {assembled ? (
        <Notice tone="ok" title={`${assembled.reference} assembled`}>
          A draft research prototype.{" "}
          {assembled.publication_blockers.length > 0
            ? `${formatCount(assembled.publication_blockers.length)} thing(s) still block publication: ${assembled.publication_blockers.join(" ")}`
            : "Nothing blocks its publication."}
        </Notice>
      ) : null}

      {halves ? (
        <>
          <div className="grid-builder__row">
            <Field label="Grid" htmlFor="assemble-grid" required>
              <Combobox
                id="assemble-grid"
                placeholder="Choose a grid"
                value={grid}
                onChange={setGrid}
                options={(grids.data ?? []).map((item) => ({
                  value: item.id,
                  label: item.reference,
                  detail: `${formatCount(item.cell_count)} cells`,
                }))}
              />
            </Field>
            <Field label="Vulnerability set" htmlFor="assemble-vulnerability" required>
              <Combobox
                id="assemble-vulnerability"
                placeholder="Choose a vulnerability set"
                value={vulnerabilitySet}
                onChange={setVulnerabilitySet}
                options={(sets.data ?? []).map((item) => ({
                  value: item.id,
                  label: `${item.country_code} ${item.version}`,
                  detail: `${formatCount(item.function_count)} functions`,
                }))}
              />
            </Field>
          </div>

          <div className="grid-builder__row">
            <Field
              label="Model version"
              htmlFor="assemble-version"
              required
              hint="Its own version, distinct from either half's."
            >
              <TextInput
                id="assemble-version"
                value={version}
                onChange={(event) => setVersion(event.target.value)}
              />
            </Field>
            <Field label="Label" htmlFor="assemble-label">
              <TextInput
                id="assemble-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </Field>
          </div>

          <div className="grid-builder__actions">
            <Button
              variant="primary"
              disabled={!ready || assemble.isPending}
              onClick={() =>
                assemble.mutate({
                  grid,
                  vulnerability_set: vulnerabilitySet,
                  version: version.trim(),
                  label: label.trim(),
                })
              }
            >
              {assemble.isPending ? "Assembling" : "Assemble the model version"}
            </Button>
          </div>
        </>
      ) : (
        <EmptyState
          title="A model version needs both halves"
          description="Build a grid and a vulnerability set for the country first; they are what a version pairs."
        />
      )}
    </Card>
  );
}
