/** When something goes wrong: common refusals, why they happen, and what to do. */

import { Link } from "react-router-dom";

import { Card, Bullets, GuideTable } from "./parts";

export function Problems() {
  return (
    <div className="guide">
      <Card title="Common problems, why they happen, and what to do">
        <div className="guide-prose">
          <p>
            When CASS refuses to do something, it is almost always protecting a result from
            being quietly wrong. The message on screen says why. This table covers the most
            common cases.
          </p>
        </div>
        <GuideTable>
          <thead>
            <tr>
              <th scope="col">What you see</th>
              <th scope="col">Why it happens</th>
              <th scope="col">What to do</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">There is no Hazard or Build tab under Models</th>
              <td>These tabs are only shown to modellers and administrators.</td>
              <td>If you need them, ask an administrator to change your role.</td>
            </tr>
            <tr>
              <th scope="row">Screens are empty, or keep asking for a project</th>
              <td>No project has been chosen.</td>
              <td>Go to the Portfolio dashboard and click a project&apos;s name.</td>
            </tr>
            <tr>
              <th scope="row">There are no projects to choose from</th>
              <td>
                Nobody has created one yet, or you have not been made a member of any
                that exist. A new installation starts with none.
              </td>
              <td>
                Press <strong>New project</strong> on the Portfolio dashboard. Anyone
                can create one, whatever their role, and you own the one you create. If
                you believe a project already exists, ask its owner to add you.
              </td>
            </tr>
            <tr>
              <th scope="row">The grid cell counter shows a yellow warning</th>
              <td>
                The specification would make more cells than the limit, or a refinement&apos;s
                cells are not smaller than the base cells.
              </td>
              <td>
                Make the base resolution larger, make the tiles or refinements smaller, or
                give the refinement a smaller cell size. The warning says which problem it is.
              </td>
            </tr>
            <tr>
              <th scope="row">CASS refuses to build a vulnerability set</th>
              <td>No GEM release has been chosen.</td>
              <td>Go to Models → Build → GEM release and press Use this release.</td>
            </tr>
            <tr>
              <th scope="row">A country is greyed out in the vulnerability country list</th>
              <td>
                GEM describes that country&apos;s buildings in a classification (HAZUS) that
                CASS cannot translate yet.
              </td>
              <td>It cannot be built at the moment. The reason is shown beside the country.</td>
            </tr>
            <tr>
              <th scope="row">CASS refuses to assemble a model version</th>
              <td>The grid and the vulnerability set are for different countries.</td>
              <td>Choose a grid and a vulnerability set for the same country.</td>
            </tr>
            <tr>
              <th scope="row">CASS refuses to attach a hazard set</th>
              <td>
                The hazard set does not include an intensity measure the vulnerability
                functions need, so some buildings would get no shaking.
              </td>
              <td>Run the hazard calculation again, including the missing measure.</td>
            </tr>
            <tr>
              <th scope="row">There is no Build Oasis package button</th>
              <td>
                Either no hazard set is attached, or the converter approval has not been
                granted yet.
              </td>
              <td>
                Attach a hazard set, press Request converter approval, and ask a reviewer to
                approve it.
              </td>
            </tr>
            <tr>
              <th scope="row">
                Promoting says no business has its whole schedule in cohort A Fire
              </th>
              <td>
                Nothing qualified. Usually the <strong>Geocode precision</strong> column is
                blank or holds a word CASS does not recognise, or{" "}
                <strong>Class of business</strong> is not exactly{" "}
                <span className="mono">Fire</span>, or the coordinates are outside the
                country the rows name. It can also be one unresolved property taking its
                whole account out with it.
              </td>
              <td>
                Open <strong>What the workbook check found</strong> and{" "}
                <strong>What was included</strong> on the import review to see which cohort
                the rows landed in and why, fix the columns in the template, and import it
                again. Bring in a portfolio, Route A, step 2 lists the exact values each
                column takes.
              </td>
            </tr>
            <tr>
              <th scope="row">A risk is flagged as outside its country</th>
              <td>
                Its coordinates are more than 5 km outside the country in its{" "}
                <strong>Country</strong> column. Usually the geocoder matched the wrong
                place, a latitude lost its minus sign, or the country code is wrong.
                Sometimes the coordinate is right and the country&apos;s outline stops at
                the coast: an offshore platform, or a site on a small island the map does
                not draw.
              </td>
              <td>
                If the data is wrong, correct the coordinate or the code in the workbook and
                import it again. If the coordinate is right, find the row in the review
                queue, choose <strong>Cohort</strong>, enter the cohort it belongs in (for
                example <span className="mono">A</span>) and say how you know. Promotion
                then includes it.
              </td>
            </tr>
            <tr>
              <th scope="row">A customer you expected is missing from the portfolio</th>
              <td>
                CASS takes a customer&apos;s schedule whole or not at all, so one property
                in another cohort, another country, or another class of business removes all
                of that customer&apos;s sites.
              </td>
              <td>
                Find that customer&apos;s properties in the review queue and resolve every one
                of them, or promote the cohort that holds them all.
              </td>
            </tr>
            <tr>
              <th scope="row">Results say USD but the values were in another currency</th>
              <td>
                A portfolio brought in through the intake template records every value as USD
                and every peril as earthquake shaking, whatever the template said.
              </td>
              <td>
                Convert the values before uploading, or upload OED files instead (Route B),
                which keep the currency you state.
              </td>
            </tr>
            <tr>
              <th scope="row">A portfolio will not publish</th>
              <td>There are still blocking problems in the data.</td>
              <td>
                Fix them: see Validation findings on the Portfolios tab, and the findings on
                the Financial structure tab.
              </td>
            </tr>
            <tr>
              <th scope="row">The Analysis builder will not let you submit</th>
              <td>One of the four checklist items is not done.</td>
              <td>Follow the link beside the item that is not done.</td>
            </tr>
            <tr>
              <th scope="row">A run stops at publish_oed and names two model versions</th>
              <td>
                Oasis is using a different model version&apos;s package, probably because
                someone built another package since.
              </td>
              <td>Build this model version&apos;s package again (Build a model, part 8), then retry.</td>
            </tr>
            <tr>
              <th scope="row">A run stops at publish_oed and mentions a currency</th>
              <td>
                The portfolio is in a currency for which there is no approved exchange rate.
              </td>
              <td>Ask your administrator about getting an exchange rate approved for that currency.</td>
            </tr>
            <tr>
              <th scope="row">A run pauses at reconcile_keys</th>
              <td>Some of the portfolio&apos;s value could not be matched to a cell or a vulnerability function.</td>
              <td>
                Either fix the data and run again, or ask for an exception with a reason and
                resume once a reviewer approves.
              </td>
            </tr>
            <tr>
              <th scope="row">
                Properties are reported as outside the domain, meaning outside the area the grid
                covers (the technical label is fail_ap)
              </th>
              <td>
                They fall outside every tile of the grid, or outside the region the hazard was
                calculated for. Wrong coordinates, such as latitude and longitude swapped or a
                missing minus sign, are a common cause.
              </td>
              <td>
                Check their coordinates first. If they are correct, the grid needs another tile
                (in a new grid version), or the hazard needs calculating over a larger region.
              </td>
            </tr>
            <tr>
              <th scope="row">Properties fail with fail_v</th>
              <td>No vulnerability function matches the building details given.</td>
              <td>
                Check the construction, occupancy and number of storeys for those properties in
                the import review.
              </td>
            </tr>
            <tr>
              <th scope="row">There is no Approve for decision use button</th>
              <td>
                The result came from a research prototype, or your role is not reviewer or
                administrator.
              </td>
              <td>An approvable result needs a finished model version and a reviewer.</td>
            </tr>
          </tbody>
        </GuideTable>
      </Card>

      <Card title="Finding out more">
        <Bullets>
          <li>
            Every run keeps its full history on the <Link to="/runs">Run monitor</Link>. It
            also has a reference code (a correlation ID) that appears in the engines&apos;
            technical logs, which helps an administrator investigate a problem.
          </li>
          <li>
            If you are asking someone for help, send them the address of the screen you are
            on and the exact message shown. To point them at a section of this guide, send
            the address from your browser.
          </li>
          <li>
            For people who drive CASS from code: the API and its documentation are at{" "}
            <span className="mono">/api/docs/</span>.
          </li>
          <li>
            For administrators: how to install and run CASS is in{" "}
            <span className="mono">platform/docs/running-cass.md</span>, and the reasons
            behind the platform&apos;s design are in{" "}
            <span className="mono">platform/docs/adr/</span>.
          </li>
        </Bullets>
      </Card>
    </div>
  );
}
