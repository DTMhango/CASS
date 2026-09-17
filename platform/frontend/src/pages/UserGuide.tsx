/**
 * The user guide, inside the application.
 *
 * A guide kept outside the product goes out of date the moment a screen
 * changes, and it has to be found and passed around before anyone can read it.
 * This one ships with the screens it describes, so changing a screen and
 * changing its instructions happen in the same commit, and anyone signed in
 * can open it from the header.
 *
 * **Who it is written for.** Someone who has never used a catastrophe model.
 * That reader needs more than a definition: they need to know why a thing
 * exists, what it looks like in practice, and what goes wrong without it. So
 * the text explains in full sentences, gives an example wherever one helps,
 * and never assumes a term was understood because it was defined once. A
 * shorter guide would read faster to someone who already knows the subject and
 * would leave a beginner guessing, which is the failure this page exists to
 * prevent. Keep that in mind when editing it.
 *
 * **Keeping it true.** Every button, field and card named here is named as the
 * screen names it. When a screen's wording changes, change it here too.
 *
 * The sections are tabs, like every other area (ADR 13), so a link can open one
 * directly: /guide?section=grids. Each section is its own file under ./guide,
 * so adding to one does not mean scrolling past the rest.
 */

import { useEffect } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import { Tabs } from "@/components/Tabs";
import { PageHeader } from "@/components/primitives";

import { Administration } from "./guide/Administration";
import { BuildModel } from "./guide/BuildModel";
import { Glossary } from "./guide/Glossary";
import { Grids } from "./guide/Grids";
import { Portfolio } from "./guide/Portfolio";
import { Problems } from "./guide/Problems";
import { RunAndRead } from "./guide/RunAndRead";
import { StartHere } from "./guide/StartHere";
import { GuideSearch } from "./guide/search/GuideSearch";

import "./UserGuide.css";

export function UserGuide() {
  const [params] = useSearchParams();
  const section = params.get("section");
  const { hash, key } = useLocation();

  // A link to a definition, a step or a topic opens its section, and the
  // browser will not scroll to it by itself: the element does not exist until
  // that section has rendered. Without one, a link from deep in one section
  // would otherwise open the next already scrolled to where the last was being
  // read.
  //
  // What was asked for is then marked for a moment and given focus, so the eye
  // and the keyboard both land on it rather than somewhere near it. Marking is
  // done here rather than with :target, which never updates when the address
  // changes without a page load.
  //
  // `key` is in the dependencies so that asking for the same thing twice --
  // searching for a definition you are already reading -- scrolls to it again
  // instead of appearing to do nothing.
  useEffect(() => {
    const target = hash ? document.getElementById(hash.slice(1)) : null;
    if (!target) {
      document.getElementById("main")?.scrollTo?.({ top: 0 });
      return;
    }
    target.scrollIntoView?.({ block: "start" });
    target.classList.add("guide-found");
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus?.({ preventScroll: true });
    const timer = window.setTimeout(() => target.classList.remove("guide-found"), 2400);
    return () => {
      window.clearTimeout(timer);
      target.classList.remove("guide-found");
    };
  }, [section, hash, key]);

  return (
    <>
      <PageHeader
        title="User guide"
        description="How to use CASS from start to finish, what each screen is for, and what the words on them mean. Written for people new to catastrophe modelling."
      />
      <GuideSearch />
      {/* Wrapped so the guide's tab strip can be styled on its own: it carries
          more sections than any other area, and a strip that scrolled sideways
          would hide the last of them behind an edge nobody thinks to drag. */}
      <div className="user-guide">
        <Tabs
          label="Guide sections"
          parameter="section"
          tabs={[
            { id: "start", label: "Start here", content: () => <StartHere /> },
            { id: "glossary", label: "Glossary", content: () => <Glossary /> },
            { id: "grids", label: "Grids, cells and tiles", content: () => <Grids /> },
            { id: "model", label: "Build a model", content: () => <BuildModel /> },
            { id: "portfolio", label: "Bring in a portfolio", content: () => <Portfolio /> },
            { id: "run", label: "Run and read results", content: () => <RunAndRead /> },
            { id: "administration", label: "Administration", content: () => <Administration /> },
            { id: "problems", label: "When something goes wrong", content: () => <Problems /> },
          ]}
        />
      </div>
    </>
  );
}
