/** Build a model: the GEM release, vulnerability set, hazard, assembly and package. */

import { Link } from "react-router-dom";

import { Notice } from "@/components/primitives";

import { Card, Step, Steps, Bullets, Api, SectionLink, GuideTable, Example, Terms, Term } from "./parts";

export function BuildModel() {
  return (
    <div className="guide">
      <Card title="Before you start">
        <div className="guide-prose">
          <p>
            A <strong>model version</strong> is the complete earthquake model for one
            country. It is made of three parts, and this section builds them in order:
          </p>
        </div>
        <Bullets>
          <li>
            a <strong>grid</strong>, which divides the country into cells;
          </li>
          <li>
            a <strong>vulnerability set</strong>, which says how much damage shaking does
            to each type of building;
          </li>
          <li>
            a <strong>hazard set</strong>, which holds the imagined earthquakes and how
            hard each one shakes every cell.
          </li>
        </Bullets>
        <div className="guide-prose">
          <p>
            The grid and the vulnerability set each take a few minutes. The hazard set
            comes from a calculation that can take from a few minutes to several hours,
            depending on how much of the country it covers.
          </p>
        </div>
        <Notice tone="info" title="Who can do this">
          Only catastrophe modellers and administrators can see the{" "}
          <Link to="/models?tab=hazard">Hazard</Link> and{" "}
          <Link to="/models?tab=build">Build</Link> tabs. Step 8 also needs a reviewer.
          Everyone can see the <Link to="/models">Catalogue</Link> tab, which lists the
          model versions available to run.
        </Notice>
        <div className="guide-prose">
          <p>The order to work in, with the reason for it:</p>
        </div>
        <ol className="guide-numbered">
          <li>
            <strong>Choose the GEM release</strong>, because the vulnerability set is built
            from it.
          </li>
          <li>
            <strong>Build the grid</strong>, because the hazard is calculated on its cells.
          </li>
          <li>
            <strong>Build the vulnerability set.</strong>
          </li>
          <li>
            <strong>Assemble the model version</strong> from the grid and the vulnerability
            set.
          </li>
          <li>
            <strong>Upload and run the hazard model</strong> on the same grid.
          </li>
          <li>
            <strong>Attach the hazard set</strong> the calculation produced to the model
            version.
          </li>
          <li>
            <strong>Publish the model version</strong>, so it can be chosen for runs.
          </li>
          <li>
            <strong>Get approval and build the Oasis package</strong>, which is the form
            the loss engine can read.
          </li>
        </ol>
      </Card>

      <Card title="Part 1: Choose the GEM release">
        <div className="guide-prose">
          <p>
            CASS takes its information about buildings and their vulnerability from
            GEM&apos;s published data, which is kept as a folder on the computer CASS runs
            on. This part tells CASS which copy to use. It usually only needs doing once.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Find the GEM release card" where="Models → Build → GEM release">
            <p>
              The card lists every GEM release CASS has found. For each one it shows how
              many countries it contains and whether it is v2026.0.0, the release CASS has
              been checked against. A release marked as unusable cannot be chosen, and the
              card says why.
            </p>
          </Step>
          <Step number={2} title="Choose the release" where="Use this release">
            <p>
              Press <strong>Use this release</strong> beside the one you want. The button
              changes to <strong>In use</strong>. The change takes effect straight away;
              nothing needs restarting.
            </p>
            <p>
              If the release you want is not listed, type its folder location in{" "}
              <strong>Or name the folder</strong> and press{" "}
              <strong>Use this folder</strong>. Ask your administrator for the location if
              you do not know it.
            </p>
          </Step>
        </Steps>
        <Notice tone="warning" title="If no release is chosen">
          CASS cannot build a vulnerability set, and the vulnerability card will say that
          no GEM release is configured.
        </Notice>
      </Card>

      <Card title="Part 2: Build the grid">
        <div className="guide-prose">
          <p>
            Use the <strong>Build a grid</strong> card on the Build tab. Grids have their
            own section in this guide, which explains what tiles and cells are and walks
            through every field:{" "}
            <SectionLink section="grids">Grids, cells and tiles</SectionLink>.
          </p>
        </div>
      </Card>

      <Card title="Part 3: Build the vulnerability set">
        <div className="guide-prose">
          <p>
            GEM publishes, for each country, the types of buildings found there, how
            common each type is, and vulnerability functions for each type. What GEM
            cannot know is how well buildings of different ages were designed for
            earthquakes, because that depends on when that country introduced and enforced
            its building codes. This part combines GEM&apos;s data with that local
            knowledge, which you provide.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Choose the country" where="Models → Build → Build a vulnerability set → Country in the GEM release">
            <p>
              Click the box and start typing the country&apos;s name or its ISO code, then
              choose it from the list. The list comes from the GEM release itself. The
              country codes are then filled in automatically from GEM&apos;s data, so the
              set cannot be saved under the wrong country by mistake.
            </p>
            <p>
              208 of the 215 countries in GEM v2026.0.0 can be built. Seven cannot: the
              United States, Canada, Puerto Rico, the US Virgin Islands, Guam, American
              Samoa and the Northern Mariana Islands. They appear greyed out with the
              reason. GEM describes the buildings in those places using a different
              classification system (called HAZUS), and CASS does not yet have a way to
              translate it.
            </p>
          </Step>
          <Step number={2} title="Name the enrichment and give it a version" where="Enrichment name and Enrichment version">
            <p>
              The <strong>enrichment</strong> is everything you state on this form that
              GEM does not supply: the design eras and the reason for each, or your
              stated reason for having none, the stock weighting, and the open questions.
              These two boxes label that set of assumptions so that a loss can later be
              traced back to it. They are the only two fields here you have to fill in
              yourself.
            </p>
            <Bullets>
              <li>
                <strong>Enrichment name</strong> is a label for the assumptions, and any
                text will do. Something short that says what they rest on reads best:{" "}
                <span className="mono">is_design_eras</span> for a set built on
                Iceland&apos;s code history. The two countries CASS was piloted on use{" "}
                <span className="mono">id_gem_stock</span> and{" "}
                <span className="mono">np_gem_stock</span>. The name is recorded with the
                build and in the audit trail; it is the version, below, that names the set
                afterwards.
              </li>
              <li>
                <strong>Enrichment version</strong> becomes the version of the
                vulnerability set, with <span className="mono">-gem</span> added to show
                the functions came from GEM. Type{" "}
                <span className="mono">0.1.0-draft</span> and the set is listed as{" "}
                <span className="mono">0.1.0-draft-gem</span> — which is how you will
                recognise it in Assemble a model version and in the catalogue. Keep it
                shorter than 28 characters, and prefer numbers you can raise, such as{" "}
                <span className="mono">0.1.0</span> then <span className="mono">0.2.0</span>.
              </li>
            </Bullets>
            <Notice tone="warning" title="Building again under the same version replaces the set">
              A set is identified by its country and version together, so building Iceland
              again at <span className="mono">0.1.0-draft</span> overwrites the Iceland set
              of that version rather than adding a second one. The functions change
              underneath anything already built on it, and there is no copy of what was
              there before. So whenever you change an era, change the reason for one, or
              change the stock weighting, raise the version. Two versions can then be
              compared, and a run made under the old assumptions can still be told apart
              from one made under the new.
            </Notice>
          </Step>
          <Step number={3} title="Choose the stock weighting" where="Stock weighting">
            <p>
              When CASS does not know exactly what type a building is, it blends the
              possible types using GEM&apos;s estimate of how common each is. This setting
              decides how &ldquo;common&rdquo; is measured.{" "}
              <strong>Replacement cost</strong>, the default, counts each type in proportion
              to what those buildings would cost to rebuild. <strong>Building count</strong>{" "}
              counts each building once, regardless of value. For insurance losses,
              replacement cost is usually the right choice, because it reflects where the
              money is.
            </p>
          </Step>
          <Step number={4} title="Choose how design levels are decided" where="How a design level is decided">
            <p>
              GEM has a separate vulnerability function for each seismic design level: no
              design (CDN), low (CDL), moderate (CDM) and high (CDH). CASS needs a way to
              decide which levels apply to each property. There are two options:
            </p>
            <Bullets>
              <li>
                <strong>By a table of eras stated here.</strong> You list periods of years
                and the design levels buildings from each period might have. Choose this
                when your portfolios record the year each building was built, and you or a
                colleague know the history of the country&apos;s earthquake building codes.
              </li>
              <li>
                <strong>By the country&apos;s building stock alone.</strong> CASS uses
                GEM&apos;s estimate of how common each design level is across the whole
                country, whatever a building&apos;s age. Choose this when your portfolios do
                not record the year built, or nobody has yet researched the code history.
                You must write a short reason for this choice. The reason is saved, and is
                shown with every model version built on this set, so readers understand the
                limitation.
              </li>
            </Bullets>
          </Step>
          <Step
            number={5}
            title="Fill in the eras, if you chose a table"
            where="Design eras"
          >
            <p>
              Under the <strong>Design eras</strong> heading, enter the eras from oldest to
              newest, one row each. The fields are numbered, so the first row is{" "}
              <strong>Era 1 up to year</strong> and so on. For each era, fill in:
            </p>
            <Bullets>
              <li>
                <strong>Up to year</strong>: the last year the era includes. Leave this
                empty for the final, most recent era, which runs up to today.
              </li>
              <li>
                <strong>Design levels</strong>: the levels a building from this period
                might have, separated by spaces, for example{" "}
                <span className="mono">CDN CDL</span>. Listing more than one means CASS will
                blend them.
              </li>
              <li>
                <strong>Reason</strong>: why you believe this, for example &ldquo;Before the
                national seismic code was adopted&rdquo;. Every era must have a reason.
              </li>
            </Bullets>
            <Example title="An era table (illustrative, not researched)">
              <p>Era 1: up to 1983, design levels CDN, reason: no national seismic code.</p>
              <p>Era 2: up to 2002, design levels CDN CDL, reason: first code, weakly enforced.</p>
              <p>Era 3: up to year left empty, design levels CDL CDM, reason: current code.</p>
              <p>
                A building from 1995 matches era 2, so it is modelled as a blend of no
                design and low design.
              </p>
            </Example>
            <p>
              CASS reads the table from the top and uses the first era a building&apos;s
              year fits. It will refuse a table that is out of order, that stops before the
              present day, that uses a design level GEM does not have, or that has an era
              without a reason. A property whose year built is not known uses the
              country&apos;s overall mix of design levels, whichever option you chose.
            </p>
          </Step>
          <Step number={6} title="Record open questions, then build" where="Build the vulnerability set">
            <p>
              Write any unresolved questions (one per line) and notes, then press the build
              button. A green message reports how many vulnerability functions and building
              classes were built. The set is saved as a draft.
            </p>
            <p>
              You may see a mention of channels. Some building classes include buildings that
              respond to different kinds of shaking: for example, when a building&apos;s height
              is unknown, the class includes both short buildings (which respond to quick,
              sharp shaking) and tall ones (which respond to slow, rolling shaking). CASS
              splits such a class into one part, or channel, for each kind of shaking,
              automatically. You do not need to do anything.
            </p>
          </Step>
        </Steps>
        <Api>POST /api/v1/vulnerability-sets/build/</Api>
      </Card>

      <Card title="Part 4: Assemble the model version">
        <div className="guide-prose">
          <p>
            This joins a grid and a vulnerability set into a model version. The hazard is
            added later, in part 6, because it takes longer to produce.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Choose the grid and the vulnerability set" where="Models → Build → Assemble a model version">
            <p>
              Choose one of each from the lists. Both must be for the same country. CASS
              refuses a pair from two different countries, because the calculation would
              run without any error but the answer would be meaningless.
            </p>
          </Step>
          <Step number={2} title="Give it a version and a label" where="Model version and Label">
            <p>
              The model version has its own version number, separate from the grid&apos;s
              and the vulnerability set&apos;s. Give it a label people will recognise. You do
              not need to write down its limitations: CASS copies them from the grid and the
              vulnerability set automatically, so they cannot be forgotten.
            </p>
          </Step>
          <Step number={3} title="Assemble it" where="Assemble the model version">
            <p>
              The new model version appears in the <strong>Model versions</strong> table
              further down the Build tab. It is a draft, and the{" "}
              <strong>Outstanding</strong> column lists what is still missing. At this
              point it will list the hazard, which is added next.
            </p>
          </Step>
        </Steps>
        <Api>POST /api/v1/model-versions/assemble/</Api>
      </Card>

      <Card title="Part 5: Upload, set up and run the hazard model">
        <div className="guide-prose">
          <p>
            This part turns a published earthquake model into a hazard set: thousands of
            imagined earthquakes and the shaking each one causes in every cell. It happens
            on <Link to="/models?tab=hazard">Models → Hazard</Link>, in three separate
            steps: upload, set up, and run. They are kept separate on purpose. A
            country-wide calculation can take hours, so CASS shows you what is in the model
            and exactly what the calculation will do before you start it, while mistakes
            are still cheap to fix.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Upload the hazard model" where="Models → Hazard → Upload a model → Register model">
            <p>
              Choose the hazard model&apos;s zip file and press{" "}
              <strong>Register model</strong>. CASS lists every file in it and records a
              fingerprint of each one (a code that changes if the file is ever altered), so
              it can always prove which files were used. It reads the settings file, and
              counts how many alternative scientific views the model contains (the paths
              through its logic tree; see <SectionLink section="glossary">Glossary</SectionLink>
              ). It does not change anything inside the package.
            </p>
          </Step>
          <Step number={2} title="Choose the model and the grid" where="Registered models, then Area-peril grid">
            <p>
              Select the model you uploaded from <strong>Registered models</strong>. Then
              choose the grid the shaking should be calculated on. Choose the same grid you
              used to assemble your model version in part 4; otherwise the hazard set will
              not fit it.
            </p>
          </Step>
          <Step number={3} title="Choose the region" where="Region">
            <p>
              The region decides which cells are calculated. It affects the run time more
              than any other setting. The choices are:
            </p>
            <Bullets>
              <li>
                <strong>Jakarta and Bandung</strong> or <strong>Java and Madura</strong>:
                ready-made areas for Indonesia.
              </li>
              <li>
                <strong>Custom bounds</strong>: type the minimum and maximum latitude and
                longitude of your own rectangle.
              </li>
              <li>
                <strong>The whole grid</strong>: the entire country. This can take several
                hours.
              </li>
            </Bullets>
            <p>
              Only properties inside the region you calculate will receive a loss, so choose
              a region that covers your portfolio.
            </p>
          </Step>
          <Step number={4} title="Review and adjust the settings">
            <p>
              The screen divides the model&apos;s settings into two kinds. The{" "}
              <strong>model&apos;s own science</strong>, such as its logic tree, is shown
              but cannot be changed: changing it would turn it into a different model while
              still carrying the original scientists&apos; name. The{" "}
              <strong>calculation&apos;s settings</strong> can be changed. CASS starts from
              these values:
            </p>
            <GuideTable>
              <thead>
                <tr>
                  <th scope="col">Setting</th>
                  <th scope="col">Starting value</th>
                  <th scope="col">What it means, and why</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row" className="mono">
                    calculation_mode
                  </th>
                  <td className="mono">event_based</td>
                  <td>
                    Imagine individual earthquakes. A loss model needs them, and published
                    models are usually set to a different mode (classical), so CASS always
                    switches this.
                  </td>
                </tr>
                <tr>
                  <th scope="row" className="mono">
                    investigation_time
                  </th>
                  <td>50 years</td>
                  <td>The number of years in each batch of imagined earthquakes.</td>
                </tr>
                <tr>
                  <th scope="row" className="mono">
                    ses_per_logic_tree_path
                  </th>
                  <td>10</td>
                  <td>
                    The number of batches for each path through the logic tree. Ten, so
                    that rare losses rest on enough imagined years (see below).
                  </td>
                </tr>
                <tr>
                  <th scope="row" className="mono">
                    number_of_logic_tree_samples
                  </th>
                  <td>20</td>
                  <td>
                    The number of paths through the logic tree to use. Testing showed that a
                    single path could be about 20% away from the scientists&apos; overall
                    view; combining 20 paths is much closer, and costs no more time.
                  </td>
                </tr>
              </tbody>
            </GuideTable>
            <p>
              Together these give 50 × 10 × 20 = <strong>10,000 imagined years</strong>.
            </p>
            <p>
              <strong>Why 10,000 and not 1,000.</strong> A 1-in-1,000-year loss is read from
              the worst imagined years. From 1,000 imagined years it is simply the single
              worst year, so a different set of imagined years could give a very different
              figure. From 10,000 imagined years it is the tenth-worst, which is much
              steadier. As a rule of thumb, divide the imagined years by the return period
              to see how many years a figure rests on: 10,000 ÷ 250 = 40 years for the
              1-in-250-year loss.
            </p>
            <p>
              <strong>The cost.</strong> Both the calculation time and the storage grow with
              the imagined years. When you resolve the configuration (the next step), CASS
              shows the number of imagined years, how many years each return period rests
              on, and the most the hazard could store. If that is more than 100 GB, it
              warns you; you can then lower the number of batches or choose a smaller
              region.
            </p>
          </Step>
          <Step number={5} title="Check the setup" where="Resolve configuration">
            <p>
              Press <strong>Resolve configuration</strong>. CASS shows every difference
              between the model as published and the calculation it will actually run, and
              lists any problems. Read these now: finding a problem here takes seconds, but
              finding it after several hours of calculation wastes the whole run.
            </p>
            <p>
              When you are happy, press <strong>Save this configuration</strong>.
            </p>
          </Step>
          <Step number={6} title="Start the calculation" where="Saved configurations → Run">
            <p>
              Open <strong>Saved configurations</strong> and press <strong>Run</strong>{" "}
              beside the one you saved. The calculation appears under{" "}
              <strong>Hazard and conversion runs</strong> on the Build tab, and in the{" "}
              <Link to="/runs">Run monitor</Link>, where you can watch its progress. You
              can close the browser while it runs.
            </p>
            <p>
              When it finishes, the result is saved as a <strong>hazard set</strong> and
              appears in the <strong>Hazard sets</strong> table on the Build tab. CASS
              keeps the calculation&apos;s full results (its <strong>datastore</strong>) for
              90 days and removes OpenQuake&apos;s own copy, so the calculation is only
              stored once.
            </p>
          </Step>
        </Steps>
        <Notice tone="info" title="How long it takes, and how much it stores">
          Measured on this installation for the Jakarta and Bandung region (962 cells, 20
          paths, 10,000 imagined years): the whole run took about 9 minutes, and stored
          233 MB of calculation results and a 38 MB footprint. CASS stores only shaking
          of 0.05 g or more, because no building in the world earthquake model is damaged
          by less; that keeps storage about a tenth of what it would otherwise be. A whole
          country takes far longer and stores more: the setup screen shows the most it
          could store before you save it. You do not need to worry about the
          computer&apos;s memory: the results are read a slice at a time, whatever their
          size.
        </Notice>
        <Api>{"POST /api/v1/hazard-models/{id}/configure/ · POST …/specs/{spec}/launch/"}</Api>
      </Card>

      <Card title="Reusing a hazard set, and when it must be run again">
        <div className="guide-prose">
          <p>
            A hazard calculation is slow, but it is done once for a grid, not once for
            each portfolio. After that, every portfolio and every model version on the same
            grid can use the same hazard set. A loss calculation then only looks up the
            shaking in the cells where the portfolio&apos;s properties are, which is why it
            takes seconds or minutes rather than hours.
          </p>
        </div>
        <Terms>
          <Term term="You can reuse the hazard set when you change">
            <Bullets>
              <li>the portfolio, or bring in a new one;</li>
              <li>
                the vulnerability set or the assumption set, as long as its functions only
                need intensity measures the hazard set has;
              </li>
              <li>deductibles, limits, reinsurance or other financial terms;</li>
              <li>
                the model version, as long as it is on the same grid. The loss engine&apos;s
                package is rebuilt, which takes about a minute, but the hazard is not.
              </li>
            </Bullets>
          </Term>
          <Term term="You must run the hazard again when you change">
            <Bullets>
              <li>
                <strong>the grid</strong>, in any way, including a new version of it: the
                cell numbers change, so the stored shaking would point at the wrong
                places;
              </li>
              <li>
                <strong>the hazard model</strong>, such as a new release of the earthquake
                model;
              </li>
              <li>
                <strong>the calculation settings</strong>: the imagined years, the number
                of paths, the random seed, or the region;
              </li>
              <li>
                <strong>the ground conditions</strong> joined to the cells;
              </li>
              <li>
                <strong>the intensity measures needed</strong>, if a new vulnerability set
                needs one the hazard set does not have.
              </li>
            </Bullets>
          </Term>
          <Term term="In between: when CASS's intensity bands change">
            <p>
              The shaking is stored as bands of strength (intensity bins). If CASS&apos;s
              bands change, the stored shaking has to be sorted into the new bands, but the
              earthquakes themselves do not need calculating again. CASS does this from the
              calculation&apos;s stored results with <strong>Rebuild the footprint</strong>{" "}
              (part 6 below), as long as those results are still kept, which is 90 days
              after the calculation.
            </p>
          </Term>
        </Terms>
        <Notice tone="info" title="So settle the grid first">
          Because any change to the grid means running the hazard again, finish the grid
          before starting a long hazard calculation on it.
        </Notice>
      </Card>

      <Card title="Part 6: Attach the hazard set to the model version">
        <Steps>
          <Step number={1} title="Find the hazard set" where="Models → Build → Hazard sets">
            <p>
              Each row is one hazard set. It shows the number of imagined earthquakes and
              years, the number of cells, and the intensity measures it contains. (Intensity
              measures are the different ways shaking strength is measured, such as PGA; each
              vulnerability function needs a particular one.)
            </p>
          </Step>
          <Step number={2} title="Choose the model version and attach" where="Attach">
            <p>
              Choose the model version from the list in that row and press{" "}
              <strong>Attach</strong>. Only model versions built on the same grid and
              country are offered.
            </p>
            <p>
              CASS refuses to attach a hazard set that is missing an intensity measure the
              vulnerability functions need. Without it, some buildings would have no
              shaking and would show a loss of zero, which would look exactly like
              buildings that were not damaged. If this happens, run the hazard calculation
              again including the missing measure.
            </p>
          </Step>
          <Step number={3} title="Rebuild a footprint, if a row asks you to" where="Hazard sets → Rebuild the footprint">
            <p>
              A row may say{" "}
              <strong>
                Binned against intensity bins that have since changed, so it cannot be
                packaged
              </strong>
              . That happens when CASS&apos;s intensity bands are updated, as they were on 17
              September 2026, when the strongest bands were widened because long
              calculations had shaking stronger than the old top band could hold.
            </p>
            <p>
              Press <strong>Rebuild the footprint</strong>. CASS sorts the stored shaking
              into the current bands, without running the earthquake calculation again,
              and follows it on the <Link to="/runs">Run monitor</Link>. The result is a new
              hazard set with the same name and <span className="mono">-b</span> plus a
              short code at the end of its version, which says &ldquo;Rebuilt from&rdquo;
              the old one. Attach the new set to your model version. Once no model version
              uses the old set, its footprint is deleted to save space; its record stays so
              the history is complete.
            </p>
            <p>
              If the button is missing, the row says why: usually that the calculation&apos;s
              stored results are older than 90 days and have been removed, in which case
              the hazard has to be run again.
            </p>
            <p>
              A vulnerability set built before the same change also has to be built again
              from its GEM release (part 3): the package build refuses a vulnerability set
              and a hazard set that use different bands, because their numbers would be
              read against each other band by band.
            </p>
          </Step>
        </Steps>
        <Api>{"POST /api/v1/model-versions/{id}/attach-hazard/ · POST /api/v1/hazard-sets/{id}/rebuild/"}</Api>
      </Card>

      <Card title="Part 7: Publish the model version">
        <div className="guide-prose">
          <p>
            Publishing freezes the model version so its results can always be repeated, and
            makes it available to choose for runs.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Read what is still outstanding" where="Model versions → Outstanding">
            <p>
              This column lists everything that stops the model version counting as a
              finished model. One example is clipped hazard: shaking stronger than the
              highest band can record, which would understate the biggest losses.
            </p>
          </Step>
          <Step number={2} title="Publish it" where="Publish, or Publish as research">
            <p>
              If nothing is outstanding, the button says <strong>Publish</strong>, and the
              model version becomes a finished model.
            </p>
            <p>
              If something is outstanding, the button says{" "}
              <strong>Publish as research</strong>. The model version can still be run, and
              its results are useful for research, but they cannot be approved, and the
              outstanding items remain listed for everyone to see.
            </p>
          </Step>
        </Steps>
        <Notice tone="info" title="What to expect today">
          A model version assembled on CASS at the moment will always show outstanding
          items, and so will always be published as research. Two of them cannot yet be
          cleared from the screens: the grid is still a draft (there is no screen for
          publishing a grid yet), and no end-to-end validation date has been recorded.
          This is expected, and matches what CASS is for: research. It does mean the{" "}
          <strong>Decision use</strong> run mode will not be available for these model
          versions.
        </Notice>
        <Api>{"POST /api/v1/model-versions/{id}/publish/"}</Api>
      </Card>

      <Card title="Part 8: Get approval and build the Oasis package">
        <div className="guide-prose">
          <p>
            The loss engine, Oasis, cannot read a model version directly. It needs the
            model converted into its own files, called a <strong>package</strong>. Because
            the conversion involves scientific choices, a reviewer must approve it first.
            This takes two people.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Ask for approval (modeller)" where="Model versions → Request converter approval">
            <p>
              Once a hazard set is attached, the model version&apos;s row shows{" "}
              <strong>Request converter approval</strong>. Press it. The row then says{" "}
              <strong>converter gate awaiting a reviewer</strong>.
            </p>
          </Step>
          <Step number={2} title="Approve or reject (reviewer)" where="Models → Build → gates waiting on a decision">
            <p>
              A reviewer opens the Build tab, where a card at the top lists the gates waiting
              on a decision. They read the request and press <strong>Approve</strong> or{" "}
              <strong>Reject</strong>. The reviewer must be a different person from the one
              who asked.
            </p>
          </Step>
          <Step number={3} title="Build the package (modeller)" where="Build Oasis package">
            <p>
              Once approved, the row shows <strong>Build Oasis package</strong>. Press it.
              CASS writes the shaking, the earthquake years, the vulnerability functions and
              the property-matching rules into the files Oasis reads. Use the{" "}
              <strong>Follow the conversion run</strong> link to watch it.
            </p>
          </Step>
        </Steps>
        <Notice tone="warning" title="Oasis uses one package at a time">
          <p>
            Building a package for one model version replaces the package Oasis was using
            before. If someone then starts a run with a different model version, CASS stops
            it before it begins and names both model versions.
          </p>
          <p>
            This protects you from a serious mistake: without the check, the run would
            calculate with one model but label the results with another. To run an older
            model version again, build its package again first.
          </p>
        </Notice>
        <Api>
          {
            "POST /api/v1/approvals/ · POST /api/v1/approvals/{id}/decide/ · POST /api/v1/model-versions/{id}/build-package/"
          }
        </Api>
      </Card>
    </div>
  );
}
