/** Run and read results: setting up a run, watching it, and reading what it produced. */

import { Link } from "react-router-dom";

import {
  Card,
  Step,
  Steps,
  Bullets,
  Api,
  SectionLink,
  Terms,
  GuideTable,
  Term,
  GlossaryLink,
  Example,
} from "./parts";

export function RunAndRead() {
  return (
    <div className="guide">
      <Card title="First, check the model fits your question">
        <div className="guide-prose">
          <p>
            Before setting up a run, open <Link to="/models">Models</Link>, which opens on{" "}
            <strong>Catalogue</strong>. Every analyst can see it, and it is the one screen
            that tells you what a model version can and cannot answer. Each model version is
            a card holding:
          </p>
        </div>
        <Terms>
          <Term term="Country, Peril, Version and Intensity measures">
            What it covers. The country must match your portfolio: a model version covers one
            country only.
          </Term>
          <Term term="Publication state and Last validated">
            Whether it is a finished model or a research prototype, and when it was last
            checked end to end.
          </Term>
          <Term term="Outstanding before this may be published as a full country model">
            Everything unfinished about it, listed rather than hidden. Read this before using
            its numbers for anything.
          </Term>
          <Term term="Exposure this version cannot model">
            The kinds of building in your portfolio it has no vulnerability function for. If
            a lot of your value sits in those, the model is answering less of your portfolio
            than it appears to.
          </Term>
          <Term term="Peril scope statement">
            Which sub-perils of the earthquake are included and which are not, with the
            reason for each. CASS models shaking, so tsunami, fire following, liquefaction
            and landslide damage is not in the numbers. If a model version has no scope
            statement, the card says so in red: its results cannot honestly be called
            earthquake loss.
          </Term>
        </Terms>
        <div className="guide-prose">
          <p>
            Press <strong>Select</strong> on the card you want. The button then reads{" "}
            <strong>Selected</strong>, and that model version appears in the bar under the
            header, ready for the run.
          </p>
        </div>
      </Card>

      <Card title="Set up and start a run">
        <div className="guide-prose">
          <p>
            A run calculates the losses for one published portfolio using one model
            version. Everything is set up on one page, the{" "}
            <Link to="/analysis">Analysis builder</Link>, in the card{" "}
            <strong>What this run will use</strong>.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Check the project" where="The header">
            <p>
              The run will belong to the project named in the header at the top of the
              screen. If it is the wrong project, choose the right one on the Portfolio
              dashboard first.
            </p>
          </Step>
          <Step number={2} title="Choose the portfolio" where="Analysis builder → Portfolio">
            <p>
              Only published portfolios can be chosen. Drafts are shown greyed out, with a
              note to publish them first. This makes sure the data cannot change while the
              run is using it.
            </p>
          </Step>
          <Step number={3} title="Choose the model version" where="Model version">
            <p>
              Choose the model for the country your portfolio is in. A model version marked as
              a research prototype (published with some work still unfinished) can be run, and
              its results are useful for research, but they cannot be approved.
            </p>
          </Step>
          <Step number={4} title="Choose the assumption set, if there is a choice" where="Assumption set">
            <p>
              An assumption set decides how CASS fills in unknown building details
              (construction, height and design level). Unless you have a reason to choose
              another, leave it on <strong>Baseline weights the model was built with</strong>
              . If the model version has no other assumption sets, this box cannot be
              changed.
            </p>
            <p>
              Changing the assumption set only changes the damage. Which cell each property
              is in, and its value, stay the same.
            </p>
          </Step>
          <Step number={5} title="Choose what the run is for" where="What this run is for">
            <p>The choice decides how far the run goes and what its results may be used for:</p>
            <GuideTable>
              <thead>
                <tr>
                  <th scope="col">Choice</th>
                  <th scope="col">When to use it</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Geometry only — map the book, calculate no loss</th>
                  <td>
                    To check which properties the model can place in a cell, before spending
                    time on a full calculation. It calculates no loss. This is a good first
                    run for any new portfolio.
                  </td>
                </tr>
                <tr>
                  <th scope="row">KRE-share technical loss</th>
                  <td>
                    An ordinary loss calculation on the share of each risk KRE holds. The
                    results are for research.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Portfolio-loss research</th>
                  <td>
                    A loss calculation for comparing alternatives, such as different
                    assumption sets. The results are for research.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Decision use</th>
                  <td>
                    A calculation whose result a reviewer may approve. It is only available
                    with a finished model version (not a research prototype) and an approved
                    assumption set; otherwise it shows &ldquo;not yet available&rdquo;.
                  </td>
                </tr>
              </tbody>
            </GuideTable>
          </Step>
          <Step number={6} title="Choose the perspective" where="Financial perspective">
            <p>
              Choose which loss to calculate: ground-up, insured, or net of reinsurance.
              Ground-up needs only the locations. Insured also needs the policies.
              Net of reinsurance also needs the reinsurance contracts. The checklist in step
              8 tells you if your portfolio does not have what the choice needs.
            </p>
            <p>
              For net of reinsurance, a second field appears: <strong>Reinsurance cover</strong>.
              Leave it at <strong>As the engine applies it</strong> for the loss engine&apos;s
              own figure, or choose <strong>Limited by contract terms</strong> to have CASS also
              apply each catastrophe layer&apos;s reinstatements and their premiums. The card{" "}
              <strong>Reinstatements: limited cover</strong> below explains the difference.
            </p>
          </Step>
          <Step number={7} title="Choose the computing power, and name the run" where="Resource profile and Run name">
            <p>
              Each <strong>Resource profile</strong> shows how many processors and how much
              memory it gives, its time limit, and how many runs it has room for right now.
              The default is usually fine.
            </p>
            <p>
              Give the run a <strong>Run name</strong> you will recognise later, such as
              &ldquo;Q3 portfolio, baseline&rdquo;. If you leave it empty, the portfolio&apos;s
              name and version are used.
            </p>
          </Step>
          <Step number={8} title="Check the checklist" where="Before this run may be submitted">
            <p>This card checks four things:</p>
            <Bullets>
              <li>a project is chosen;</li>
              <li>the portfolio is published;</li>
              <li>a model version is chosen;</li>
              <li>the portfolio has the information the chosen perspective needs.</li>
            </Bullets>
            <p>
              Anything not yet done has a link that takes you to the screen where you can do
              it.
            </p>
          </Step>
          <Step number={9} title="Start the run" where="Submit analysis">
            <p>
              Press <strong>Submit analysis</strong>. The run is added to the queue and you
              are taken to the Run monitor. You can close your browser; the run carries on
              without it.
            </p>
          </Step>
        </Steps>
        <Api>{"POST /api/v1/analysis-runs/ · POST /api/v1/analysis-runs/{id}/submit/"}</Api>
      </Card>

      <Card title="Watch the run">
        <div className="guide-prose">
          <p>
            The <Link to="/runs">Run monitor</Link> shows how long the run has been going
            and, for the step it is on, how far through that step the engine reports it is.
            Neither of these is a prediction of when it will finish.
          </p>
          <p>
            A run goes through these steps in order. You do not need to do anything during
            most of them, but knowing what each does helps if a run stops. The first column
            is the name the screen shows; the short name in the second is what appears in a
            failure message and in the run&apos;s event log.
          </p>
        </div>
        <GuideTable>
          <thead>
            <tr>
              <th scope="col">Step, as the Run monitor names it</th>
              <th scope="col">Short name</th>
              <th scope="col">What happens</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Validate exposure</th>
              <td className="mono">validate_exposure</td>
              <td>The portfolio is checked again.</td>
            </tr>
            <tr>
              <th scope="row">Apply assumptions</th>
              <td className="mono">enrich</td>
              <td>Missing building details are filled in using the assumption set, and CASS records how much of the data is assumed rather than stated in your spreadsheet.</td>
            </tr>
            <tr>
              <th scope="row">Publish OED</th>
              <td className="mono">publish_oed</td>
              <td>The portfolio files are frozen. CASS checks that Oasis is serving this model version&apos;s package, and converts a portfolio in another currency at an approved rate.</td>
            </tr>
            <tr>
              <th scope="row">Run keys lookup</th>
              <td className="mono">keys</td>
              <td>Each property is matched to its cell, and to the vulnerability function for its type of building.</td>
            </tr>
            <tr>
              <th scope="row">Reconcile keys</th>
              <td className="mono">reconcile_keys</td>
              <td>CASS checks that the matched value, the value deliberately left out and the value it could not match add up to the portfolio&apos;s total. Value it could not match pauses the run (see below).</td>
            </tr>
            <tr>
              <th scope="row">Generate Oasis files</th>
              <td className="mono">generate_inputs</td>
              <td>The files Oasis needs are written.</td>
            </tr>
            <tr>
              <th scope="row">Validate Oasis files</th>
              <td className="mono">validate_inputs</td>
              <td>Those files are checked.</td>
            </tr>
            <tr>
              <th scope="row">Pre-loss smoke check</th>
              <td className="mono">smoke</td>
              <td>
                A quick trial with only a few of the largest earthquakes, to catch a broken
                setup: the <GlossaryLink term="Smoke check">smoke check</GlossaryLink>.
              </td>
            </tr>
            <tr>
              <th scope="row">Calculate losses</th>
              <td className="mono">losses</td>
              <td>The full loss calculation. This is usually the longest step.</td>
            </tr>
            <tr>
              <th scope="row">Collect results</th>
              <td className="mono">collect</td>
              <td>The losses are gathered into results.</td>
            </tr>
            <tr>
              <th scope="row">Result review</th>
              <td className="mono">review</td>
              <td>The results are checked before they are shown.</td>
            </tr>
          </tbody>
        </GuideTable>
        <Steps>
          <Step number={1} title="If the run pauses at the matching check" where="Keys reconciliation">
            <p>
              The <strong>Keys reconciliation</strong> card shows how much value was matched,
              how much was deliberately left out, how much could not be matched, and the
              portfolio&apos;s total. If some value could not be matched, the run pauses and
              shows <strong>Unmapped value is waiting to be accepted</strong>.
            </p>
            <p>
              This pause is on purpose: otherwise those properties would silently add nothing
              to the loss. To continue, write under <strong>Why the run should go on</strong>{" "}
              why the missing value is acceptable (at least a sentence), and press{" "}
              <strong>Ask for an exception</strong>. A reviewer who is not you then approves
              or rejects it. Once approved, press <strong>Resume the run</strong>. If you
              would rather fix the data, the <SectionLink section="problems">When something
              goes wrong</SectionLink> section explains the common causes.
            </p>
          </Step>
          <Step number={2} title="Download files the run produced, if you need them" where="Evidence → Download">
            <p>
              The <strong>Evidence</strong> card lists the files the run produced. Each
              download is recorded.
            </p>
          </Step>
          <Step number={3} title="Try again, or stop a run" where="Retry and Cancel">
            <p>
              <strong>Cancel</strong> stops a run that is still going.{" "}
              <strong>Retry</strong> starts a failed run again as a new run. The history of
              the original run is kept unchanged, in <strong>Stage history</strong>, so you
              can always see what happened.
            </p>
          </Step>
        </Steps>
      </Card>

      <Card title="Read the results">
        <Steps>
          <Step number={1} title="Find the result" where="Results">
            <p>
              Each perspective of each finished run appears as its own result. A badge beside
              its name shows its status: <strong>Research only</strong> (from a research
              prototype, so it cannot be approved), <strong>Awaiting review</strong> (could be
              approved but has not been), or <strong>Approved</strong>.
            </p>
          </Step>
          <Step number={2} title="Read the main numbers">
            <p>At the top of each result:</p>
            <Bullets>
              <li>
                <strong>Average annual loss</strong>: the loss to expect in an average year.
                This is the most reliable number.
              </li>
              <li>
                <strong>Standard deviation</strong>: how much the yearly loss varies.
              </li>
              <li>
                Two <GlossaryLink term="Return period">return-period</GlossaryLink> losses,
                for example the 100-year loss: a loss that large or larger has a 1% chance of
                happening in any one year.
              </li>
            </Bullets>
            <p>
              Below them is the exceedance probability (EP) curve, a chart of how likely each
              size of loss is. Open <strong>Full exceedance probability table</strong> to see
              the loss at every return period.
            </p>
          </Step>
          <Step number={3} title="See what is behind the numbers">
            <p>Open these sections to understand where the loss comes from:</p>
            <Bullets>
              <li>
                <strong>Events behind this number</strong>: the imagined earthquakes that
                caused the most loss.
              </li>
              <li>
                <strong>Where the loss is</strong>: the average annual loss in each cell,
                showing which places contribute most.
              </li>
              <li>
                <strong>Scenario range</strong>: how much the answer changes under different
                assumption sets.
              </li>
              <li>
                <strong>What this number rests on</strong>: the model version, assumption
                set, run mode, valuation date, perspective, currency (with the rate, if the
                portfolio had to be converted) and approval status, then what is not
                included, the quality of the data, and{" "}
                <strong>Where the uncertainty comes from</strong>.
              </li>
            </Bullets>
          </Step>
          <Step number={4} title="Compare two runs" where="Compare two runs">
            <p>
              Choose a starting result (the baseline) and a result to compare with it. CASS
              shows how the numbers differ and lists what changed between the two runs that
              could explain it, such as a different model version or assumption set.
            </p>
          </Step>
          <Step number={5} title="Export or approve" where="Export package and Approve for decision use">
            <p>
              <strong>Export package</strong> downloads the numbers together with a record of
              exactly how they were produced, so someone else can trace them.
            </p>
            <p>
              A reviewer can press <strong>Approve for decision use</strong> on a result from
              a finished model version. An approved result is frozen; running again creates a
              new result rather than changing it.
            </p>
          </Step>
        </Steps>
      </Card>

      <Card title="Reinstatements: limited cover">
        <div className="guide-prose">
          <p>
            A catastrophe excess of loss layer pays up to its limit for an event. Once it has
            paid, it is <strong>reinstated</strong> (its limit restored) for the rest of the
            year, but only as many times as the contract allows, and each reinstatement costs
            a premium. The loss engine does not model this: it pays every layer in full on
            every event, however many events a year holds, as though reinstatements were
            unlimited and free. That can make the loss kept look smaller than it is in years
            with several large earthquakes.
          </p>
          <p>
            When you choose <strong>Limited by contract terms</strong>, CASS works through
            each imagined year in turn and applies every layer&apos;s real terms: its
            reinstatements (from the <strong>Reinstatements</strong> column), the rate of each
            (<strong>Reinstatement rate</strong>) and the premium they are charged on (
            <strong>Reinstatement premium</strong>, normally the layer&apos;s MDP at 100%). The
            premium for restoring part of the layer is taken off the recovery it restores.
          </p>
        </div>
        <Example title="One layer, two earthquakes in a year">
          <p>
            A layer of 10,000,000 in excess of 5,000,000 has one reinstatement at 100% on a
            premium of 1,000,000. In one year, two earthquakes put 12,000,000 and then
            20,000,000 into it.
          </p>
          <GuideTable>
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">Recovered</th>
                <th scope="col">Reinstatement premium</th>
                <th scope="col">Net recovered</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>First</td>
                <td>7,000,000</td>
                <td>7/10 × 1,000,000 = 700,000</td>
                <td>6,300,000</td>
              </tr>
              <tr>
                <td>Second</td>
                <td>10,000,000</td>
                <td>only 3,000,000 of reinstatement is left: 300,000</td>
                <td>9,700,000</td>
              </tr>
            </tbody>
          </GuideTable>
          <p>
            The layer has now paid 17,000,000 of its 20,000,000 for the year, so a third
            earthquake could recover at most 3,000,000. The loss engine would show 17,000,000
            recovered and no premium, and would pay a third earthquake in full.
          </p>
        </Example>
        <div className="guide-prose">
          <p>
            The result appears as a second net-of-reinsurance result, labelled{" "}
            <strong>limited cover</strong>, beside the engine&apos;s own. Its panel shows, for
            each layer, what it recovers a year with its limits and as the engine pays it, the
            reinstatement premium a year, and how often it runs out of cover. It also shows a
            check: CASS works out the engine&apos;s figure itself, from the same losses, and
            says whether it matches. If it does not, the panel says so in amber, and the
            result should not be relied on until the difference is understood.
          </p>
          <p>Four things to know:</p>
        </div>
        <Bullets>
          <li>
            It applies to programmes of catastrophe excess of loss contracts only. A quota
            share or surplus share in the programme is refused with the reason, before the
            run starts.
          </li>
          <li>
            A layer whose Reinstatements cell is blank is applied as the engine applies it,
            and the result lists it. Write 0 for a layer that pays its limit once a year.
          </li>
          <li>
            Within a year, earthquakes are applied in the order of their event numbers,
            because the imagined catalogue does not give them dates. This can change the
            largest single loss of a year once a layer runs out, but never the year&apos;s
            total.
          </li>
          <li>
            A warranty such as &ldquo;two or more risks must be involved&rdquo; is not
            modelled, so recoveries from an earthquake that damages only one property may be
            overstated.
          </li>
        </Bullets>
      </Card>

      <Card title="Reading the numbers with care">
        <div className="guide-prose">
          <p>Four things are easy to miss and change how much weight a number can bear.</p>
        </div>
        <Bullets>
          <li>
            <strong>CASS reads about 10% lower than the engine it is checked against.</strong>{" "}
            The one book measured both ways — the 64-location Jakarta&ndash;Bandung
            portfolio — gave an average annual loss of 566,728 through CASS against 626,732
            calculated directly in OpenQuake from the same ground shaking: a ratio of 0.904.
            At individual return periods the two ranged from 0.70 to 1.26 of each other.
            Nobody has yet agreed how close is close enough, so this is recorded rather than
            passed or failed. Treat a CASS loss as an estimate with roughly that much room
            around it, not as a figure accurate to the last pound.
          </li>
          <li>
            <strong>Trust the average more than the extremes.</strong> A 1-in-1,000-year loss
            is read from the worst imagined years: from the default 10,000 imagined years it
            is the tenth-worst, and from a hazard set made with only 1,000 years it is the
            single worst, so a different set of imagined years could give quite different
            figures. The screen that sets up a hazard calculation shows how many years each
            return period rests on. The average annual loss uses every year, so it is much
            steadier.
          </li>
          <li>
            <strong>Ground with no measurement is treated as rock.</strong> Where the hazard
            model has no soil measurement within 15 km of a cell, CASS assumes rock. Rock
            shakes less than soft ground, so this can make losses too low on soft ground.
            Across Indonesia, about 79% of cells have no nearby measurement. The hazard
            run&apos;s site report shows how much of the grid this affected.
          </li>
          <li>
            <strong>An approximate address gives an approximate cell.</strong> A property
            located only to its town or postcode could really be in a neighbouring cell with
            different shaking. The geocoding sensitivity report shows how much value this
            affects.
          </li>
          <li>
            <strong>&ldquo;Net of reinsurance&rdquo; is what the insurer keeps.</strong> The
            amount the reinsurers pay is the insured loss minus this figure. The engine&apos;s
            own figure treats reinstatements as unlimited and free; the limited-cover result
            does not.
          </li>
        </Bullets>
      </Card>
    </div>
  );
}
