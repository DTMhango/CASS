/**
 * The model catalogue.
 *
 * Section 3 requires it to show country, peril, version, publication state,
 * assumptions and validation date. Section 9 requires known limitations to be
 * visible here, and section 6 requires the SA-only prototype to report the
 * vulnerability classes and TIV it cannot model.
 *
 * The catalogue is therefore not a list of things to pick from. It is the
 * screen where an analyst finds out what a model version will not tell them.
 */

import { useModelCatalogue } from "@/api/hooks";
import type { CatalogueModel } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Disclosure,
  EmptyState,
  Notice,
  PageHeader,
  Spinner,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatDate, formatPercent } from "@/lib/format";

import "./ModelCatalogue.css";

/** How each peril-scope treatment should read, from section 9. */
const SCOPE_TONE: Record<string, "ok" | "warning" | "error" | "idle"> = {
  included: "ok",
  proxied: "warning",
  excluded: "error",
  not_material: "idle",
};

export function ModelCatalogue({ embedded = false }: { embedded?: boolean } = {}) {
  const { data: models, isLoading } = useModelCatalogue();
  const context = useWorkingContext();

  if (isLoading) return <Spinner label="Loading the model catalogue" />;

  return (
    <>
      {embedded ? null : <PageHeader
        title="Model catalogue"
        description="Approved earthquake model versions, what each one covers, and what it does not. Select a version to carry it into the analysis builder."
      />}

      {!models || models.length === 0 ? (
        <Card>
          <EmptyState
            title="No model versions are published"
            description="A model version appears here once its grid, vulnerability set and validation evidence have been registered and it has been published."
          />
        </Card>
      ) : (
        <div className="catalogue">
          {models.map((model) => (
            <ModelCard
              key={model.id}
              model={model}
              selected={context.modelId === model.id}
              onSelect={() => context.setModel(model)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function ModelCard({
  model,
  selected,
  onSelect,
}: {
  model: CatalogueModel;
  selected: boolean;
  onSelect: () => void;
}) {
  const unsupported = model.unsupported_taxonomy_report ?? {};
  const unsupportedShare = unsupported.unsupported_tiv_share as number | undefined;
  const unsupportedClasses = (unsupported.classes as string[] | undefined) ?? [];

  return (
    <Card
      title={
        <span className="catalogue__title">
          {model.label || model.reference}
          {model.is_research_prototype ? (
            <StatusBadge
              tone="warning"
              detail="A research prototype may be run, but its output must not be used for decisions."
            >
              Research only
            </StatusBadge>
          ) : (
            <StatusBadge tone="ok" detail="Approved for decision use.">
              Approved
            </StatusBadge>
          )}
        </span>
      }
      description={
        <span className="mono">
          {model.reference} · grid {model.grid}
        </span>
      }
      actions={
        <Button variant={selected ? "secondary" : "primary"} onClick={onSelect}>
          {selected ? "Selected" : "Select"}
        </Button>
      }
    >
      <dl className="catalogue__facts">
        <Fact term="Country">{model.country_code}</Fact>
        <Fact term="Peril">{model.peril}</Fact>
        <Fact term="Version">{model.version}</Fact>
        <Fact term="Intensity measures">
          {model.imts.length ? model.imts.join(", ") : "not declared"}
        </Fact>
        <Fact term="Publication state">{model.publication_state}</Fact>
        <Fact term="Last validated">{formatDate(model.validation_date)}</Fact>
      </dl>

      {model.blockers.length > 0 ? (
        <Notice
          tone="warning"
          title="Outstanding before this may be published as a full country model"
        >
          <ul className="catalogue__blockers">
            {model.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </Notice>
      ) : null}

      {unsupportedShare !== undefined || unsupportedClasses.length > 0 ? (
        <Notice tone="warning" title="Exposure this version cannot model">
          {unsupportedShare !== undefined ? (
            <p>
              {formatPercent(unsupportedShare)} of benchmark value maps to vulnerability
              classes outside this release.
            </p>
          ) : null}
          {unsupportedClasses.length ? (
            <p className="mono catalogue__classes">{unsupportedClasses.join(", ")}</p>
          ) : null}
        </Notice>
      ) : null}

      {Object.keys(model.peril_scope ?? {}).length > 0 ? (
        <Disclosure summary="Peril scope statement">
          <ul className="catalogue__scope">
            {Object.entries(model.peril_scope as Record<string, { treatment?: string; rationale?: string }>).map(
              ([peril, detail]) => (
                <li key={peril}>
                  <StatusBadge
                    tone={SCOPE_TONE[detail?.treatment ?? ""] ?? "idle"}
                    size="sm"
                  >
                    {detail?.treatment ?? "unstated"}
                  </StatusBadge>
                  <span className="catalogue__scope-peril">{peril}</span>
                  {detail?.rationale ? (
                    <span className="muted">{detail.rationale}</span>
                  ) : null}
                </li>
              ),
            )}
          </ul>
        </Disclosure>
      ) : (
        <Notice tone="error" title="No peril scope statement">
          Results from this version cannot be labelled earthquake loss until the treatment of
          liquefaction, landslide, tsunami and fire following earthquake is recorded.
        </Notice>
      )}

      {model.assumptions_note ? (
        <Disclosure summary="Known limitations">
          <p>{model.assumptions_note}</p>
        </Disclosure>
      ) : null}
    </Card>
  );
}

function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="catalogue__fact">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
