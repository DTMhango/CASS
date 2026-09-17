/** Grids, cells and tiles: what a grid is, how CASS builds one, and how to build one. */

import { Link } from "react-router-dom";

import { Notice } from "@/components/primitives";

import { Card, Step, Steps, Bullets, Api, SectionLink, Terms, GuideTable, Example, Term, GlossaryLink } from "./parts";

export function Grids() {
  return (
    <div className="guide">
      <Card title="Why a model needs a grid">
        <div className="guide-prose">
          <p>
            To calculate a loss, CASS needs to know how hard the ground shakes under
            every property, for every one of thousands of imagined earthquakes.
            Calculating that separately for each address would take far too long, and
            earthquake science is not precise enough for the extra detail to help.
          </p>
          <p>
            So instead, CASS covers the country with a pattern of small rectangles,
            like a sheet of graph paper laid over a map. Each rectangle is called a{" "}
            <strong>cell</strong>. The shaking is calculated once for each cell, for
            each earthquake. Every property inside a cell is then given that
            cell&apos;s shaking. The whole pattern of cells is the <strong>grid</strong>
            .
          </p>
          <p>
            A grid is built once for a country, before any portfolio is brought in, and
            then kept fixed. That matters: every portfolio and every earthquake
            calculation for that country uses exactly the same cells, so the results of
            two runs can be fairly compared.
          </p>
          <p>
            You do not draw the cells one by one. You write a short set of instructions,
            called a <strong>specification</strong>, saying which areas to cover and how
            big the cells should be, and CASS creates the cells from it.
          </p>
          <p>
            You do not have to write a specification from nothing either. CASS ships a
            ready-made one, called a <strong>seed</strong>, for ten countries: Bangladesh,
            Bhutan, Indonesia, Kuwait, the Maldives, Nepal, Oman, the Philippines, Qatar
            and Türkiye. You load a seed into the form, change anything you want, and
            build it. The seeds are described further down this page.
          </p>
        </div>
      </Card>

      <Card title="What a cell is">
        <Bullets>
          <li>
            <strong>A rectangle drawn with lines of latitude and longitude.</strong> Its
            south and north edges are lines of latitude (the minimum and maximum
            latitude), and its west and east edges are lines of longitude (the minimum
            and maximum longitude). Those four numbers are all it takes to describe a
            cell.
          </li>
          <li>
            <strong>Measured in degrees, not kilometres.</strong> A cell 0.1° tall is
            always about 11.1 km tall. A cell 0.1° wide is about 11.1 km wide at the
            equator, but narrower further north or south, because lines of longitude
            get closer together towards the poles. In Jakarta, close to the equator, it
            is about 11.0 km wide, so the cells are nearly square. Far from the equator
            they are taller than they are wide.
          </li>
          <li>
            <strong>Numbered.</strong> Each cell has a number, its{" "}
            <strong>AreaPerilID</strong>. Other parts of CASS, such as the stored
            shaking, refer to a cell by its number.
          </li>
          <li>
            <strong>Never overlapping.</strong> Every point on the map is in exactly one
            cell. A point that lies exactly on the line between two cells is counted in
            the cell to its north or east, so there is never any doubt.
          </li>
          <li>
            <strong>Only a shape, to begin with.</strong> A newly built cell contains no
            shaking and no information about the ground. Shaking is added later, when a
            hazard calculation is run on the grid.
          </li>
        </Bullets>
      </Card>

      <Card title="The four parts of a specification">
        <div className="guide-prose">
          <p>
            A specification says four things: which areas to include (the tiles), how
            big the cells should be (the base resolution), where to use smaller cells
            (the refinements), and which cells to leave out because nothing insured can
            be in them (what the grid keeps). The picture shows the first three.
          </p>
        </div>
        <GridDiagram />
        <Terms>
          <Term term="1. Tiles: which areas to include">
            <p>
              A tile is a rectangle you draw around part of the country to say
              &ldquo;include this area&rdquo;. You describe it with four numbers (its
              minimum and maximum latitude and longitude), a name, and a short reason.
              All the tiles together make up the area the grid covers, called the{" "}
              <strong>domain</strong>.
            </p>
            <p>
              <strong>Why tiles exist.</strong> Tiles let you name the parts of a country
              you are modelling, and leave out parts you are not. The Indonesian seed, for
              example, has ten tiles, such as &ldquo;Sumatra&rdquo;, &ldquo;Java and
              Madura&rdquo; and &ldquo;Sangihe, Talaud and Miangas&rdquo;. Each has a reason
              a reviewer can read.
            </p>
            <p>
              <strong>Tiles do not have to follow the coast.</strong> A rectangle drawn
              around islands always holds a lot of sea: the eight rectangles of the first
              Indonesian grid held 52,831 cells, and only about 18,000 of them touched
              land. You could try to follow the coast with many small rectangles, but
              doing that for Indonesia takes about 2,000 of them, which nobody could
              check. Instead, draw a few simple rectangles and let the grid leave out the
              sea for you (part 4 below).
            </p>
            <p>
              <strong>A helpful way to picture it.</strong> Imagine one sheet of graph
              paper covering the whole world. Laying a tile is like cutting a window in a
              piece of card and placing it on top: the window decides which squares are
              used, but it never moves the squares themselves.
            </p>
            <p>
              <strong>What happens outside the tiles.</strong> An area outside every tile
              simply has no cells. A property there cannot be given any shaking, so CASS
              reports it as <GlossaryLink term="Domain">outside the domain</GlossaryLink>.
              It never quietly moves the property into
              the nearest cell, because that would give it the wrong shaking without
              anyone knowing.
            </p>
            <p>
              Tiles are allowed to touch or overlap. Where two tiles overlap, each cell is
              still only created once.
            </p>
          </Term>
          <Term term="2. Base resolution: how big the cells are">
            <p>
              The size of the cells, in degrees, everywhere except where a refinement
              asks for smaller ones. A base resolution of 0.1° makes cells about 11 km
              across. This one number has the biggest effect on how many cells the grid
              has.
            </p>
          </Term>
          <Term term="3. Refinements: where to use smaller cells">
            <p>
              A refinement is a rectangle where you want more detail, so the cells there
              are smaller than the base resolution. Like a tile, it has four numbers, a
              name and a reason, plus its own, smaller cell size.
            </p>
            <p>
              <strong>When to use one.</strong> Where larger cells would hide differences
              that matter: for example in a city with a lot of insured value, or where
              the ground changes quickly from hard rock to soft soil, so shaking can be
              very different a few kilometres apart. The Indonesian grid refines Jakarta,
              Bandung, Surabaya, Denpasar and Medan to 0.025°, about 2.8 km.
            </p>
            <p>
              <strong>How it fits in.</strong> The small cells{" "}
              <strong>replace</strong> the large cells in that area; they are not laid on
              top of them, so no place is ever in two cells. For that to work, a
              refinement has to fit exactly over whole large cells, and CASS checks three
              things:
            </p>
            <Bullets>
              <li>
                Its cells must be smaller than the base resolution, or it adds no detail.
              </li>
              <li>
                Its cell size must divide the base cell size a whole number of times. With
                a base of 0.1°, a refinement of 0.05° (2 across) or 0.025° (4 across)
                fits; 0.03° does not, because three and a bit small cells would not fill a
                large one.
              </li>
              <li>
                Its four edges must be whole multiples of the base cell size. With a base
                of 0.1°, edges at 106.5 and 107.1 are fine; an edge at 106.53 is not.
              </li>
            </Bullets>
            <p>
              <strong>What goes wrong otherwise.</strong> Before CASS checked this, a
              refinement drawn with edges at 0.06 and 0.14 over a 0.1° base put a quarter
              of the places inside it into two cells at once, and one drawn at 0.05 to 0.17
              left three fifths of its area in no cell at all. Now CASS refuses such a
              refinement and tells you the nearest box that would fit, for example
              &ldquo;latitude 0 to 0.2, longitude 0 to 0.2&rdquo;.
            </p>
          </Term>
          <Term term="4. What the grid keeps: land, and places near people or buildings">
            <p>
              Two tick boxes decide which of the tiles&apos; cells are kept. Both are ticked
              when you start a new specification.
            </p>
            <p>
              <strong>Keep only cells that touch the country&apos;s land.</strong> CASS
              compares every cell with the country&apos;s outline and leaves out cells that
              are entirely over the sea, or entirely in another country. The outline comes
              from Natural Earth, a free world map that ships with CASS. A cell is kept if
              any part of it touches land, not just its middle: checking only the middle
              would have dropped 9% of Indonesia&apos;s cells, all of them on coasts, where
              many insured buildings are.
            </p>
            <p>
              The <strong>coast buffer</strong> keeps cells a little way out to sea as well,
              5 km unless you change it. The map&apos;s coastlines are only accurate to
              about 5 km, and an address can be placed just offshore of a coastline drawn
              that way.
            </p>
            <p>
              <strong>Skip land with no buildings or people nearby.</strong> Land is not
              the same as places where insured property can be: the middle of a desert,
              a mountain range or a rainforest has no buildings. CASS reads a world map of
              buildings and residents, made from satellite images by the European
              Commission (the Global Human Settlement Layer), and leaves out cells that are
              further than the <strong>settlement buffer</strong> from any building or
              resident. Using both buildings and residents matters: a factory or a port
              can have buildings and no residents.
            </p>
            <p>
              The settlement buffer is 5 km unless you change it. That number was tested
              against the real locations in KRE&apos;s geocoded portfolio: at 2 km, two of
              224 locations would have been left out; at 5 km, none were.
            </p>
            <p>
              <strong>What these cannot see.</strong> The outline leaves out many very
              small islands (it draws only 176 of the Maldives&apos; roughly 1,190), and
              the building map can miss an isolated site such as a mine, a dam or a
              pipeline station far from any town. A property in a cell that was left out
              is reported as outside the grid, never moved or silently dropped. If your
              book has remote sites, check they are inside before relying on the grid.
            </p>
          </Term>
        </Terms>
      </Card>

      <Card title="How CASS turns a specification into cells">
        <div className="guide-prose">
          <p>
            You do not need to know this to build a grid, but it explains some things you
            may notice, such as a tile covering slightly more area than you typed. When
            you press <strong>Build the grid</strong>, CASS does the following.
          </p>
        </div>
        <Steps>
          <Step number={1} title="Fill each refinement with small cells">
            <p>
              For every refinement, CASS covers its rectangle with cells of the
              refinement&apos;s size.
            </p>
          </Step>
          <Step number={2} title="Fill each tile with base cells, skipping refined areas">
            <p>
              For every tile, CASS covers its rectangle with cells of the base size. If a
              base cell&apos;s centre falls inside a refinement, that base cell is left out,
              because the refinement has already covered that ground with smaller cells.
            </p>
          </Step>
          <Step number={3} title="Line every cell up on one worldwide pattern">
            <p>
              Cell edges are always placed at whole multiples of the cell size, counted
              from latitude 0 and longitude 0, rather than starting from the corner of
              your tile. This is the graph paper from the picture above: the squares are
              always in the same places, whatever window you cut.
            </p>
            <p>
              For example, with 0.1° cells, cell edges fall at 106.0, 106.1, 106.2 and so
              on. If you type a tile edge of 106.03, the first cell still starts at 106.0,
              which is why a tile can cover slightly more ground than the numbers you
              typed. The benefit is that neighbouring tiles line up exactly instead of
              leaving thin gaps or slivers between them, and a refinement&apos;s small
              cells fit neatly inside the large ones.
            </p>
          </Step>
          <Step number={4} title="Remove duplicates">
            <p>Any cell covered by two overlapping tiles is kept only once.</p>
          </Step>
          <Step number={5} title="Leave out the sea and empty land">
            <p>
              If the specification asks for it, CASS leaves out every cell that does not
              touch the country&apos;s land (allowing for the coast buffer), and every cell
              further than the settlement buffer from any building or resident. It counts
              how many cells each rule left out, and the build message tells you.
            </p>
          </Step>
          <Step number={6} title="Put the cells in order and number them">
            <p>
              The cells are sorted from south to north, and from west to east within each
              row, then numbered 1, 2, 3 and so on, starting from the south-west corner.
              Because the order is always the same, the same specification always
              produces the same numbers.
            </p>
          </Step>
          <Step number={7} title="Save and register the grid">
            <p>
              The cells are saved as a table, with one row per cell giving its number,
              its four edges and its country. The grid is added to the list of grids as a{" "}
              <strong>draft</strong>. It is given a reference made from the country code,
              the word &ldquo;grid&rdquo; and the version, for example{" "}
              <span className="mono">id-grid-1.0.0</span>.
            </p>
          </Step>
        </Steps>
      </Card>

      <Card title="How many cells, and why it matters">
        <div className="guide-prose">
          <p>
            Every cell has to have its shaking calculated for every imagined earthquake,
            and that shaking has to be stored. So more cells means a longer hazard
            calculation, more storage, and a slower loss calculation. Choosing the cell
            size is a balance between detail and time.
          </p>
          <p>
            The number of cells grows faster than you might expect.{" "}
            <strong>Halving the cell size makes four times as many cells</strong>,
            because each old cell is split in half both across and down, into four. On
            the grid screen this is described as &ldquo;cost is quadratic&rdquo;.
          </p>
        </div>
        <GuideTable>
          <caption className="visually-hidden">Cell counts at different resolutions</caption>
          <thead>
            <tr>
              <th scope="col">Area covered</th>
              <th scope="col">Cell size</th>
              <th scope="col" className="numeric">
                Number of cells
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">One tile, 2° by 2° (about 220 km square)</th>
              <td>0.1° (about 11 km)</td>
              <td className="numeric">400</td>
            </tr>
            <tr>
              <th scope="row">The same tile</th>
              <td>0.05° (about 5.5 km)</td>
              <td className="numeric">1,600</td>
            </tr>
            <tr>
              <th scope="row">The same tile</th>
              <td>0.025° (about 2.8 km)</td>
              <td className="numeric">6,400</td>
            </tr>
            <tr>
              <th scope="row">All of Indonesia</th>
              <td>0.1°</td>
              <td className="numeric">52,831</td>
            </tr>
            <tr>
              <th scope="row">All of Indonesia</th>
              <td>0.01° (about 1 km)</td>
              <td className="numeric">about 5,000,000</td>
            </tr>
            <tr>
              <th scope="row">
                All of Indonesia, keeping only land near people or buildings (the seed)
              </th>
              <td>0.025°, with ten cities at 0.0125° (about 1.4 km)</td>
              <td className="numeric">270,769</td>
            </tr>
          </tbody>
        </GuideTable>
        <div className="guide-prose">
          <p>
            To help you judge this, the grid screen counts the cells while you type, so
            you can see the effect of a change before building anything. Once the
            specification is complete, the count is exact: it is the number the build
            will produce, after refinements have replaced large cells and the sea and
            empty land have been left out. This installation will not build a grid with
            more than 500,000 cells (unless an administrator has changed that limit), and
            the screen warns you before you reach it.
          </p>
          <p>
            Beside the count, the screen shows the most the grid&apos;s hazard could take
            up on disk for every 1,000 imagined years. For example, a grid of 100,000
            cells shows &ldquo;at most 2.8 GB for every thousand simulated years&rdquo;,
            so a hazard run of the usual 10,000 years could store up to about 28 GB. The
            seed grid for all of Indonesia, 270,769 cells, could store up to about 77 GB.
            These are ceilings, measured on the Jakarta and Bandung area, which has the
            strongest shaking CASS has calculated; quieter areas store less. CASS only
            stores shaking of 0.05 g or more, because no building in the world
            earthquake model is damaged by less.
          </p>
          <p>
            The count is not a time estimate. On this installation, a hazard run of
            10,000 imagined years for the 962 cells of Jakarta and Bandung took about 9
            minutes from start to finish, but time does not grow simply with the number
            of cells, so CASS does not predict it for a whole country.
          </p>
        </div>
      </Card>

      <Card title="Worked example: counting a tile, a refinement and the sea">
        <Example title="One tile, one refinement, and a coast">
          <p>
            <strong>The tile</strong> runs from longitude 106.0 to 108.0 (2 degrees) and
            from latitude −7.5 to −5.5 (also 2 degrees), with cells of 0.1°. Two degrees
            divided by 0.1 is 20, so the tile has 20 columns and 20 rows:{" "}
            <strong>400 cells</strong>.
          </p>
          <p>
            <strong>The refinement</strong> runs from longitude 106.6 to 107.0 and
            latitude −6.4 to −6.0 (0.4 degrees each way), with cells of 0.025°. 0.4
            divided by 0.025 is 16, so it has 16 columns and 16 rows:{" "}
            <strong>256 small cells</strong>. That same area held 4 columns and 4 rows of
            the large cells, which is 16 large cells, and those 16 are removed. Its edges
            (106.6, 107.0, −6.4 and −6.0) are all multiples of 0.1, and 0.025 goes into 0.1
            exactly four times, so it fits.
          </p>
          <p>
            <strong>So far</strong> the grid has 400 − 16 + 256 = <strong>640 cells</strong>.
          </p>
          <p>
            <strong>Now suppose the northern part of the tile is sea.</strong> With
            &ldquo;Keep only cells that touch the country&apos;s land&rdquo; ticked, CASS
            leaves out every cell that is entirely over the sea, including any more than
            5 km from the coast. If that is 150 large cells, the counter shows{" "}
            <strong>490 cells</strong> and says that 150 over the sea are left out.
          </p>
          <p>
            <strong>When the counter says &ldquo;at most&rdquo;.</strong> While the
            specification is still unfinished, or has a problem the build would refuse,
            CASS cannot count exactly. It then shows a number that can only be too high,
            never too low: it adds the 400 and the 256 without subtracting anything. That
            way, if it says you are under the limit, you are.
          </p>
        </Example>
      </Card>

      <Card title="Build a grid, step by step">
        <Notice tone="info" title="Who can do this, and where">
          Catastrophe modellers and administrators can build grids. Go to{" "}
          <Link to="/models?tab=build">Models → Build</Link> and find the card called{" "}
          <strong>Build a grid</strong>.
        </Notice>
        <Steps>
          <Step number={1} title="Start from a seed, if there is one" where="Start from a country → Seed → Load into the form">
            <p>
              If your country is one of the ten CASS ships a seed for, choose it under{" "}
              <strong>Seed</strong> and press <strong>Load into the form</strong>. Every
              field below is filled in, including the reasons and open questions. It
              replaces anything already in the form. Nothing is built until you press{" "}
              <strong>Build the grid</strong>, so you can change whatever you like first.
              If you use a seed, check each step below rather than skipping them.
            </p>
          </Step>
          <Step number={2} title="Choose the country" where="Country">
            <p>
              Start typing the country&apos;s name or its two-letter code, for example
              &ldquo;Nepal&rdquo; or &ldquo;NP&rdquo;, and pick it from the list. The list
              holds every country CASS has an outline for, which is what lets it leave out
              the sea. Countries with a seed say so.
            </p>
          </Step>
          <Step number={3} title="Give it a version" where="Version">
            <p>
              Type a version, such as <span className="mono">1.0.0</span>. You can use
              any text, but numbers that go up with each change are easiest to follow.
            </p>
            <p>
              <strong>Why the version matters.</strong> The stored shaking, model versions
              and past results all refer to cells by their numbers. If the shape of a grid
              changed but kept the same version, cell 1,204 might suddenly mean a
              different place, and everything built on the old grid would quietly point at
              the wrong ground. So <strong>whenever you change the tiles, the resolution
              or the refinements, use a new version</strong>. Building again with a version
              that already exists replaces that version&apos;s cells, so only do that for a
              grid nothing has been built on yet.
            </p>
          </Step>
          <Step number={4} title="Give it a label" where="Label">
            <p>
              A name people will recognise in lists, such as &ldquo;Indonesia national
              grid&rdquo;.
            </p>
          </Step>
          <Step number={5} title="Set the base resolution" where="Base resolution (degrees)">
            <p>
              Type the cell size in degrees as a decimal number, for example{" "}
              <span className="mono">0.1</span> for cells about 11 km across. It is saved
              exactly as you type it. If you are unsure, 0.1 is a reasonable size for a
              whole country; the table above shows how other sizes change the number of
              cells.
            </p>
          </Step>
          <Step number={6} title="Leave the mapping tolerance at 0" where="Mapping tolerance (km)">
            <p>
              This number is saved with the grid as a note, but it does not change
              anything today: CASS always reports properties outside the tiles rather
              than moving them into a nearby cell. Leave it at 0 unless you have been asked
              to record a particular value.
            </p>
          </Step>
          <Step number={7} title="Choose what the grid keeps" where="What the grid keeps">
            <p>
              Leave both boxes ticked unless you have a reason not to:{" "}
              <strong>Keep only cells that touch the country&apos;s land</strong> and{" "}
              <strong>Skip land with no buildings or people nearby</strong>. Leave both
              buffers at 5 km unless you have been asked to use something else.
            </p>
            <p>
              <strong>When to untick the land box.</strong> When the country&apos;s outline
              is known to be incomplete. The Maldives seed does this: the outline is
              missing most of its islands, so the seed relies on the buildings map
              instead.
            </p>
            <p>
              <strong>When to widen the settlement buffer.</strong> When your book has
              isolated sites far from towns, such as mines, dams, hydropower plants or oil
              and gas facilities. A wider buffer keeps more cells, and the counter shows
              how many.
            </p>
          </Step>
          <Step number={8} title="Add the tiles" where="Tiles">
            <p>The form starts with one empty tile. For each tile, fill in:</p>
            <Bullets>
              <li>
                <strong>Name</strong>: where it is, such as &ldquo;Java and Madura&rdquo;.
              </li>
              <li>
                <strong>Reason</strong>: why this area is included, such as &ldquo;Largest
                concentration of insured value&rdquo;. A reviewer reads this later.
              </li>
              <li>
                <strong>Minimum latitude and maximum latitude</strong>: the southern and
                northern edges. Remember that south of the equator is negative. For a tile
                running from 9° south to 5.7° south, the minimum is{" "}
                <span className="mono">-9.0</span> and the maximum is{" "}
                <span className="mono">-5.7</span> (−9 is smaller than −5.7).
              </li>
              <li>
                <strong>Minimum longitude and maximum longitude</strong>: the western and
                eastern edges. East of Greenwich is positive, so for Indonesia both are
                positive numbers, with the minimum being the western edge.
              </li>
            </Bullets>
            <p>
              The minimum must always be smaller than the maximum. To add another tile,
              press <strong>Add a tile</strong>. The cell counter starts working as soon
              as one tile has all four numbers filled in.
            </p>
            <p>
              With the land box ticked, a tile can safely include sea and neighbouring
              countries, so draw it generously around the land you mean. Drawing it too
              tightly is the mistake to avoid: land left outside every tile has no cells.
            </p>
            <p>
              <strong>Tip:</strong> to find the latitude and longitude of a place, use any
              online map. Many let you right-click a point to copy its coordinates, given
              as latitude first, then longitude.
            </p>
          </Step>
          <Step number={9} title="Add refinements, if you want any" where="Refinements">
            <p>
              Refinements are optional. To add one, press{" "}
              <strong>Add a refinement</strong> and fill in the same fields as a tile,
              plus its <strong>resolution</strong>: the smaller cell size. It must divide
              the base resolution exactly (for example 0.05 or 0.025 with a base of 0.1),
              and the four edges must be multiples of the base resolution (for example
              106.5 and 107.1 with a base of 0.1). If they are not, the counter says so
              and suggests a box that would fit.
            </p>
            <p>
              Place refinements inside a tile. A refinement outside every tile still
              creates cells, which is rarely what you want.
            </p>
          </Step>
          <Step number={10} title="Write down anything still undecided" where="Open questions and Notes">
            <p>
              Under <strong>Open questions</strong>, write one question per line about
              anything the grid does not settle, such as &ldquo;How should coastlines be
              handled?&rdquo;. These are saved with the grid, so a later reader can tell
              the difference between something that was decided and something that was
              never considered. Use <strong>Notes</strong> for anything else worth
              recording.
            </p>
          </Step>
          <Step number={11} title="Check the cell counter">
            <p>
              Below the tick boxes, the screen shows how many cells your specification will
              make, split into base cells and the cells in each refinement, how many cells
              over the sea and on empty land were left out, and the most the grid&apos;s
              hazard could store. Look at it before building.
            </p>
            <p>
              A yellow warning called{" "}
              <strong>Some of the country&apos;s land is in no tile</strong> lists the
              approximate latitude and longitude of land your tiles missed, largest
              first. For example, &ldquo;near 5.55, 126.6&rdquo; is Miangas, a small
              Indonesian island north of Sulawesi. Widen a tile or add one to include it.
            </p>
            <p>
              A yellow warning called <strong>The build would refuse this</strong> or{" "}
              <strong>More cells than this installation builds</strong> means CASS would
              not build it, and says why. To reduce the number of cells, make the base
              resolution larger (for example 0.05 instead of 0.025), make the tiles
              smaller, or make the refinements cover less area.
            </p>
          </Step>
          <Step number={12} title="Build it" where="Build the grid">
            <p>
              Press <strong>Build the grid</strong>. The button is only available once the
              country, version, label and base resolution are filled in and the
              specification is under the cell limit.
            </p>
            <p>
              A green message confirms the grid was built, with its reference, its number
              of cells, and how many cells over the sea and on empty land were left out.
              The grid then appears in the table{" "}
              <strong>Grids in the registry</strong> further down, marked as a draft.
              Building a grid does not approve it; it is simply ready to be used in a model
              version.
            </p>
          </Step>
        </Steps>
        <Api>{"POST /api/v1/grids/build/ · POST /api/v1/grids/estimate/ (the cell count only) · GET /api/v1/grids/seeds/ · GET /api/v1/grids/seeds/{code}/ · GET /api/v1/grids/countries/"}</Api>
      </Card>

      <Card title="The seed grids CASS ships">
        <div className="guide-prose">
          <p>
            A seed is a complete specification for one country, written and checked when
            CASS was built. Every seed follows the same rule, so the choices behind them
            are the same everywhere:
          </p>
        </div>
        <Bullets>
          <li>
            <strong>Cells of 0.0125° (about 1.4 km) wherever the country allows.</strong>{" "}
            That is about as fine as the world&apos;s soil data, so finer cells would add
            calculation without adding information.
          </li>
          <li>
            <strong>Cells of 0.025° (about 2.8 km) where 0.0125° would be too many.</strong>{" "}
            The test is whether the country&apos;s inhabited land fits in 300,000 cells,
            which leaves room under the 500,000 limit. Where it does not, the largest
            cities keep 1.4 km cells as refinements.
          </li>
          <li>
            <strong>Tiles that name regions, with the sea and empty land left out</strong>{" "}
            by the two tick boxes, each with a 5 km buffer. Each seed was checked to leave
            none of its country&apos;s land outside every tile.
          </li>
        </Bullets>
        <GuideTable>
          <caption className="visually-hidden">The seed grids</caption>
          <thead>
            <tr>
              <th scope="col">Country</th>
              <th scope="col">Cell size</th>
              <th scope="col">Refined cities</th>
              <th scope="col" className="numeric">
                Cells
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Indonesia</th>
              <td>0.025°</td>
              <td>
                Jakarta, Surabaya, Bandung, Medan, Semarang, Makassar, Palembang,
                Denpasar, Yogyakarta, Padang
              </td>
              <td className="numeric">270,769</td>
            </tr>
            <tr>
              <th scope="row">Philippines</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">220,318</td>
            </tr>
            <tr>
              <th scope="row">Türkiye</th>
              <td>0.025°</td>
              <td>
                Istanbul, Kocaeli, Izmir, Ankara, Bursa, Antalya, Adana, Gaziantep,
                Kahramanmaras, Antakya
              </td>
              <td className="numeric">144,678</td>
            </tr>
            <tr>
              <th scope="row">Oman</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">130,825</td>
            </tr>
            <tr>
              <th scope="row">Bangladesh</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">94,166</td>
            </tr>
            <tr>
              <th scope="row">Nepal</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">92,132</td>
            </tr>
            <tr>
              <th scope="row">Bhutan</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">24,583</td>
            </tr>
            <tr>
              <th scope="row">Maldives</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">16,185</td>
            </tr>
            <tr>
              <th scope="row">Kuwait</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">13,840</td>
            </tr>
            <tr>
              <th scope="row">Qatar</th>
              <td>0.0125°</td>
              <td>None needed</td>
              <td className="numeric">8,752</td>
            </tr>
          </tbody>
        </GuideTable>
        <div className="guide-prose">
          <p>
            <strong>The Maldives is different.</strong> The world outline CASS uses draws
            only 176 of the Maldives&apos; roughly 1,190 islands, so leaving out cells
            &ldquo;not touching land&rdquo; would drop real islands. Its seed leaves the
            land box unticked and keeps cells within 5 km of any building or resident
            instead. Its open questions also note that ground shaking there is low, and
            that the earthquake-related losses it has suffered came from tsunami, which
            CASS does not model.
          </p>
          <p>
            Every seed carries open questions like these, written for that country. Read
            them before using the grid.
          </p>
        </div>
      </Card>

      <Card title="Choosing tiles and a cell size">
        <div className="guide-prose">
          <p>
            There is no single correct grid, which is why CASS asks for a reason beside
            every tile and refinement: so others can understand and question your
            choices. These questions help.
          </p>
        </div>
        <Bullets>
          <li>
            <strong>Is every insured property inside the grid?</strong> A property outside
            the grid cannot be modelled. The first Indonesian grid gained its Bangka
            Belitung tile only after a real property turned out to be in the gap between
            two tiles; now the counter warns about land outside every tile before you
            build. A property can also fall in a cell left out as empty land. A{" "}
            <strong>Geometry only</strong> run (see{" "}
            <SectionLink section="run">Run and read results</SectionLink>) shows which
            properties fall outside, without the time and cost of a full loss calculation.
          </li>
          <li>
            <strong>Are the cells smaller than your addresses are accurate?</strong> If
            many properties are only located to a postcode or town, making the cells
            smaller than that area does not make their losses more accurate; it only adds
            cells. The geocoding sensitivity report in the import review shows how much
            value this affects.
          </li>
          <li>
            <strong>Where does the shaking change quickly?</strong> In places where soft
            ground sits next to hard rock, such as the basins under Jakarta and Bandung,
            shaking can be very different only a few kilometres apart. Those are good
            places for a refinement.
          </li>
          <li>
            <strong>Is the refinement based on the ground, or on today&apos;s
            portfolio?</strong> Refining around where your current properties happen to be
            is a reasonable start. But the grid is meant to stay fixed while portfolios
            change, so refinements based on where the ground or the shaking changes
            quickly will stay useful for longer.
          </li>
          <li>
            <strong>How much can the hazard take up?</strong> Every cell&apos;s shaking is
            stored for every imagined year, so a finer grid costs disk space for as long as
            its hazard is kept. The counter&apos;s storage figure is the most it could be.
            Once a hazard calculation has been run on a grid, changing the grid means
            running the hazard again (see{" "}
            <SectionLink section="build">Build a model</SectionLink>), so settle the grid
            first.
          </li>
          <li>
            <strong>How much detail can the data support?</strong> The world data about
            soil and ground conditions is about 1 km across at its finest, and a hazard
            model&apos;s own site data is often coarser, 10 km for the Indonesian model. Cells
            much smaller than about 1 km add calculation without adding real information.
          </li>
        </Bullets>
      </Card>
    </div>
  );
}

/**
 * Two tiles on one lattice, with a refinement inside the first.
 *
 * The lattices are SVG patterns anchored at the drawing's origin rather than at
 * each tile, which is the rule the generator follows too: every tile shares one
 * worldwide lattice, so their cell edges line up.
 */
function GridDiagram() {
  return (
    <figure className="guide-figure">
      <div className="guide-figure__frame">
        <svg
          className="guide-grid"
          viewBox="0 0 520 300"
          role="img"
          aria-label="Tiles, base cells and a refinement"
          aria-describedby="guide-grid-caption"
        >
          <defs>
            <pattern id="guide-base" width="40" height="40" patternUnits="userSpaceOnUse">
              <path className="guide-grid__base-line" d="M40 0H0V40" />
            </pattern>
            <pattern id="guide-fine" width="10" height="10" patternUnits="userSpaceOnUse">
              <path className="guide-grid__fine-line" d="M10 0H0V10" />
            </pattern>
          </defs>

          <text className="guide-grid__label" x="40" y="70">
            Tile 1: Java
          </text>
          <rect className="guide-grid__cells" x="40" y="80" width="280" height="160" />
          <rect className="guide-grid__refinement" x="120" y="120" width="80" height="80" />
          <rect className="guide-grid__fine" x="120" y="120" width="80" height="80" />
          <rect className="guide-grid__tile" x="40" y="80" width="280" height="160" />
          <rect className="guide-grid__refinement-edge" x="120" y="120" width="80" height="80" />

          <text className="guide-grid__label" x="360" y="110">
            Tile 2: Bali
          </text>
          <rect className="guide-grid__cells" x="360" y="120" width="120" height="80" />
          <rect className="guide-grid__tile" x="360" y="120" width="120" height="80" />

          <path className="guide-grid__leader" d="M190 130 L240 40" />
          <text className="guide-grid__label guide-grid__label--accent" x="246" y="30">
            Refinement: Jakarta
          </text>
          <text className="guide-grid__note" x="246" y="46">
            small cells replace the base cells
          </text>

          <path className="guide-grid__leader" d="M60 250 L60 236" />
          <text className="guide-grid__note" x="40" y="264">
            One base cell, 0.1° (about 11 km)
          </text>

          <text className="guide-grid__note" x="360" y="226">
            Sea between tiles:
          </text>
          <text className="guide-grid__note" x="360" y="242">
            no cells at all
          </text>

          <text className="guide-grid__axis" x="260" y="292" textAnchor="middle">
            longitude (further east →)
          </text>
          <text
            className="guide-grid__axis"
            x="14"
            y="160"
            textAnchor="middle"
            transform="rotate(-90 14 160)"
          >
            latitude (further north →)
          </text>
        </svg>
      </div>
      <figcaption className="guide-figure__caption" id="guide-grid-caption">
        Two tiles are drawn with thick dark outlines: a large one for Java and a smaller
        one for Bali. The thin grey squares inside them are the base cells. Both tiles
        sit on the same pattern of squares, so their cell edges line up. Inside the Java
        tile, the blue area is a refinement for Jakarta: four large cells have been
        replaced by 64 small cells, each a quarter of the width. The sea between the two
        tiles has no cells at all, so a property there would be reported as outside the
        domain.
      </figcaption>
    </figure>
  );
}
