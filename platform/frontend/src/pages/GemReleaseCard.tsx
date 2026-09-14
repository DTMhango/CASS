/**
 * Which GEM release this installation builds vulnerability sets from.
 *
 * CASS does not carry GEM's models: the release is too large for the codebase
 * and it is GEM's to publish. The person running CASS keeps GEM's two
 * repositories on their own device, in the folder the installation mounts, and
 * chooses the release here. Every folder is read for its layout and for the
 * commit each repository is at, so the card says which release it is and
 * whether it is the one CASS was validated against, rather than asking anybody
 * to type either.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import { useChooseGemRelease, useGemRelease } from "@/api/hooks";
import type { GemReleaseInspection } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { Button, Card, Field, Notice, TextInput } from "@/components/primitives";
import { formatCount } from "@/lib/format";

import "./GridBuilder.css";

function Validation({ release }: { release: GemReleaseInspection }) {
  return release.matches_validated ? (
    <StatusBadge tone="ok" size="sm" detail="Both repositories are at the commits CASS was validated against.">
      validated
    </StatusBadge>
  ) : (
    <StatusBadge
      tone="warning"
      size="sm"
      detail={`Not at the ${release.validated_release} commits CASS was validated against.`}
    >
      not the validated commits
    </StatusBadge>
  );
}

export function GemReleaseCard() {
  const status = useGemRelease();
  const choose = useChooseGemRelease();
  const [path, setPath] = useState("");

  const data = status.data;
  const current = data?.current ?? null;
  const refusal = choose.error as ApiError | null;
  const problems = ((refusal?.body ?? {}) as { problems?: string[] }).problems ?? [];

  return (
    <Card
      title="GEM release"
      description="CASS does not carry GEM's models. Keep GEM's global_exposure_model and global_vulnerability_model repositories side by side on your device, in the models folder the installation mounts, and choose the release here."
    >
      {refusal ? (
        <Notice tone="error" title={refusal.message}>
          {problems.length ? (
            <ul>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : null}

      {data && current ? (
        <Notice
          tone={current.usable ? (current.matches_validated ? "ok" : "warning") : "error"}
          title={
            current.usable
              ? `Building from ${current.release || current.path}`
              : "The release in use cannot be built from"
          }
        >
          <p>
            <span className="mono">{current.path}</span>,{" "}
            {data.source === "chosen" ? "chosen on the platform" : "set for the installation"}.{" "}
            {current.usable ? `${formatCount(current.countries)} countries published.` : null}{" "}
            <Validation release={current} />
          </p>
          {[...current.problems, ...current.notes].length ? (
            <ul>
              {[...current.problems, ...current.notes].map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : null}

      {data && !current ? (
        <Notice tone="warning" title="No GEM release is chosen">
          No vulnerability set can be built until one is. Choose one found below, or name the
          folder that holds it.
        </Notice>
      ) : null}

      {data?.discovered.length ? (
        <ul className="structure__findings">
          {data.discovered.map((release) => (
            <li key={release.path}>
              <strong>{release.release || release.path}</strong>{" "}
              <span className="mono">{release.path}</span>{" "}
              {release.usable ? `— ${formatCount(release.countries)} countries ` : "— cannot be built from "}
              <Validation release={release} />{" "}
              <Button
                variant="secondary"
                size="sm"
                disabled={!release.usable || release.path === data.path}
                busy={choose.isPending}
                onClick={() => choose.mutate(release.path)}
              >
                {release.path === data.path ? "In use" : "Use this release"}
              </Button>
            </li>
          ))}
        </ul>
      ) : data ? (
        <p className="muted">
          No GEM release was found under <span className="mono">{data.mount}</span>, where the
          installation mounts the models folder from your device.
        </p>
      ) : null}

      <div className="grid-builder__row">
        <Field
          label="Or name the folder"
          htmlFor="gem-release-path"
          hint={data ? `As the installation sees it, under ${data.mount}.` : undefined}
        >
          <TextInput id="gem-release-path" value={path} onChange={(event) => setPath(event.target.value)} />
        </Field>
      </div>
      <div className="grid-builder__actions">
        <Button
          variant="primary"
          disabled={path.trim() === ""}
          busy={choose.isPending}
          onClick={() => choose.mutate(path.trim())}
        >
          Use this folder
        </Button>
      </div>
    </Card>
  );
}
