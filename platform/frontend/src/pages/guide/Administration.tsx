/** Administration: what the screen shows, who can change what, and what to quote when asking for help. */

import { Link } from "react-router-dom";

import { Notice } from "@/components/primitives";

import { Card, Bullets, GuideTable, SectionLink, Terms, Term } from "./parts";

export function Administration() {
  return (
    <div className="guide">
      <Card title="Who this section is for">
        <div className="guide-prose">
          <p>
            <Link to="/administration">Administration</Link> is the screen that describes the
            installation itself: which versions of everything are running, whether the
            calculation engines are reachable, who has which role, and a record of every
            governed action anyone has taken.
          </p>
          <p>
            <strong>Everyone can open it and read most of it.</strong> That is deliberate: if
            you are asking a colleague or a supplier for help, the facts they will ask for
            are on this screen, and you should not need an administrator to read them out.
            Only a platform administrator can change anything, and a message at the top says
            so when you cannot.
          </p>
        </div>
      </Card>

      <Card title="Engine health">
        <div className="guide-prose">
          <p>
            The first card, and the one to check when something is not working. CASS does the
            calculation through two separate engines, OpenQuake for the earthquakes and Oasis
            for the losses, and this card asks each one whether it is alive and reports what
            came back.
          </p>
          <p>
            A red message naming an engine means runs that need it will fail until it is
            back. That is an administrator&apos;s problem to fix, not yours, but seeing it
            here saves you investigating a run that was never going to work. CASS asks the
            engines through its own API, so what you see is what the platform can reach, not
            what your browser can.
          </p>
        </div>
      </Card>

      <Card title="Exposure data standards">
        <div className="guide-prose">
          <p>
            Which version of OED (the industry exposure format) CASS checks your portfolios
            against, and any other versions registered beside it. Worth quoting if someone
            sends you files from another system and the import complains about a column.
          </p>
        </div>
      </Card>

      <Card title="This installation">
        <div className="guide-prose">
          <p>
            The identifying facts about this copy of CASS. Quote them in full when you report
            a problem.
          </p>
        </div>
        <Terms>
          <Term term="API version">
            The version of the CASS platform itself.
          </Term>
          <Term term="OED schema">
            The exposure format version portfolios are checked against.
          </Term>
          <Term term="Artifact backend">
            Where the large files runs produce are stored.
          </Term>
          <Term term="Default resource profile">
            The computing power a run is given when nobody chooses otherwise.
          </Term>
          <Term term="Use of model data">
            A standing statement that the models and data CASS carries are used inside
            Klapton Re only: not sold and not passed outside the company.
          </Term>
        </Terms>
      </Card>

      <Card title="Tested engine combinations">
        <div className="guide-prose">
          <p>
            The combinations of engine versions this installation has actually been tested
            against. It matters because a model package built under one combination is not
            guaranteed to behave the same under another, so CASS records which ones have
            passed their checks rather than assuming any version will do.
          </p>
        </div>
      </Card>

      <Card title="Execution profiles, and queues, storage and retention">
        <div className="guide-prose">
          <p>
            An <strong>execution profile</strong> (called <strong>Resource profile</strong>{" "}
            when you set up a run) is a named allowance of computing power: so many
            processors, so much memory, a time limit, and a limit on how many runs may use it
            at once. They exist so one very large job cannot take over the machine and stop
            everyone else working.
          </p>
          <p>
            <strong>Queues, storage and retention</strong>, which only an administrator sees,
            shows what each profile is running now, how much the file store holds, and what
            the housekeeping sweep will delete next. If your run is queued rather than
            running, this is where you find out what it is waiting behind.
          </p>
        </div>
      </Card>

      <Card title="Users and roles">
        <div className="guide-prose">
          <p>
            Everyone with an account, and what each may do. The table has a row per person
            and these columns:
          </p>
        </div>
        <GuideTable>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">What it means</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Person</th>
              <td>Their name and username.</td>
            </tr>
            <tr>
              <th scope="row">Platform role</th>
              <td>
                Portfolio analyst, Underwriter, Catastrophe modeller, Reviewer or Platform
                administrator. See{" "}
                <SectionLink section="start">Start here</SectionLink> for what each can do.
              </td>
            </tr>
            <tr>
              <th scope="row">May publish models</th>
              <td>Whether they can build and publish model versions: modellers and administrators.</td>
            </tr>
            <tr>
              <th scope="row">May decide gates</th>
              <td>
                Whether they can approve or reject work at a gate: reviewers and
                administrators. Nobody can approve their own request, whatever this says.
              </td>
            </tr>
            <tr>
              <th scope="row">Local install</th>
              <td>Whether they are allowed to run an approved copy of CASS on their own machine.</td>
            </tr>
            <tr>
              <th scope="row">Access</th>
              <td>Shown to administrators only: where a role is changed.</td>
            </tr>
          </tbody>
        </GuideTable>
        <div className="guide-prose">
          <p>
            A platform role decides what somebody may do anywhere in CASS. It does not decide
            which projects they can see: that is project membership, granted separately per
            project.
          </p>
        </div>
      </Card>

      <Card title="Audit trail">
        <div className="guide-prose">
          <p>
            An append-only record of every governed action: who did it, when, and what it
            touched. Nothing in it can be edited or deleted, which is what makes it worth
            having.
          </p>
          <p>
            Use it to answer &ldquo;who approved this?&rdquo; or &ldquo;what changed between
            these two runs?&rdquo;. You can filter it, including by the correlation ID that a
            run carries, which ties together everything that run did.
          </p>
        </div>
        <Notice tone="info" title="If a filtered search comes back empty">
          An empty trail under a correlation ID usually means the ID came from somewhere
          other than a run. Copy it from the run in the{" "}
          <Link to="/runs">Run monitor</Link> rather than from a log message.
        </Notice>
      </Card>

      <Card title="What to send when you ask for help">
        <Bullets>
          <li>What you were trying to do, and the exact message on screen.</li>
          <li>The address in your browser&apos;s address bar.</li>
          <li>
            From <strong>This installation</strong>: the API version and the OED schema.
          </li>
          <li>
            For a run that failed: its correlation ID, and the step it stopped on, from the{" "}
            <Link to="/runs">Run monitor</Link>.
          </li>
          <li>
            Whether <strong>Engine health</strong> shows anything unreachable.
          </li>
        </Bullets>
      </Card>
    </div>
  );
}
