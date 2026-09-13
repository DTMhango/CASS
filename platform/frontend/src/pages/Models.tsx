/**
 * The models an analysis can use, and the work that produces them.
 *
 * Choosing a model version, computing hazard and assembling a package are not
 * three steps in one line: a modeller runs hazard, builds a package, looks at
 * what the catalogue now says and goes round again. Three sidebar entries made
 * that cycle look like a sequence, and put a modeller's workbench in the middle
 * of an analyst's path. One area, three tabs, and the sidebar keeps its order.
 */

import { Tabs } from "@/components/Tabs";
import { useSession } from "@/api/hooks";
import { PageHeader } from "@/components/primitives";

import { HazardModels } from "./HazardModels";
import { ModelBuild } from "./ModelBuild";
import { ModelCatalogue } from "./ModelCatalogue";

export function Models() {
  const { data: session } = useSession();
  const mayBuild = session?.user?.capabilities.publish_models ?? false;

  return (
    <>
      <PageHeader
        title="Models"
        description="What each model version covers and what it does not, and -- for a modeller -- the hazard and package work behind it."
      />
      <Tabs
        label="Model sections"
        tabs={[
          {
            id: "catalogue",
            label: "Catalogue",
            content: () => <ModelCatalogue embedded />,
          },
          ...(mayBuild
            ? [
                {
                  id: "hazard",
                  label: "Hazard",
                  content: () => <HazardModels embedded />,
                },
                {
                  id: "build",
                  label: "Build",
                  content: () => <ModelBuild embedded />,
                },
              ]
            : []),
        ]}
      />
    </>
  );
}
