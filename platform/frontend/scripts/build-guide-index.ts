/**
 * Writes the guide's search index from the guide's own sections.
 *
 * Run it after changing the guide: `npm run guide:index`. Nothing runs this
 * automatically, because a generated file that appears during a build is a file
 * nobody reviews; instead a test compares the committed index with what the
 * sections now say, so forgetting fails the build with this command in the
 * message.
 */

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { buildGuideIndex, renderIndexModule } from "../src/pages/guide/search/indexer";

const pages = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "pages");
const guideDirectory = join(pages, "guide");

const sections = Object.fromEntries(
  readdirSync(guideDirectory)
    .filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.tsx"))
    .map((name) => [name.replace(/\.tsx$/, ""), readFileSync(join(guideDirectory, name), "utf8")]),
);

const entries = buildGuideIndex(readFileSync(join(pages, "UserGuide.tsx"), "utf8"), sections);
const target = join(guideDirectory, "search", "index.generated.ts");
writeFileSync(target, renderIndexModule(entries), "utf8");

const counts = entries.reduce<Record<string, number>>(
  (tally, entry) => ({ ...tally, [entry.kind]: (tally[entry.kind] ?? 0) + 1 }),
  {},
);
const sectionCount = new Set(entries.map((entry) => entry.section)).size;
console.log(
  `Indexed ${entries.length} things across ${sectionCount} sections ` +
    `(${counts.term ?? 0} definitions, ${counts.step ?? 0} steps, ${counts.card ?? 0} cards).`,
);
