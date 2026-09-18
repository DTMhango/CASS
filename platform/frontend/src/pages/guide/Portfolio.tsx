/** Bring in a portfolio: import, review, correct, publish, and add the financial structure. */

import { Link } from "react-router-dom";

import { Notice } from "@/components/primitives";

import {
  Card,
  Step,
  Steps,
  Bullets,
  Api,
  GuideTable,
  GlossaryLink,
  Example,
  SectionLink,
} from "./parts";

export function Portfolio() {
  return (
    <div className="guide">
      <Card title="Before you start">
        <div className="guide-prose">
          <p>
            A <strong>portfolio</strong> is your list of insured properties. Before CASS
            can use it, three things happen: it is brought in, it is checked and
            corrected, and it is <strong>published</strong>, which freezes it so any
            calculation made from it can be repeated exactly.
          </p>
          <p>
            Everything in this section happens in <Link to="/exposure">Exposure</Link>,
            which has three tabs: <strong>Portfolios</strong>,{" "}
            <strong>Import review</strong> and <strong>Financial structure</strong>.
          </p>
          <p>
            First, though, you need a project, because a portfolio belongs to one and
            the screens below stay empty without it. On the{" "}
            <Link to="/">Portfolio dashboard</Link>, click a project&apos;s name to work
            in it, or press <strong>New project</strong> if there is none to click;
            anyone can create one, whatever their role. The project you are working in
            is named in the header at the top of every screen, and the Portfolios tab
            says so too when you have not chosen one.
          </p>
        </div>
      </Card>

      <Card title="Which route to use">
        <div className="guide-prose">
          <p>There are two ways to bring a portfolio in. Which one depends on your data.</p>
        </div>
        <Bullets>
          <li>
            <strong>Route A: your data is a spreadsheet in your own layout.</strong> This
            is the usual case. You copy the data into the CASS intake template, and CASS
            checks it thoroughly before it becomes a portfolio.
          </li>
          <li>
            <strong>Route B: your data is already in OED files.</strong>{" "}
            <GlossaryLink term="OED">OED</GlossaryLink> is the industry standard layout. If
            a colleague or another system has already produced OED files, you can upload
            them directly.
          </li>
        </Bullets>
        <div className="guide-prose">
          <p>
            Both are on the Portfolios tab, under <strong>Add a portfolio</strong>, headed{" "}
            <strong>Your data is a spreadsheet</strong> and{" "}
            <strong>Your data is already OED</strong>. That panel is what the tab shows
            until you pick a portfolio from the list; once you have picked one, press{" "}
            <strong>Add portfolio</strong> above the list to get it back.
          </p>
        </div>
      </Card>

      <Card title="Route A: Bring in a spreadsheet with the intake template">
        <Steps>
          <Step number={1} title="Download the intake template" where="Exposure → Portfolios → Add a portfolio → Your data is a spreadsheet → Download the intake template">
            <p>
              Press <strong>Download the intake template</strong> to save the spreadsheet
              to your computer. Its columns are exactly the ones CASS reads, so filling it
              in avoids having to explain your own spreadsheet layout to CASS later.
            </p>
          </Step>
          <Step number={2} title="Copy your data into the template">
            <p>
              Five columns decide whether a property can be modelled at all. Fill these in
              first, and exactly as described: a blank or unexpected value in any of them
              quietly removes the row, and later a whole customer, from the portfolio.
            </p>
            <GuideTable>
              <thead>
                <tr>
                  <th scope="col">Column</th>
                  <th scope="col">What to put in it</th>
                  <th scope="col">What happens if it is wrong or blank</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Latitude, Longitude</th>
                  <td>
                    The coordinates, as decimals. South and west are negative: Jakarta is
                    −6.2088, 106.8456.
                  </td>
                  <td>
                    Blank coordinates, or 0 and 0, make the row unclassified, and an
                    unclassified row is left out of every promotion until the workbook is
                    corrected and imported again.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Country</th>
                  <td>The two-letter ISO code, such as ID.</td>
                  <td>
                    CASS checks the coordinates fall inside the country, or within 5 km of
                    its coast or border, against country outlines it ships with for every
                    ISO code. A row further out is unclassified and listed under{" "}
                    <strong>What the workbook check found</strong>. Correct the coordinate
                    or the code and import again; or, if the coordinate is right (an
                    offshore platform, say), confirm the row into a cohort in the review
                    queue and record why. A code that is not an ISO code, such as{" "}
                    <span className="mono">UK</span> for the United Kingdom (
                    <span className="mono">GB</span>), makes the row unclassified too.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Geocode precision</th>
                  <td>
                    How exact the coordinates are. One of{" "}
                    <span className="mono">parcel</span>, <span className="mono">street</span>{" "}
                    or <span className="mono">embedded</span> (exact enough to trust the
                    cell: cohort A), or <span className="mono">locality</span>,{" "}
                    <span className="mono">postcode</span> or{" "}
                    <span className="mono">admin</span> (approximate: cohort B).
                  </td>
                  <td>
                    Blank, or any other word, makes the row{" "}
                    <GlossaryLink term="Cohort">unclassified</GlossaryLink>. This is the
                    single most common reason a promotion finds nothing to promote.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Class of business</th>
                  <td>
                    <span className="mono">Fire</span>, spelled exactly like that, for
                    property business.
                  </td>
                  <td>
                    Promotion only takes <span className="mono">Fire</span> rows. Anything
                    else, including <span className="mono">fire</span> in lower case, is left
                    out. Liability and Engineering are excluded on purpose: liability has no
                    building at the coordinates, and engineering risks need different
                    treatment.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Needs review</th>
                  <td>
                    <span className="mono">yes</span> for a row someone should check; leave
                    it blank otherwise.
                  </td>
                  <td>
                    A <span className="mono">yes</span> puts the row in cohort C, which keeps
                    it out of a cohort A portfolio until you decide about it.
                  </td>
                </tr>
              </tbody>
            </GuideTable>
            <p>The rest of the columns change the answer rather than gate it:</p>
            <Bullets>
              <li>
                <strong>Total insured value</strong>, and the four value columns (
                <strong>Building value</strong>, <strong>Other structures value</strong>,{" "}
                <strong>Contents value</strong>,{" "}
                <strong>Business interruption value</strong>). Fill in the four where you
                know them. Where you give only a total, CASS splits it using the coverage
                split you choose when you promote.
              </li>
              <li>
                <strong>Primary site</strong>: marks the main location of a customer with
                several. It is only used if you choose the 70/30 allocation when promoting.
              </li>
              <li>
                <strong>Storeys</strong>: the number of floors. Fill it in wherever you know
                it. If you do not, leave it blank; 0 means the same thing. A building with no
                stated height is modelled as a blend of every height it could be, which is a
                less precise answer. (This column is called{" "}
                <span className="mono">NumberOfStoreys</span> in OED files, but{" "}
                <strong>Storeys</strong> in this template.)
              </li>
              <li>
                <strong>Year built</strong>: lets a table of design eras decide how well the
                building was designed for earthquakes. Without it, the country&apos;s overall
                mix is used.
              </li>
              <li>
                <strong>Occupancy</strong> and <strong>Construction</strong>: what the
                building is used for and what it is built from. They decide which
                vulnerability function answers the risk, so the more you fill in, the more
                specific the model can be.
              </li>
              <li>
                <strong>Perils covered</strong>: write <span className="mono">QEQ</span>,
                which is earthquake shaking. The template&apos;s own help says the same.
              </li>
              <li>
                <strong>Policy ID</strong> joins a property to its policy row, and{" "}
                <strong>Risk reference</strong> identifies the property itself.
              </li>
              <li>
                <strong>Deductibles and limits.</strong> There are two places for them: Risk
                deductible and Risk limit on the Risks sheet, and Policy deductible and Policy
                limit on the Policies sheet. CASS takes the risk&apos;s deductible off first, then
                the policy&apos;s deductible off what is left. So for a policy that covers{" "}
                <strong>one property</strong>, which is most policies, fill in the Policies sheet
                only and leave the two risk columns blank. Written in both places, the same
                deductible is taken off twice. Use the risk columns only when a policy covers
                several properties that each have their own deductible or limit.
              </li>
              <li>
                <strong>Layer</strong>: a policy with more than one layer has one row per layer
                on the Policies sheet, each with the same Policy ID and Policy reference. Repeat
                the policy deductible and limit on every layer&apos;s row, because each layer
                reads them from its own row.
              </li>
              <li>
                <strong>Layer attachment</strong> and <strong>Layer limit</strong>: fill in
                both on every policy row. Write 0 as the attachment of a layer that pays from
                the first loss, and the sum insured as the limit when the policy is a share
                of the whole risk rather than of a layer. A blank is written as blank, which
                the loss engine reads as no limit and no attachment: that policy&apos;s
                insured loss becomes its ground-up loss. CASS still models it, and lists it
                as <strong>possibly overstated</strong> on the portfolio and beside every
                insured and net-of-reinsurance result.
              </li>
              <li>
                <strong>Reinsurance contracts</strong> and <strong>Reinsurance scope</strong>{" "}
                sheets: fill these in only if the portfolio is reinsured. The contracts sheet
                has one row per layer of each contract. The scope sheet says what each
                contract covers, once per contract: a row with only the contract number
                covers the whole portfolio, and a row with a Policy ID covers that policy. The
                contract types CASS applies are quota share (QS), surplus share (SS) and
                catastrophe excess of loss (CXL). See the example below.
              </li>
            </Bullets>
            <Example title="A policy with one property: where the deductible goes">
              <p>
                An earthquake causes 120,000 of damage to a property. Its policy has a
                deductible of 25,000.
              </p>
              <p>
                Written on the Policies sheet only, the insurance pays 120,000 − 25,000 ={" "}
                <strong>95,000</strong>. Written on both sheets, it is taken off twice and the
                insurance pays 120,000 − 25,000 − 25,000 = <strong>70,000</strong>, which is
                25,000 too little. CASS warns when a single-property policy has a deductible on
                both sheets.
              </p>
            </Example>
            <Example title="A policy with one property and two layers">
              <p>
                A warehouse has a building value of 8,000,000 and a contents value of
                2,000,000. It is the only property on its policy, and the policy has two
                layers, so it takes <strong>one row on the Risks sheet</strong> and{" "}
                <strong>two rows on the Policies sheet</strong>:
              </p>
              <GuideTable>
                <thead>
                  <tr>
                    <th scope="col">Policy ID</th>
                    <th scope="col">Policy reference</th>
                    <th scope="col">Layer</th>
                    <th scope="col">Layer attachment</th>
                    <th scope="col">Layer limit</th>
                    <th scope="col">Signed share</th>
                    <th scope="col">Policy deductible</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="mono">2026_06_PFAC8716</td>
                    <td className="mono">P-10802-01</td>
                    <td>1</td>
                    <td>0</td>
                    <td>3,000,000</td>
                    <td>0.2</td>
                    <td>100,000</td>
                  </tr>
                  <tr>
                    <td className="mono">2026_06_PFAC8716</td>
                    <td className="mono">P-10802-01</td>
                    <td>2</td>
                    <td>3,000,000</td>
                    <td>7,000,000</td>
                    <td>0.1</td>
                    <td>100,000</td>
                  </tr>
                </tbody>
              </GuideTable>
              <p>An earthquake causes 6,000,000 of damage to the warehouse.</p>
              <ol className="guide-numbered">
                <li>The policy deductible comes off: 6,000,000 − 100,000 = 5,900,000.</li>
                <li>
                  Layer 1 pays the part of that from 0 to 3,000,000, at a 20% share: 3,000,000 ×
                  0.2 = <strong>600,000</strong>.
                </li>
                <li>
                  Layer 2 pays the part from 3,000,000 up to 10,000,000 (its attachment plus its
                  limit), at a 10% share: (5,900,000 − 3,000,000) × 0.1 ={" "}
                  <strong>290,000</strong>.
                </li>
                <li>The insured loss is 600,000 + 290,000 = <strong>890,000</strong>.</li>
              </ol>
              <p>
                Both layers look at the same 5,900,000; layer 2 does not wait for layer 1 to
                be used up. If the deductible were written on layer 1&apos;s row only, layer 2
                would work from the full 6,000,000 and pay 300,000, so the insured loss would
                come to 900,000. These figures were checked by running the same rows through
                the loss engine.
              </p>
            </Example>
            <Example title="A quota share and a two-layer catastrophe programme">
              <p>
                The template&apos;s own example rows show this programme. A 30% quota share
                covers one policy and applies first. Then a catastrophe excess of loss in two
                layers covers the whole portfolio: 20,000,000 in excess of 5,000,000, and
                50,000,000 in excess of 25,000,000 with 85% of it placed. The Reinsurance
                contracts sheet has three rows:
              </p>
              <GuideTable>
                <thead>
                  <tr>
                    <th scope="col">Contract number</th>
                    <th scope="col">Layer</th>
                    <th scope="col">Contract type</th>
                    <th scope="col">Inuring priority</th>
                    <th scope="col">Ceded share</th>
                    <th scope="col">Placed share</th>
                    <th scope="col">Attachment per event</th>
                    <th scope="col">Limit per event</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>1</td>
                    <td>1</td>
                    <td>QS</td>
                    <td>1</td>
                    <td>0.3</td>
                    <td>1</td>
                    <td></td>
                    <td></td>
                  </tr>
                  <tr>
                    <td>2</td>
                    <td>1</td>
                    <td>CXL</td>
                    <td>2</td>
                    <td></td>
                    <td>1</td>
                    <td>5,000,000</td>
                    <td>20,000,000</td>
                  </tr>
                  <tr>
                    <td>2</td>
                    <td>2</td>
                    <td>CXL</td>
                    <td>2</td>
                    <td></td>
                    <td>0.85</td>
                    <td>25,000,000</td>
                    <td>50,000,000</td>
                  </tr>
                </tbody>
              </GuideTable>
              <p>The Reinsurance scope sheet has two:</p>
              <GuideTable>
                <thead>
                  <tr>
                    <th scope="col">Contract number</th>
                    <th scope="col">Policy ID</th>
                    <th scope="col">What it means</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>1</td>
                    <td className="mono">2026_06_PFAC8716</td>
                    <td>The quota share covers this policy only.</td>
                  </tr>
                  <tr>
                    <td>2</td>
                    <td></td>
                    <td>Both layers of contract 2 cover the whole portfolio.</td>
                  </tr>
                </tbody>
              </GuideTable>
              <p>
                The two catastrophe layers share contract number 2 and inuring priority 2.
                That makes them one programme: both look at the same loss, the one left after
                the quota share, and each pays its own slice. If layer 2 had priority 3, it
                would only see what layer 1 left, and would usually pay nothing.
              </p>
              <p>
                The example rows also fill in the three reinstatement columns: the first
                layer has 2 reinstatements at a rate of 1 (100%) on a reinstatement premium of
                900,000, and the second has 1 at 100% on 450,000. The loss engine ignores
                these; a run with limited cover applies them (see{" "}
                <SectionLink section="run">Run and read results</SectionLink>).
              </p>
            </Example>
            <Notice tone="warning" title="Two things this route fixes for you">
              <p>
                However you fill the template in, promotion records{" "}
                <strong>every peril as QEQ</strong> (earthquake shaking) and{" "}
                <strong>every value as USD</strong>. The Currency column is read but not
                used.
              </p>
              <p>
                So if your values are in another currency, say rupiah, the results will carry
                rupiah amounts labelled USD. Convert the values yourself before you upload, or
                treat the currency label on the results as wrong and say so when you share
                them. Route B, where you upload OED files, keeps the currency you state.
              </p>
            </Notice>
          </Step>
          <Step number={3} title="Upload the completed template" where="Completed template, As-at date, Import workbook">
            <p>
              Under <strong>Completed template</strong>, choose your filled-in file. If the
              spreadsheet describes the portfolio at a date other than today (for example,
              the end of last quarter), enter that date as the{" "}
              <strong>As-at date</strong>. Then press <strong>Import workbook</strong>.
            </p>
            <p>
              The as-at date is a label, not an instruction. CASS records it with the
              import and shows it on the import review, so that two imports of the same
              book taken at different dates can be told apart. It changes nothing about
              what CASS reads, and no calculation uses it. Leaving it empty means the
              import is read as the position today.
            </p>
            <p>
              CASS reads the file exactly as it is and does not correct anything. A green
              message confirms it was read. At this point it is an <strong>import</strong>,{" "}
              <strong>not yet a portfolio</strong>: nothing can be run on it until you have
              reviewed and promoted it.
            </p>
          </Step>
          <Step number={4} title="Open the import review" where="Exposure → Import review">
            <p>
              Choose your import from the <strong>Import</strong> list at the top. The page
              then shows these cards, in this order:
            </p>
            <Bullets>
              <li>
                <strong>What was read</strong>: the file, and what CASS made of it.
              </li>
              <li>
                <strong>Height, and what not knowing it costs</strong>: how many properties
                have no storey count, and what filling it in would change.
              </li>
              <li>
                <strong>Missing model inputs</strong>: which information the model needs is
                missing, and how much insured value sits behind each gap.
              </li>
              <li>
                <strong>What was included</strong>: the properties by country and class of
                business, and the cohort each one is in.
              </li>
              <li>
                <strong>Allocation and coordinate findings</strong> and{" "}
                <strong>Does the allocation assumption matter?</strong>: how value was shared
                between a customer&apos;s locations, and how far the answer would move under a
                different assumption.
              </li>
              <li>
                <strong>Do the coarse geocodes support their cells?</strong>: the check in
                step 6.
              </li>
              <li>
                <strong>What a result from this import may be used for</strong>: which kinds
                of run this data is good enough for.
              </li>
              <li>
                <strong>Review queue</strong> and <strong>Promote to a portfolio</strong>:
                steps 5, 7 and 8.
              </li>
            </Bullets>
            <p>
              Read it from the top. The point of this page is to show you the cost of each
              gap in the data before anybody relies on a result.
            </p>
          </Step>
          <Step number={5} title="Work through the review queue" where="Review queue → Record decision">
            <p>
              Properties in cohort C have something that needs a person to check it, such
              as a location that looks wrong. They are listed in the review queue with the
              most valuable first, so your time goes where the money is.
            </p>
            <p>
              For each one, decide what should happen, write a short reason, and press{" "}
              <strong>Record decision</strong>. Your decision is recorded alongside the data;
              your original spreadsheet is never altered.
            </p>
            <p>
              You do not have to finish the whole queue. Any property still waiting for a
              decision is left out of the portfolio rather than modelled on a guess.
            </p>
            <Notice tone="warning" title="Leaving one property out leaves its customer out">
              <p>
                CASS takes a customer&apos;s schedule whole or not at all: every one of an
                account&apos;s properties must qualify, or none of them are included.
              </p>
              <p>
                This is deliberate. A policy&apos;s total has to go somewhere, so including
                half a customer&apos;s sites would either pile their value onto the sites that
                were included, overstating them, or lose it altogether. But it does mean a
                single unresolved property removes that whole customer, so work the queue
                customer by customer, not just from the top.
              </p>
            </Notice>
          </Step>
          <Step
            number={6}
            title="Check whether the approximate locations can be trusted"
            where="Do the coarse geocodes support their cells?"
          >
            <p>
              Cohort B properties have coordinates that are only approximate, for example the
              centre of a postcode area rather than the building itself. CASS tests each one
              by trying positions across the whole area its geocode could mean, and checks
              whether it stays in the same cell. The report shows how much insured value is
              in a cell that cannot be relied on. If that value is large, it is worth
              improving those addresses before trusting the results.
            </p>
          </Step>
          <Step number={7} title="Confirm the import has been checked" where="Promote to a portfolio → Accept this import">
            <p>
              At the bottom of the page, press <strong>Accept this import</strong>. This
              records that you have checked that what CASS read matches your spreadsheet.
            </p>
          </Step>
          <Step number={8} title="Turn the import into a portfolio" where="Promote to an exposure version">
            <p>Fill in the form that appears:</p>
            <Bullets>
              <li>
                <strong>Portfolio name</strong>: the name the portfolio will have.
              </li>
              <li>
                <strong>Cohort</strong>: which group of properties to include. The list does
                not say &ldquo;A&rdquo;, &ldquo;B&rdquo; or &ldquo;C&rdquo;. It names them{" "}
                <strong>Automated test cohort</strong> (cohort A, the precise geocodes),{" "}
                <strong>Geocoding-sensitivity cohort</strong> (B, the approximate ones) and{" "}
                <strong>Analyst-review backlog</strong> (C, the flagged ones). The hint under
                the field says how many properties the chosen group holds. Unclassified rows
                are not offered. A row a reviewer has confirmed into a cohort, with a reason,
                is promoted with that cohort.
              </li>
              <li>
                <strong>Country</strong>: a model version covers one country, so a portfolio
                must too. By the same whole-schedule rule as above, a customer with sites in
                two countries is left out of both rather than split between them.
              </li>
              <li>
                <strong>Allocation</strong>: how a policy&apos;s total value is shared between
                its locations when the spreadsheet only gives the total.{" "}
                <strong>Equal across locations</strong> shares it evenly and is the normal
                starting point. <strong>Primary concentrated (70/30)</strong> puts 70% at the
                main location, and is useful as a test of how much the choice matters.
              </li>
              <li>
                <strong>Coverage split</strong>: how a single value is divided between the
                building, its contents and lost income, where the spreadsheet does not say.
              </li>
              <li>
                <strong>Occupancy</strong>: what to assume a building is used for, where the
                spreadsheet does not say.
              </li>
              <li>
                <strong>Policy terms and reinsurance</strong>: what to do with the Policies
                and reinsurance sheets. The hint under the field says how many Policy IDs
                have a layer attachment and limit on every row, and how many reinsurance
                contracts the workbook holds.
                <Bullets>
                  <li>
                    <strong>Apply them, flagging policies with no limit</strong> (the normal
                    choice) writes every policy&apos;s terms and the reinsurance. A policy
                    with no limit, no attachment or no row on the Policies sheet keeps its
                    ground-up loss as insured loss. It is not left out, because leaving it
                    out would understate the book; it is listed as possibly overstated
                    instead.
                  </li>
                  <li>
                    <strong>Leave them out: ground-up loss only</strong> ignores the
                    policy terms and the reinsurance sheets.
                  </li>
                </Bullets>
              </li>
            </Bullets>
            <p>
              Coverage split and Occupancy are only used to fill gaps: anything your
              spreadsheet does state is never overwritten. Press{" "}
              <strong>Promote to an exposure version</strong>. CASS writes the OED files,
              checks them, and publishes the portfolio in one step. If a reinsurance
              contract breaks the contract rules, nothing is promoted and the message says
              which contract and why; the same problems are listed in the import&apos;s
              findings as soon as you upload the workbook.
            </p>
          </Step>
          <Step number={9} title="Check what was written" where="Exposure → Portfolios, then Financial structure">
            <p>
              The green message says what was written: how many policies, how many
              reinsurance contract layers, and which policies may be overstated. Follow its link, or go to Exposure → Portfolios
              and click the portfolio. The <strong>Perspectives this data supports</strong>{" "}
              card says which losses you can now calculate: ground-up, insured, and net of
              reinsurance. The Financial structure tab shows the policies and contracts
              exactly as they will be applied.
            </p>
          </Step>
        </Steps>
        <Api>GET /api/v1/exposure-versions/template/ · POST /api/v1/exposure-versions/upload/</Api>
      </Card>

      <Card title="Route B: Upload OED files directly">
        <Steps>
          <Step number={1} title="Create a portfolio" where="Exposure → Portfolios → Add a portfolio → Your data is already OED">
            <p>
              Type a <strong>Portfolio name</strong> and press{" "}
              <strong>Create version</strong>. This creates version 1 of the portfolio, as a
              draft, and opens it so you can attach its files.
            </p>
          </Step>
          <Step number={2} title="Upload the files" where="Source files">
            <p>
              Upload the location file. If you have them, also upload the account file (which
              holds the policies) and the reinsurance files. CASS keeps the files exactly as
              you uploaded them.
            </p>
          </Step>
          <Step number={3} title="Read the summaries">
            <p>Several cards appear beneath the portfolio. The most useful to read first:</p>
            <Bullets>
              <li>
                <strong>Perspectives this data supports</strong>: which of the three losses
                (ground-up, insured, net of reinsurance) your files contain enough
                information for, and if not, what is missing.
              </li>
              <li>
                <strong>Value summary</strong>: the total insured value, split by coverage,
                by country and by currency. It matches your files exactly, so it is a good
                way to check nothing was lost.
              </li>
            </Bullets>
          </Step>
          <Step number={4} title="Validate and fix problems" where="Validate, then Validation findings and Portfolio rows">
            <p>
              Press <strong>Validate</strong>. CASS checks every row and lists the problems
              it finds under <strong>Validation findings</strong>. Each finding is either an{" "}
              <strong>error</strong> or a <strong>warning</strong>. Errors must be fixed
              before the portfolio can be published. Warnings do not stop you, but they are
              saved and shown alongside any result made from the portfolio, so they are worth
              reading. Use the filter at the top of the card to show only errors.
            </p>
            <p>
              To fix a row, find it in the <strong>Portfolio rows</strong> table at the
              bottom of the page and correct it there. Then press{" "}
              <strong>Validate</strong> again.
            </p>
          </Step>
          <Step number={5} title="Publish" where="Publish">
            <p>
              Once nothing blocking remains, press <strong>Publish</strong>. The portfolio
              is now frozen and can be used in runs.
            </p>
            <p>
              A published portfolio can never be changed, so that every result made from it
              can be repeated. To correct it later, press{" "}
              <strong>Correct in a new version</strong>. That copies it into version 2 as a
              draft, which you can edit and publish in the same way.
            </p>
          </Step>
        </Steps>
        <Api>{"POST /api/v1/exposure-versions/{id}/rows/edit/ · POST …/publish/"}</Api>
      </Card>

      <Card title="Add the insurance and reinsurance details">
        <div className="guide-prose">
          <p>
            A list of properties on its own only gives a <strong>ground-up</strong> loss:
            the cost of the damage. To see what the insurance pays (the insured loss) and
            what the insurer keeps after reinsurance, CASS needs the terms of the policies
            and the reinsurance treaties. This is called the{" "}
            <strong>financial structure</strong>.
          </p>
          <p>
            If you brought the portfolio in with the intake template and filled in its
            Policies and reinsurance sheets, this is already done: promotion writes them.
            The screen below is for a portfolio uploaded as OED files without them, or for
            adding to one.
          </p>
          <p>
            You can only add these to a <strong>draft</strong> portfolio. If the portfolio is
            already published, press <strong>Correct in a new version</strong> first.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Open the financial structure" where="Exposure → Financial structure">
            <p>
              This shows what the portfolio already contains: its accounts and their policy
              layers, any reinsurance contracts, and any problems found with them.
            </p>
          </Step>
          <Step number={2} title="Add a policy for each account" where="Add a policy">
            <p>For each customer account, fill in:</p>
            <Bullets>
              <li>
                <strong>Account</strong>: choose the customer from the list.
              </li>
              <li>
                <strong>Policy reference</strong>: the policy&apos;s own reference number.
              </li>
              <li>
                <strong>Perils covered</strong>: already filled in with{" "}
                <span className="mono">QEQ</span>, which means earthquake shaking.
              </li>
              <li>
                <strong>Policy deductible</strong>: the amount the customer pays before the
                policy pays anything. Leave it empty if there is none. If the account has only
                one property and your location file already gives that property a deductible,
                leave this empty too: CASS takes off the property&apos;s deductible and then the
                policy&apos;s, so the same amount would come off twice. If both are filled in,
                the portfolio&apos;s Validation findings show a warning. You do not need to repeat
                the deductible for each layer; CASS writes it on every layer for you.
              </li>
              <li>
                For each <strong>layer</strong> of cover: the <strong>attachment</strong>{" "}
                (the loss at which it starts paying; 0 for an ordinary policy), the{" "}
                <strong>limit</strong> (the most it pays; leave empty for no limit), and the{" "}
                <strong>signed share</strong> (the proportion the insurer has taken, as a
                decimal: 1 for all of it, 0.25 for 25%). One layer is filled in for you;
                press <strong>Add a layer</strong> if the policy has more than one.
              </li>
            </Bullets>
          </Step>
          <Step number={3} title="Add each reinsurance treaty" where="Add a reinsurance contract">
            <p>
              Choose the <strong>Contract type</strong>, give it a{" "}
              <strong>Contract name</strong>, and fill in the perils it covers. The rest of
              the form changes to ask only for what that type needs:
            </p>
            <GuideTable>
              <thead>
                <tr>
                  <th scope="col">Type</th>
                  <th scope="col">How it works</th>
                  <th scope="col">What you fill in</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Quota share</th>
                  <td>The reinsurer pays a fixed percentage of every loss.</td>
                  <td>
                    <strong>Ceded share</strong> (0.3 for 30%), and a{" "}
                    <strong>Limit per event</strong> if there is one.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Surplus share</th>
                  <td>Works property by property, taking a share of each larger risk.</td>
                  <td>
                    The <strong>Risk level</strong> (per location, per policy or per account),
                    a <strong>Limit per risk</strong> if any, and each risk it covers with its
                    own ceded share.
                  </td>
                </tr>
                <tr>
                  <th scope="row">Catastrophe excess of loss</th>
                  <td>
                    Pays the part of each earthquake&apos;s total loss above a set amount, up to
                    a limit.
                  </td>
                  <td>
                    <strong>Attachment per event</strong>, <strong>Limit per event</strong>, and
                    the <strong>Share of the layer ceded</strong> (leave empty for all of it).
                  </td>
                </tr>
              </tbody>
            </GuideTable>
            <p>For every type, also fill in:</p>
            <Bullets>
              <li>
                <strong>Inuring priority</strong>: the order the treaties apply in. Priority 1
                applies first; the treaty with priority 2 then only sees what is left. For
                example, a quota share is often applied before a catastrophe excess of loss.
              </li>
              <li>
                <strong>Placed share</strong>: the proportion of the treaty actually sold to
                reinsurers.
              </li>
              <li>
                <strong>Covers</strong>: <strong>The whole portfolio</strong>, or{" "}
                <strong>Named accounts or locations</strong>, in which case you list them.
              </li>
            </Bullets>
          </Step>
          <Step number={4} title="Check for problems, then publish" where="What is written">
            <p>
              The <strong>What is written</strong> card shows the policies and contracts as
              CASS has saved them, and flags anything that would stop a run from working, so
              you find out now rather than partway through a calculation. To change a policy
              or contract, remove it and add it again. When there are no problems, go back to
              the Portfolios tab and publish the portfolio.
            </p>
          </Step>
        </Steps>
        <Api>
          {"POST /api/v1/exposure-versions/{id}/structure/policies/ · …/structure/contracts/ · GET …/findings/"}
        </Api>
      </Card>
    </div>
  );
}
