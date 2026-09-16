/**
 * Building the financial structure on the platform.
 *
 * A portfolio that arrived with locations alone can be given its policies and
 * treaties here, written into the draft version's own OED files. The forms ask
 * only for what the engine uses for each contract type -- a quota share's
 * ceded share, a surplus share's share on each risk it names, a catastrophe
 * excess of loss's attachment and limit for each event -- and every change
 * comes back as the structure read from the files, findings and all, so the
 * page above shows what the portfolio now says rather than what was typed.
 *
 * A published version is immutable, so it is offered the correction that makes
 * a new draft rather than forms that could not be saved.
 */

import { useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import {
  useAddContract,
  useAddPolicy,
  useCorrectExposure,
  usePortfolioRows,
  useRemoveContract,
  useRemovePolicy,
} from "@/api/hooks";
import type {
  ContractInput,
  ContractScopeInput,
  ExposureVersion,
  FinancialStructureSummary,
  PolicyLayerInput,
} from "@/api/types";
import { Button, Card, Combobox, Field, Notice, Select, TextInput } from "@/components/primitives";

import "./GridBuilder.css";

const CONTRACT_TYPES: Record<ContractInput["type"], { label: string; explains: string }> = {
  CXL: {
    label: "Catastrophe excess of loss",
    explains:
      "Pays each event's loss across the portfolio above the attachment, up to the limit.",
  },
  QS: {
    label: "Quota share",
    explains: "Cedes a fixed share of every loss it covers.",
  },
  SS: {
    label: "Surplus share",
    explains:
      "Cedes risk by risk: each risk it names carries its own ceded share, usually the part of the risk above the retained line.",
  },
};

const RISK_LEVELS: { value: "LOC" | "POL" | "ACC"; label: string }[] = [
  { value: "LOC", label: "Per location" },
  { value: "POL", label: "Per policy" },
  { value: "ACC", label: "Per account" },
];

interface Refusal {
  message: string;
  fields: Record<string, string>;
}

function refusalOf(error: unknown): Refusal | null {
  if (!error) return null;
  if (!(error instanceof ApiError)) return { message: String(error), fields: {} };
  const body = (error.body ?? {}) as { fields?: Record<string, string> };
  return { message: error.message, fields: body.fields ?? {} };
}

function Refused({ error }: { error: unknown }) {
  const refusal = refusalOf(error);
  if (!refusal) return null;
  const messages = Object.values(refusal.fields);
  return (
    <Notice tone="error" title={refusal.message}>
      {messages.length ? (
        <ul>
          {messages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}
    </Notice>
  );
}

/** What is sent for a number field: nothing where nothing was typed. */
function amount(value: string): string | undefined {
  return value.trim() === "" ? undefined : value.trim();
}

export function StructureBuilder({
  exposure,
  structure,
}: {
  exposure: ExposureVersion;
  structure: FinancialStructureSummary;
}) {
  const correct = useCorrectExposure(exposure.id);
  const rows = usePortfolioRows(exposure.id, "location", { offset: 0, limit: 500, search: "" });

  const locations = useMemo(
    () =>
      (rows.data?.rows ?? []).map((row) => ({
        account: row.values.AccNumber ?? "",
        location: row.values.LocNumber ?? "",
      })),
    [rows.data],
  );
  const accounts = useMemo(
    () => [...new Set(locations.map((item) => item.account).filter(Boolean))].sort(),
    [locations],
  );

  if (exposure.is_frozen) {
    return (
      <Card
        title="Build the structure"
        description="A published portfolio cannot change. Correct it in a new version, and build its policies and contracts there."
      >
        <Refused error={correct.error} />
        <Button busy={correct.isPending} onClick={() => correct.mutate()}>
          Correct in a new version
        </Button>
      </Card>
    );
  }

  return (
    <>
      <PolicyForm exposure={exposure} accounts={accounts} />
      <ContractForm exposure={exposure} accounts={accounts} locations={locations} />
      <Written exposure={exposure} structure={structure} />
    </>
  );
}

function PolicyForm({ exposure, accounts }: { exposure: ExposureVersion; accounts: string[] }) {
  const add = useAddPolicy(exposure.id);
  const [account, setAccount] = useState("");
  const [policy, setPolicy] = useState("");
  const [perils, setPerils] = useState("QEQ");
  const [deductible, setDeductible] = useState("");
  const [layers, setLayers] = useState<PolicyLayerInput[]>([
    { attachment: "0", limit: "", participation: "1" },
  ]);

  const ready = account !== "" && policy.trim() !== "" && perils.trim() !== "" && layers.length > 0;

  function setLayer(index: number, change: Partial<PolicyLayerInput>) {
    setLayers((current) =>
      current.map((layer, position) => (position === index ? { ...layer, ...change } : layer)),
    );
  }

  return (
    <Card
      title="Add a policy"
      description="A policy's layers are evaluated in order: each attaches where its cover begins and pays up to its limit, and the signed share is the proportion of the layer written."
    >
      <Refused error={add.error} />
      <div className="grid-builder__row">
        <Field label="Account" htmlFor="policy-account" required>
          <Combobox
            id="policy-account"
            placeholder="Choose an account"
            value={account}
            onChange={setAccount}
            options={accounts.map((item) => ({ value: item, label: item }))}
          />
        </Field>
        <Field label="Policy reference" htmlFor="policy-reference" required>
          <TextInput id="policy-reference" value={policy} onChange={(event) => setPolicy(event.target.value)} />
        </Field>
        <Field label="Perils covered" htmlFor="policy-perils" required hint="QEQ for earthquake shake.">
          <TextInput id="policy-perils" value={perils} onChange={(event) => setPerils(event.target.value)} />
        </Field>
        <Field label="Policy deductible" htmlFor="policy-deductible" hint="A flat amount; blank for none.">
          <TextInput
            id="policy-deductible"
            inputMode="decimal"
            value={deductible}
            onChange={(event) => setDeductible(event.target.value)}
          />
        </Field>
      </div>

      {layers.map((layer, index) => (
        <div className="grid-builder__row" key={index}>
          <Field label={`Layer ${index + 1} attachment`} htmlFor={`layer-${index}-attachment`}>
            <TextInput
              id={`layer-${index}-attachment`}
              inputMode="decimal"
              value={layer.attachment}
              onChange={(event) => setLayer(index, { attachment: event.target.value })}
            />
          </Field>
          <Field label={`Layer ${index + 1} limit`} htmlFor={`layer-${index}-limit`} hint="Blank for unlimited.">
            <TextInput
              id={`layer-${index}-limit`}
              inputMode="decimal"
              value={layer.limit}
              onChange={(event) => setLayer(index, { limit: event.target.value })}
            />
          </Field>
          <Field
            label={`Layer ${index + 1} signed share`}
            htmlFor={`layer-${index}-share`}
            hint="A proportion: 0.25 for 25%."
          >
            <TextInput
              id={`layer-${index}-share`}
              inputMode="decimal"
              value={layer.participation}
              onChange={(event) => setLayer(index, { participation: event.target.value })}
            />
          </Field>
          {layers.length > 1 ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setLayers((current) => current.filter((_, position) => position !== index))}
            >
              Remove layer {index + 1}
            </Button>
          ) : null}
        </div>
      ))}

      <div className="grid-builder__actions">
        <Button
          variant="secondary"
          onClick={() =>
            setLayers((current) => [...current, { attachment: "", limit: "", participation: "1" }])
          }
        >
          Add a layer
        </Button>
        <Button
          variant="primary"
          disabled={!ready}
          busy={add.isPending}
          onClick={() =>
            add.mutate({
              account,
              policy: policy.trim(),
              perils: perils.trim(),
              deductible: amount(deductible),
              layers: layers.map((layer) => ({
                attachment: layer.attachment.trim(),
                limit: layer.limit.trim(),
                participation: layer.participation.trim(),
              })),
            })
          }
        >
          Write the policy
        </Button>
      </div>
    </Card>
  );
}

function ContractForm({
  exposure,
  accounts,
  locations,
}: {
  exposure: ExposureVersion;
  accounts: string[];
  locations: { account: string; location: string }[];
}) {
  const add = useAddContract(exposure.id);
  const [type, setType] = useState<ContractInput["type"]>("CXL");
  const [name, setName] = useState("");
  const [perils, setPerils] = useState("QEQ");
  const [priority, setPriority] = useState("1");
  const [ceded, setCeded] = useState("");
  const [placed, setPlaced] = useState("1");
  const [attachment, setAttachment] = useState("");
  const [limit, setLimit] = useState("");
  const [riskLevel, setRiskLevel] = useState<"LOC" | "POL" | "ACC">("LOC");
  const [riskLimit, setRiskLimit] = useState("");
  const [covers, setCovers] = useState<"whole" | "named">("whole");
  const [scope, setScope] = useState<ContractScopeInput[]>([]);

  const named = type === "SS" || covers === "named";

  function setScopeRow(index: number, change: Partial<ContractScopeInput>) {
    setScope((current) => current.map((row, position) => (position === index ? { ...row, ...change } : row)));
  }

  function body(): ContractInput {
    const common = {
      type,
      name: name.trim() || undefined,
      perils: perils.trim(),
      inuring_priority: Number(priority) || 1,
      placed_percent: amount(placed),
    };
    const scoped = named
      ? { scope: scope.map((row) => ({ ...row, ceded_percent: type === "SS" ? row.ceded_percent : undefined })) }
      : { whole_portfolio: true };
    if (type === "CXL") {
      return {
        ...common,
        ...scoped,
        ceded_percent: amount(ceded),
        occurrence_attachment: amount(attachment),
        occurrence_limit: amount(limit),
      };
    }
    if (type === "QS") {
      return { ...common, ...scoped, ceded_percent: amount(ceded), occurrence_limit: amount(limit) };
    }
    return { ...common, scope: scoped.scope ?? [], risk_level: riskLevel, risk_limit: amount(riskLimit) };
  }

  return (
    <Card title="Add a reinsurance contract" description={CONTRACT_TYPES[type].explains}>
      <Refused error={add.error} />
      <div className="grid-builder__row">
        <Field label="Contract type" htmlFor="contract-type" required>
          <Select
            id="contract-type"
            value={type}
            onChange={(event) => setType(event.target.value as ContractInput["type"])}
          >
            {(Object.keys(CONTRACT_TYPES) as ContractInput["type"][]).map((key) => (
              <option key={key} value={key}>
                {CONTRACT_TYPES[key].label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Contract name" htmlFor="contract-name">
          <TextInput id="contract-name" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Perils covered" htmlFor="contract-perils" required>
          <TextInput id="contract-perils" value={perils} onChange={(event) => setPerils(event.target.value)} />
        </Field>
        <Field
          label="Inuring priority"
          htmlFor="contract-priority"
          hint="Lower numbers apply first and inure to the benefit of higher ones."
        >
          <TextInput
            id="contract-priority"
            inputMode="numeric"
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
        </Field>
      </div>

      <div className="grid-builder__row">
        {type === "CXL" ? (
          <>
            <Field label="Attachment per event" htmlFor="contract-attachment" required>
              <TextInput
                id="contract-attachment"
                inputMode="decimal"
                value={attachment}
                onChange={(event) => setAttachment(event.target.value)}
              />
            </Field>
            <Field label="Limit per event" htmlFor="contract-limit" required>
              <TextInput
                id="contract-limit"
                inputMode="decimal"
                value={limit}
                onChange={(event) => setLimit(event.target.value)}
              />
            </Field>
            <Field label="Share of the layer ceded" htmlFor="contract-ceded" hint="Blank for all of it.">
              <TextInput
                id="contract-ceded"
                inputMode="decimal"
                value={ceded}
                onChange={(event) => setCeded(event.target.value)}
              />
            </Field>
          </>
        ) : null}
        {type === "QS" ? (
          <>
            <Field label="Ceded share" htmlFor="contract-ceded" required hint="A proportion: 0.3 for 30%.">
              <TextInput
                id="contract-ceded"
                inputMode="decimal"
                value={ceded}
                onChange={(event) => setCeded(event.target.value)}
              />
            </Field>
            <Field label="Limit per event" htmlFor="contract-limit" hint="Blank for unlimited.">
              <TextInput
                id="contract-limit"
                inputMode="decimal"
                value={limit}
                onChange={(event) => setLimit(event.target.value)}
              />
            </Field>
          </>
        ) : null}
        {type === "SS" ? (
          <>
            <Field label="Risk level" htmlFor="contract-risk-level" required>
              <Select
                id="contract-risk-level"
                value={riskLevel}
                onChange={(event) => setRiskLevel(event.target.value as "LOC" | "POL" | "ACC")}
              >
                {RISK_LEVELS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Limit per risk" htmlFor="contract-risk-limit" hint="Blank for unlimited.">
              <TextInput
                id="contract-risk-limit"
                inputMode="decimal"
                value={riskLimit}
                onChange={(event) => setRiskLimit(event.target.value)}
              />
            </Field>
          </>
        ) : null}
        <Field label="Placed share" htmlFor="contract-placed" hint="The proportion of the contract placed.">
          <TextInput
            id="contract-placed"
            inputMode="decimal"
            value={placed}
            onChange={(event) => setPlaced(event.target.value)}
          />
        </Field>
      </div>

      {type === "SS" ? (
        <p className="muted">
          A surplus share names each risk it cedes, with the share ceded on it.
        </p>
      ) : (
        <div className="grid-builder__row">
          <Field label="Covers" htmlFor="contract-covers">
            <Select
              id="contract-covers"
              value={covers}
              onChange={(event) => setCovers(event.target.value as "whole" | "named")}
            >
              <option value="whole">The whole portfolio</option>
              <option value="named">Named accounts or locations</option>
            </Select>
          </Field>
        </div>
      )}

      {named
        ? scope.map((row, index) => (
            <div className="grid-builder__row" key={index}>
              <Field label={`Risk ${index + 1} account`} htmlFor={`scope-${index}-account`}>
                <Combobox
                  id={`scope-${index}-account`}
                  placeholder="Choose an account"
                  value={row.account}
                  onChange={(value) => setScopeRow(index, { account: value, location: "" })}
                  options={accounts.map((item) => ({ value: item, label: item }))}
                />
              </Field>
              {type !== "SS" || riskLevel === "LOC" ? (
                <Field label={`Risk ${index + 1} location`} htmlFor={`scope-${index}-location`}>
                  <Combobox
                    id={`scope-${index}-location`}
                    placeholder={type === "SS" ? "Choose a location" : undefined}
                    value={row.location ?? ""}
                    onChange={(value) => setScopeRow(index, { location: value })}
                    options={[
                      // A surplus share at location level must name one; other contracts may take the whole account.
                      ...(type === "SS" ? [] : [{ value: "", label: "The whole account" }]),
                      ...locations
                        .filter((item) => item.account === row.account)
                        .map((item) => ({ value: item.location, label: item.location })),
                    ]}
                  />
                </Field>
              ) : null}
              {type === "SS" && riskLevel === "POL" ? (
                <Field label={`Risk ${index + 1} policy`} htmlFor={`scope-${index}-policy`}>
                  <TextInput
                    id={`scope-${index}-policy`}
                    value={row.policy ?? ""}
                    onChange={(event) => setScopeRow(index, { policy: event.target.value })}
                  />
                </Field>
              ) : null}
              {type === "SS" ? (
                <Field
                  label={`Risk ${index + 1} ceded share`}
                  htmlFor={`scope-${index}-share`}
                  hint="A proportion of this risk."
                >
                  <TextInput
                    id={`scope-${index}-share`}
                    inputMode="decimal"
                    value={row.ceded_percent ?? ""}
                    onChange={(event) => setScopeRow(index, { ceded_percent: event.target.value })}
                  />
                </Field>
              ) : null}
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setScope((current) => current.filter((_, position) => position !== index))}
              >
                Remove risk {index + 1}
              </Button>
            </div>
          ))
        : null}

      <div className="grid-builder__actions">
        {named ? (
          <Button
            variant="secondary"
            onClick={() => setScope((current) => [...current, { account: "", location: "" }])}
          >
            Name a risk
          </Button>
        ) : null}
        <Button
          variant="primary"
          disabled={perils.trim() === ""}
          busy={add.isPending}
          onClick={() => add.mutate(body())}
        >
          Write the contract
        </Button>
      </div>
    </Card>
  );
}

function Written({
  exposure,
  structure,
}: {
  exposure: ExposureVersion;
  structure: FinancialStructureSummary;
}) {
  const removePolicy = useRemovePolicy(exposure.id);
  const removeContract = useRemoveContract(exposure.id);

  const policies = [
    ...new Map(
      structure.layers.map((layer) => [`${layer.account}/${layer.policy}`, layer]),
    ).values(),
  ];
  if (!policies.length && !structure.contracts.length) return null;

  return (
    <Card title="What is written" description="Remove a policy or a contract to write it again.">
      <Refused error={removePolicy.error ?? removeContract.error} />
      <ul className="structure__findings">
        {policies.map((layer) => (
          <li key={`${layer.account}/${layer.policy}`}>
            Policy <span className="mono">{layer.policy}</span> on account{" "}
            <span className="mono">{layer.account}</span>{" "}
            <Button
              variant="secondary"
              size="sm"
              busy={removePolicy.isPending}
              onClick={() => removePolicy.mutate({ account: layer.account, policy: layer.policy })}
            >
              Remove policy {layer.policy}
            </Button>
          </li>
        ))}
        {structure.contracts.map((contract) =>
          contract.number === null ? null : (
            <li key={contract.number}>
              Contract <span className="mono">{contract.number}</span>{" "}
              {contract.name || contract.type_label}{" "}
              <Button
                variant="secondary"
                size="sm"
                busy={removeContract.isPending}
                onClick={() => removeContract.mutate({ number: contract.number as number })}
              >
                Remove contract {contract.number}
              </Button>
            </li>
          ),
        )}
      </ul>
    </Card>
  );
}
