/** Start here: what CASS is for, how a loss is calculated, and how to find your way around. */

import { Link } from "react-router-dom";

import { Notice } from "@/components/primitives";

import { Card, Step, Steps, Bullets, SectionLink, Terms, GuideTable, Example, Term, GlossaryLink } from "./parts";

export function StartHere() {
  return (
    <div className="guide">
      <Card title="What CASS is for">
        <div className="guide-prose">
          <p>
            CASS stands for the Catastrophe Analytics and Scenario Suite. It
            estimates how much money a group of insured buildings could lose if
            earthquakes strike them.
          </p>
          <p>
            Large earthquakes are rare. Any one insurer may go decades without a
            serious one and then face a very large loss in a single day. Because
            history alone does not contain enough large earthquakes to show what
            could happen, a <strong>catastrophe model</strong> imagines thousands of
            earthquakes that are scientifically plausible, works out the damage each
            one would do to your buildings, and adds up what that damage would cost.
            That gives a picture of both the typical year and the rare, very bad
            year.
          </p>
          <p>
            CASS does that calculation for a portfolio you give it, and shows you
            where the losses come from.
          </p>
        </div>
        <Notice tone="warning" title="CASS is a research tool">
          Its results help people understand earthquake risk and test ideas. They
          are not a basis for setting prices or reserves, even when a reviewer has
          approved them.
        </Notice>
      </Card>

      <Card title="Signing in">
        <div className="guide-prose">
          <p>
            CASS runs in a browser, on an address someone in your organisation will give
            you. Your administrator creates the account and sets its role, which decides
            what you can see; there is no way to sign yourself up.
          </p>
          <p>
            On the sign-in screen, type your <strong>Username</strong> (not an email
            address) and <strong>Password</strong>, then press <strong>Sign in</strong>. The
            eye button beside the password shows what you have typed, which is worth using
            if a sign-in fails. If it still fails, the screen says why; a password nobody
            can remember is an administrator&apos;s job to reset.
          </p>
          <p>
            You stay signed in until you press <strong>Sign out</strong> at the bottom of
            the sidebar, or until the platform ends your session. Work already running
            carries on whether or not you stay signed in.
          </p>
        </div>
      </Card>

      <Card title="How to use this guide">
        <Bullets>
          <li>
            <strong>If you are new</strong>, read this page first, then the{" "}
            <SectionLink section="glossary">Glossary</SectionLink>, then the sections
            in order. Each one builds on the one before.
          </li>
          <li>
            <strong>If you are looking something up</strong>, jump to the section
            you need using the tabs above. You can copy the address from your
            browser to send someone straight to a section.
          </li>
          <li>
            <strong>Numbered steps</strong> are things to do, in order. The grey
            label under a step says where on screen to do it. An arrow means
            &ldquo;then&rdquo;: <em>Models → Build</em> means click{" "}
            <strong>Models</strong> in the sidebar on the left, then the{" "}
            <strong>Build</strong> tab at the top of that page.
          </li>
          <li>
            <strong>Words in bold</strong> are usually the exact text of a button,
            field or heading on the screen, so you can look for them.
          </li>
          <li>
            <strong>Shaded example boxes</strong> use made-up numbers to show how an
            idea works. They are not real results.
          </li>
          <li>
            <strong>&ldquo;For scripting (API)&rdquo; lines</strong> are for people
            who drive CASS from code instead of the screens. You can ignore them.
          </li>
        </Bullets>
      </Card>

      <Card title="How CASS calculates a loss, in plain words">
        <div className="guide-prose">
          <p>
            It helps to know the whole calculation before learning the screens,
            because every screen prepares one part of it. This is the full chain, in
            the order it happens.
          </p>
        </div>
        <ol className="guide-numbered">
          <li>
            <strong>Divide the country into small squares.</strong> Calculating the
            shaking at every single address would take far too long, so the country
            is covered with a grid of small squares called <strong>cells</strong>.
            Each cell is treated as one place.
          </li>
          <li>
            <strong>Imagine many years of earthquakes.</strong> A hazard model
            produced by earthquake scientists is used to invent many years of plausible
            earthquakes: 1,000 of them with the settings CASS starts from. Each imagined earthquake is called an{" "}
            <strong>event</strong>. Most years have only small ones; a few years
            have a very large one.
          </li>
          <li>
            <strong>Work out how hard each event shakes each cell.</strong> The
            shaking in each cell for each event is stored. This record is called the{" "}
            <strong>footprint</strong>.
          </li>
          <li>
            <strong>Put each of your properties in its cell.</strong> CASS reads the
            latitude and longitude of every property and finds which cell it falls
            in.
          </li>
          <li>
            <strong>Turn shaking into damage.</strong> A{" "}
            <strong>vulnerability function</strong> says how badly a particular type
            of building is damaged by a given strength of shaking, as a percentage of
            what it would cost to rebuild. An old brick house is damaged much more
            than a modern building designed for earthquakes.
          </li>
          <li>
            <strong>Turn damage into money.</strong> The damage percentage multiplied
            by the building&apos;s value is the <strong>ground-up loss</strong>: the
            full cost of repair.
          </li>
          <li>
            <strong>Apply the insurance.</strong> The policy&apos;s deductible (the part
            the policyholder pays first) and limit (the most the policy will pay) are
            applied, giving the <strong>insured loss</strong>. Then any
            reinsurance treaties are applied, giving the loss the insurer keeps: the{" "}
            <strong>loss net of reinsurance</strong>.
          </li>
          <li>
            <strong>Add it all up, year by year.</strong> The losses from every event
            in each imagined year are added together. With 1,000 years of totals,
            CASS can say what an average year costs and how bad the worst years are.
          </li>
        </ol>
        <Example title="Example: one warehouse, one imagined earthquake (made-up numbers)">
          <p>
            A warehouse is insured for 1,000,000. Its coordinates place it in cell
            4,512.
          </p>
          <p>
            In imagined year 317, an earthquake shakes cell 4,512 hard. The
            vulnerability function for this kind of warehouse says that level of
            shaking causes, on average, 12% damage. So the ground-up loss is 12% of
            1,000,000, which is <strong>120,000</strong>. (The engine also allows for
            the damage turning out higher or lower than the average.)
          </p>
          <p>
            The policy has a deductible of 20,000, which the owner pays first. So the
            insurer pays 100,000: the <strong>insured loss</strong>.
          </p>
          <p>
            The insurer has a 30% quota share treaty, so a reinsurer pays 30,000 of
            that. The insurer keeps <strong>70,000</strong>: the loss net of
            reinsurance.
          </p>
          <p>
            CASS does this for every property, for every event, in every one of the
            10,000 imagined years.
          </p>
        </Example>
      </Card>

      <Card title="What you need before a loss can be calculated">
        <div className="guide-prose">
          <p>
            The chain above needs four things to exist. The rest of this guide shows
            how each one is made.
          </p>
        </div>
        <GuideTable>
          <thead>
            <tr>
              <th scope="col">What</th>
              <th scope="col">What it is</th>
              <th scope="col">Who makes it, and where it is explained</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">A grid</th>
              <td>The country divided into cells.</td>
              <td>
                A modeller.{" "}
                <SectionLink section="grids">Grids, cells and tiles</SectionLink>.
              </td>
            </tr>
            <tr>
              <th scope="row">A vulnerability set</th>
              <td>The vulnerability functions for every type of building in the country.</td>
              <td>
                A modeller. <SectionLink section="model">Build a model</SectionLink>,
                part 3.
              </td>
            </tr>
            <tr>
              <th scope="row">A hazard set</th>
              <td>The imagined earthquakes and how hard each shakes every cell.</td>
              <td>
                A modeller. <SectionLink section="model">Build a model</SectionLink>,
                part 5.
              </td>
            </tr>
            <tr>
              <th scope="row">A published portfolio</th>
              <td>Your list of properties, checked and then frozen so it cannot change.</td>
              <td>
                An analyst.{" "}
                <SectionLink section="portfolio">Bring in a portfolio</SectionLink>.
              </td>
            </tr>
          </tbody>
        </GuideTable>
        <div className="guide-prose">
          <p>
            The first three are joined together into a <strong>model version</strong>
            . A <strong>run</strong> takes one published portfolio and one model
            version and calculates the losses.
          </p>
        </div>
      </Card>

      <Card title="The three losses a run can report">
        <div className="guide-prose">
          <p>
            The same earthquake damage can be counted in three ways, depending on
            whose money you are looking at. Each way is called a{" "}
            <strong>perspective</strong>. You choose one when you set up a run.
          </p>
        </div>
        <GuideTable>
          <thead>
            <tr>
              <th scope="col">Perspective</th>
              <th scope="col">What it measures</th>
              <th scope="col">What your portfolio must include for it</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Ground-up</th>
              <td>
                The full cost of repairing the damage, as if there were no insurance
                at all.
              </td>
              <td>The properties, where they are, and their values.</td>
            </tr>
            <tr>
              <th scope="row">Insured</th>
              <td>
                The part the insurance policies pay, after the policyholder has paid
                their deductible and after any policy limit is reached.
              </td>
              <td>The policies, with their deductibles and limits.</td>
            </tr>
            <tr>
              <th scope="row">Loss net of reinsurance</th>
              <td>
                The part the insurer is left paying itself, after its own insurers
                (reinsurers) have paid their share.
              </td>
              <td>The policies, and the reinsurance contracts.</td>
            </tr>
          </tbody>
        </GuideTable>
        <div className="guide-prose">
          <p>
            The amount the reinsurers pay is called the <strong>ceded</strong> loss.
            CASS does not show it as a separate number, but you can work it out: it
            is the insured loss minus the loss net of reinsurance.
          </p>
          <p>
            A note if you have used other tools: the loss engine inside CASS labels
            the third perspective <span className="mono">ri</span> (short for
            reinsurance), which makes it look like the amount paid by reinsurers. It
            is actually the amount the insurer keeps. CASS calls it &ldquo;loss net
            of reinsurance&rdquo; to avoid that confusion.
          </p>
        </div>
        <Example title="A real run on this installation">
          <p>
            A portfolio of 64 properties in Jakarta and Bandung, with three
            reinsurance treaties, was run for all three perspectives. It took 96
            seconds. The average yearly losses were: ground-up 566,728; insured
            305,479; net of reinsurance 151,314. So the reinsurers paid 305,479 −
            151,314 = 154,165.
          </p>
          <p>
            Those are real figures from this installation, but they are not exact truth:
            the same book calculated directly in OpenQuake came to 626,732 ground-up, about
            10% more. <SectionLink section="run">Run and read results</SectionLink> explains
            what that means for how much weight a number can carry.
          </p>
        </Example>
      </Card>

      <Card title="The journey, in five stages">
        <Steps>
          <Step number={1} title="Set up the platform" where="An administrator, once for each installation">
            <p>
              Someone installs CASS, starts it, and tells it where to find the
              earthquake building data it needs. Most people never do this: CASS is
              already running when they first sign in. The instructions are in the
              file <span className="mono">platform/docs/running-cass.md</span>.
            </p>
          </Step>
          <Step number={2} title="Build a model version" where="Models → Build and Models → Hazard (modellers only)">
            <p>
              A modeller builds the grid, the vulnerability set and the hazard set for
              a country, and joins them into a model version.{" "}
              <SectionLink section="model">Build a model</SectionLink> walks through
              every step.
            </p>
          </Step>
          <Step number={3} title="Bring in a portfolio" where="Exposure">
            <p>
              An analyst uploads the list of properties, checks and corrects how CASS
              has read it, adds the insurance and reinsurance details, and then
              publishes it so it can be used.{" "}
              <SectionLink section="portfolio">Bring in a portfolio</SectionLink>.
            </p>
          </Step>
          <Step number={4} title="Run it" where="Analysis builder, then Run monitor">
            <p>
              The analyst chooses the portfolio, the model version and the
              perspective, starts the run, and watches it progress.{" "}
              <SectionLink section="run">Run and read results</SectionLink>.
            </p>
          </Step>
          <Step number={5} title="Read the answer" where="Results">
            <p>
              The results show the average yearly loss, how large the rare bad years
              are, which earthquakes and which places caused the most loss, and how
              one run compares with another.
            </p>
          </Step>
        </Steps>
      </Card>

      <Card title="The Portfolio dashboard: where you land">
        <div className="guide-prose">
          <p>
            <Link to="/">Portfolio dashboard</Link> is the first screen after signing in. It
            answers two questions: what is waiting for you, and which project are you
            working in.
          </p>
        </div>
        <Terms>
          <Term term="The five tiles across the top">
            <strong>Projects</strong> you can see, <strong>Runs in progress</strong>,{" "}
            <strong>Awaiting approval</strong> (work sitting at a gate),{" "}
            <strong>Imports to review</strong> (spreadsheets read but not yet turned into
            portfolios) and <strong>Portfolios to resolve</strong> (portfolios with problems
            that stop them being published). A tile showing 0 means nothing is waiting.
          </Term>
          <Term term="Coloured messages">
            Under the tiles, CASS raises anything that needs attention: runs that failed,
            with a link to each; imports waiting for a review; and a warning listing model
            versions that are research prototypes, whose results must not be used for
            decisions.
          </Term>
          <Term term="Projects">
            Every project you can see, with your role in it, how many portfolios it holds and
            how many runs are going. Click a project&apos;s name to work in it; that is what
            fills in the project shown in the header. <strong>New project</strong> creates
            one.
          </Term>
          <Term term="Recent runs">
            The last ten runs, newest first, with their state. Click one to open it in the
            Run monitor.
          </Term>
          <Term term="Portfolios with blocking findings">
            Portfolios that cannot be published until something in their data is fixed, with
            a link straight to the problem.
          </Term>
        </Terms>
      </Card>

      <Card title="What you can do depends on your role">
        <div className="guide-prose">
          <p>
            Every account is given one role. Your role decides which screens and
            buttons you see, so if a step in this guide mentions a button you cannot
            find, your role is the first thing to check. Your role is shown under
            your name at the bottom of the sidebar.
          </p>
        </div>
        <GuideTable>
          <thead>
            <tr>
              <th scope="col">Role</th>
              <th scope="col">What it lets you do</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Portfolio analyst</th>
              <td>
                Create projects, bring in portfolios, run analyses and read results.
                Most day-to-day work happens with this role.
              </td>
            </tr>
            <tr>
              <th scope="row">Underwriter</th>
              <td>The same screens as a portfolio analyst.</td>
            </tr>
            <tr>
              <th scope="row">Catastrophe modeller</th>
              <td>
                Everything an analyst can do, and also build models: the{" "}
                <strong>Hazard</strong> and <strong>Build</strong> tabs under Models,
                where grids, vulnerability sets, hazard runs and model versions are
                made.
              </td>
            </tr>
            <tr>
              <th scope="row">Reviewer</th>
              <td>
                Check other people&apos;s work and approve or reject it at the points
                where CASS requires a second person&apos;s agreement (called gates), and
                approve results. Nobody, in any role, can approve their own request.
              </td>
            </tr>
            <tr>
              <th scope="row">Platform administrator</th>
              <td>
                Everything the other roles can do, and also manage user accounts, the
                calculation engines, storage, and the record of who did what.
              </td>
            </tr>
          </tbody>
        </GuideTable>
      </Card>

      <Card title="Finding your way around the screen">
        <div className="guide-prose">
          <p>
            Every screen has the same frame around it, so you always know what you
            are working on.
          </p>
        </div>
        <Terms>
          <Term term="The sidebar (left)">
            The main areas of CASS, listed in the order work usually happens:
            Portfolio dashboard, Exposure, Analysis builder, Run monitor, Results,
            Models, Administration. At the bottom are your name, your role and the{" "}
            <strong>Sign out</strong> button. On a narrow screen the sidebar shrinks to
            icons only; hold the pointer over an icon to see what that area is for.
          </Term>
          <Term term="The header (top)">
            On the left, the project you are working in. On the right, the link to
            this <strong>User guide</strong>.
          </Term>
          <Term term="The bar under the header">
            Four items that tell you what the numbers on the screen belong to. Each
            is chosen on a particular screen and then remembered, even if you reload
            the page or come back tomorrow.
          </Term>
          <Term term="Project (in the header)">
            A project is a folder for one piece of work: it holds the portfolios, runs
            and results that belong together. You must choose a project before you can
            do anything else. On the <Link to="/">Portfolio dashboard</Link>, click
            the name of a project to choose it, or press{" "}
            <strong>New project</strong> to make one. A new project needs a name, a
            short reference (used to label its files, so keep it short and do not
            change it later) and a purpose.
          </Term>
          <Term term="Model version (in the bar)">
            The model the next run will use. You choose it in the{" "}
            <Link to="/analysis">Analysis builder</Link> or in the{" "}
            <Link to="/models">Models</Link> catalogue. Beside it is a badge: a green{" "}
            <strong>Approved</strong> badge means the model is finished and its results may
            be approved for decisions, and a yellow <strong>Research only</strong> badge
            means it is not, so its results cannot be. Today every model version built in
            CASS carries the yellow badge.
          </Term>
          <Term term="Portfolio (in the bar)">
            The list of properties you are working on, and which version of it. You
            choose it in <Link to="/exposure">Exposure → Portfolios</Link> or in the
            Analysis builder. It shows <strong>Draft</strong> while it can still be
            changed and <strong>Published</strong> once it is frozen. Only a published
            portfolio can be run.
          </Term>
          <Term term="Perspective (in the bar)">
            Which of the three losses you are looking at: ground-up, insured, or net
            of reinsurance. You choose it in the Analysis builder.
          </Term>
          <Term term="Run state (in the bar)">
            Whether a calculation is running in this project right now, and which
            step it has reached.
          </Term>
        </Terms>
      </Card>

      <Card title="One limit to know before you start">
        <Notice tone="warning" title="CASS can only calculate a loss where earthquake shaking has been calculated">
          <p>
            Building a grid and a vulnerability set for a country takes a few minutes.
            Calculating the shaking (the hazard) for a whole country takes hours of
            computer time, so it is only done where it is needed.
          </p>
          <p>
            At the moment, shaking has been calculated for one area only: Jakarta and
            Bandung in Indonesia. That area is 962 cells out of the 52,831 cells that
            cover all of Indonesia.
          </p>
          <p>
            A property outside that area is marked{" "}
            <GlossaryLink term="Domain">
              <strong>outside the domain</strong>
            </GlossaryLink>
            , meaning outside the ground the model covers. This is deliberate. CASS could have quietly given those properties a loss
            of zero, but that would make the total look smaller than it really is
            without anyone noticing. Instead it tells you they could not be modelled.
          </p>
          <p>
            In practice: a portfolio in Jakarta and Bandung can be run from start to
            finish today. A portfolio spread across Indonesia needs the shaking
            calculated for the whole country first. A portfolio in another country
            needs a hazard model for that country.
          </p>
        </Notice>
      </Card>
    </div>
  );
}
