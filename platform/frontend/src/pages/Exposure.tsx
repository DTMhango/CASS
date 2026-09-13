/**
 * Everything that happens to a portfolio before a run uses it.
 *
 * Building a portfolio and reviewing what an import made of it are the same
 * piece of work seen from two ends, and a person moves between them until the
 * data is right. They were two sidebar entries, which read as two steps in a
 * sequence; they are two tabs here, which is what they are.
 */

import { Tabs } from "@/components/Tabs";
import { PageHeader } from "@/components/primitives";

import { ExposureWorkspace } from "./ExposureWorkspace";
import { ImportReview } from "./ImportReview";

export function Exposure() {
  return (
    <>
      <PageHeader
        title="Exposure"
        description="Bring a portfolio in, see how CASS reads it, correct it, then publish a version a run can use."
      />
      <Tabs
        label="Exposure sections"
        tabs={[
          {
            id: "portfolios",
            label: "Portfolios",
            content: () => <ExposureWorkspace embedded />,
          },
          {
            id: "import-review",
            label: "Import review",
            content: () => <ImportReview embedded />,
          },
        ]}
      />
    </>
  );
}
