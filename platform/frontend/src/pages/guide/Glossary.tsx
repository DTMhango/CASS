
import { Bullets, Card, SectionLink, Terms, Term } from "./parts";

/**
 * Every term the screens use, explained for a reader who has not met it.
 *
 * Grouped by where a person meets the term rather than alphabetically: someone
 * reading the Build tab wants the model terms together. Each entry says what
 * the thing is, and where it helps, why it exists or an everyday example.
 */
export function Glossary() {
  return (
    <div className="guide">
      <Card title="How this glossary is organised">
        <div className="guide-prose">
          <p>
            The terms are grouped by where you will meet them: first the ideas behind
            any catastrophe model, then the terms used when building a grid, the hazard
            and the vulnerability, then the terms for portfolios and insurance, and last
            the terms used when running a calculation and reading its results. Use your
            browser&apos;s find command (Ctrl+F, or Cmd+F on a Mac) to jump to a term.
          </p>
        </div>
      </Card>

      <Card title="The basic ideas" description="What every catastrophe model is built on.">
        <Terms>
          <Term term="Catastrophe model">
            A calculation that estimates the losses disasters could cause, including
            disasters that have not happened yet. It works by imagining a very large
            number of possible disasters and working out the damage from each. Every
            catastrophe model has three parts: the hazard (how strong the disaster is
            at each place), the vulnerability (how much damage that does), and the
            financial terms (who pays for the damage).
          </Term>
          <Term term="Hazard">
            How strong a natural event is at a particular place. For an earthquake,
            that means how hard the ground shakes at that spot. The same earthquake
            shakes some places much harder than others: places closer to where it
            starts usually shake harder, and so do places on soft ground.
          </Term>
          <Term term="Vulnerability">
            How easily a building is damaged by a given strength of shaking. It is
            measured as the cost of the damage as a share of what it would cost to
            rebuild the building completely. Two buildings next to each other can
            have very different vulnerability: an old building of unreinforced brick
            might be badly damaged by shaking that a modern building designed for
            earthquakes survives with little harm.
          </Term>
          <Term term="Exposure">
            The things that could be damaged, and what they are worth. In CASS that
            means the insured properties: where each one is, what it is built from,
            what it is used for, how tall it is, and its value. Your portfolio is your
            exposure.
          </Term>
          <Term term="Peril and sub-peril">
            A peril is a type of disaster, such as earthquake, flood or windstorm. A
            sub-peril is one particular way that disaster causes damage. An earthquake
            can damage buildings by shaking them, and also by causing a tsunami, a fire,
            a landslide, or by turning wet ground soft (liquefaction). CASS models the
            shaking only. Damage from the other sub-perils is not included in its
            numbers.
          </Term>
          <Term term="Event">
            One imagined earthquake: where it happens, how large it is, and how hard
            it shakes each place. Events are not earthquakes that really happened.
            They are earthquakes that could plausibly happen, invented by the hazard
            model based on what scientists know about the faults in the area.
          </Term>
          <Term term="Simulated years">
            The number of imagined years of earthquakes the model creates: 10,000 by
            default in CASS. Large earthquakes are so rare that even a hundred years
            of real history would include only a few. A 1-in-1,000-year loss is read
            from the worst imagined years, so it needs many of them: from 1,000 imagined
            years it is just the single worst year, and from 10,000 it is the
            tenth-worst, which is much steadier. Losses are also averaged across all the
            imagined years to give yearly figures. More imagined years take longer to
            calculate and more space to store.
          </Term>
          <Term term="GEM">
            The Global Earthquake Model Foundation, a not-for-profit organisation of
            earthquake scientists. It publishes earthquake hazard models, information
            about the kinds of buildings in each country, and vulnerability functions
            for those buildings. CASS uses GEM&apos;s published data, with GEM&apos;s
            written permission.
          </Term>
          <Term term="OpenQuake">
            The earthquake calculation software made by GEM. CASS uses it to calculate
            the imagined earthquakes and the shaking they cause. You never use it
            directly; CASS runs it for you.
          </Term>
          <Term term="Oasis (Oasis LMF)">
            Free, open-source software used across the insurance industry to calculate
            losses. CASS gives it the shaking, the vulnerability functions and your
            portfolio, and Oasis calculates the losses. As with OpenQuake, CASS runs it
            for you.
          </Term>
        </Terms>
      </Card>

      <Card
        title="Grid terms"
        description={
          <>
            These are explained further, with a picture and worked numbers, in{" "}
            <SectionLink section="grids">Grids, cells and tiles</SectionLink>.
          </>
        }
      >
        <Terms>
          <Term term="Latitude and longitude">
            The two numbers that give any position on Earth. Latitude says how far
            north or south of the equator a place is: positive numbers are north,
            negative numbers are south. Longitude says how far east or west it is of
            Greenwich in London: positive numbers are east, negative numbers are west.
            For example, Jakarta is at about latitude −6.2 (6.2 degrees south) and
            longitude 106.8 (106.8 degrees east).
          </Term>
          <Term term="Degree (°)">
            The unit latitude and longitude are measured in. To give a sense of size: a
            tenth of a degree (0.1°) of latitude is about 11 kilometres, anywhere on
            Earth. A tenth of a degree of longitude is also about 11 kilometres near
            the equator, but gets shorter towards the poles.
          </Term>
          <Term term="Area-peril grid (grid)">
            A pattern of small rectangles laid over a country, like a sheet of graph
            paper over a map. The model works out the shaking once for each rectangle
            rather than separately for every building, which makes the calculation
            fast enough to be practical. Every building inside a rectangle is given
            that rectangle&apos;s shaking. The name comes from the loss engine, which
            calls each rectangle an &ldquo;area peril&rdquo;.
          </Term>
          <Term term="Cell">
            One rectangle of the grid. Its edges are two lines of latitude (its south
            and north sides) and two lines of longitude (its west and east sides).
          </Term>
          <Term term="AreaPerilID">
            The number that identifies a cell: 1, 2, 3 and so on. Other parts of CASS,
            such as the stored shaking, refer to cells by this number. Within one
            version of a grid, a number always means the same rectangle on the map.
          </Term>
          <Term term="Resolution">
            How big the cells are, measured in degrees. A smaller number means smaller
            cells, which gives more detail but means many more cells to calculate.
            Halving the resolution (for example going from 0.1° to 0.05°) makes four
            times as many cells.
          </Term>
          <Term term="Base resolution">
            The cell size used across the whole grid, except in areas where a
            refinement asks for smaller cells.
          </Term>
          <Term term="Tile">
            A rectangle you draw around part of a country to say &ldquo;include this
            area in the model&rdquo;, with a name and a reason, such as &ldquo;Java and
            Madura&rdquo;. You can think of a tile as a window: it decides which part of
            the graph paper is used. A tile does not have to follow the coast, because
            the grid can leave out the sea by itself (see Land clip).
          </Term>
          <Term term="Domain">
            The whole area the grid covers: all of its tiles put together, less any cells
            left out as sea or as empty land. A property whose coordinates fall outside
            the domain cannot be modelled, and CASS lists it so you know.
          </Term>
          <Term term="Refinement">
            An area, usually a city, where you want more detail, so the grid uses
            smaller cells there than elsewhere. For example, the Indonesian grid uses
            cells about 11 km across in most places but about 2.8 km across in
            Jakarta, where a lot of insured value is concentrated and the ground
            changes a lot over short distances. The small cells take the place of the
            larger ones in that area; they are not added on top.
          </Term>
          <Term term="Grid specification">
            The written instructions for a grid: which country, which version, the base
            resolution, the tiles, the refinements, and a reason for each. You write
            the specification; CASS creates the cells from it.
          </Term>
          <Term term="Mapping tolerance">
            A distance in kilometres saved with a grid. It records how close a property
            just outside the tiles would need to be before it would be acceptable to
            treat it as being inside the nearest cell. CASS does not currently move any
            property into a cell it is not in; it always reports such properties
            instead. So in practice this number is a note for the future, and 0 is the
            normal value.
          </Term>
          <Term term="Cell limit">
            The largest number of cells this installation will build in one grid:
            500,000, unless an administrator has changed it. Every cell has its shaking
            calculated and stored for every imagined year, so the limit keeps the hazard
            calculation and its storage practical. The grid itself is quick to build and
            use at this size.
          </Term>
          <Term term="Seed grid">
            A ready-made grid specification CASS ships for a country: Bangladesh,
            Bhutan, Indonesia, Kuwait, the Maldives, Nepal, Oman, the Philippines, Qatar
            and Türkiye. You load it into the grid form, change what you want, and build
            it. Every seed follows the same rule for cell size and says what it leaves
            open.
          </Term>
          <Term term="Land clip">
            The option &ldquo;Keep only cells that touch the country&apos;s land&rdquo;.
            CASS compares each cell with the country&apos;s outline from Natural Earth, a
            free world map, and leaves out cells entirely over the sea or in another
            country. A cell that touches land at all is kept, so coasts are not lost.
          </Term>
          <Term term="Coast buffer">
            How far out to sea the land clip still keeps cells: 5 km unless you change
            it. The world map&apos;s coastlines are accurate to about 5 km, and an address
            can be placed just offshore.
          </Term>
          <Term term="Settlement buffer">
            How far from any building or resident a cell can be and still be kept, when
            the grid skips land with no buildings or people nearby: 5 km unless you
            change it. At 5 km, none of KRE&apos;s geocoded locations were left out. Widen
            it if your book has isolated sites, such as mines or dams, far from towns.
          </Term>
        </Terms>
      </Card>

      <Card
        title="Hazard terms"
        description="The earthquake side of the model: how often earthquakes happen, and how hard each one shakes a place."
      >
        <Terms>
          <Term term="Hazard model">
            An earthquake model for a country, published by scientists. It describes
            where earthquakes can happen, how large they can be, how often they
            happen, and how the shaking spreads out and weakens with distance. It
            arrives as a zip file in OpenQuake&apos;s format, which you upload to CASS.
          </Term>
          <Term term="job.ini">
            A settings file inside the hazard model&apos;s zip file. It tells OpenQuake
            how to run the model: for example, how many years to imagine. CASS reads it
            and shows you its settings on screen, so you do not need to open it.
          </Term>
          <Term term="Logic tree">
            Earthquake scientists do not always agree. For example, they may disagree
            about how quickly shaking weakens with distance. Instead of choosing one
            answer, a hazard model lists the reasonable alternatives and gives each a
            weight showing how much the scientists trust it. This list of alternatives
            and weights is called a logic tree, because it branches like a tree at each
            question.
          </Term>
          <Term term="Path (realisation)">
            One route through the logic tree, choosing one alternative at every branch.
            Each path is one complete, reasonable view of the hazard. CASS picks 20
            paths at random, in proportion to their weights, and combines their
            earthquakes. Using 20 rather than 1 gives an answer much closer to the
            scientists&apos; overall view.
          </Term>
          <Term term="Classical and event-based">
            Two different kinds of calculation OpenQuake can do. A classical
            calculation answers &ldquo;what is the chance of each level of shaking at
            this place?&rdquo;, but produces no individual earthquakes. An event-based
            calculation imagines individual earthquakes one by one. A loss model needs
            individual earthquakes, because one earthquake damages many buildings at
            once and those losses have to be added together. Published models are
            usually set up as classical, so CASS always switches them to event-based.
          </Term>
          <Term term="Investigation time">
            The number of years covered by one batch of imagined earthquakes. The
            default in CASS is 50 years.
          </Term>
          <Term term="Stochastic event set (SES)">
            One batch of imagined years of earthquakes (&ldquo;stochastic&rdquo; means
            generated at random). The total number of imagined years is: the
            investigation time × the number of batches per path × the number of paths.
            With the defaults that is 50 × 1 × 20 = 1,000 years.
          </Term>
          <Term term="Intensity measure (IMT)">
            A way of measuring how strong shaking is. There are several, because
            different buildings respond to different kinds of shaking. PGA (peak ground
            acceleration) measures how violently the ground itself moves. SA
            (spectral acceleration), written with a number such as SA(0.3), measures
            how hard a building that naturally sways back and forth once every 0.3
            seconds would be pushed. Short, stiff buildings sway quickly and respond to
            short numbers; tall buildings sway slowly and respond to longer ones. Each
            vulnerability function says which intensity measure it needs.
          </Term>
          <Term term="g">
            The unit shaking strength is measured in: the acceleration caused by
            Earth&apos;s gravity. Shaking of 0.2 g pushes the ground (or the building)
            with a force of one fifth of gravity. Strong earthquake shaking near a
            fault can exceed 1 g.
          </Term>
          <Term term="Intensity bin">
            A band of shaking strength, for example from 0.20 g to 0.25 g. To keep the
            stored data manageable, the loss engine records which band the shaking
            falls in rather than its exact value.
          </Term>
          <Term term="Clipped hazard">
            Shaking stronger than the top band can hold. When that happens the extra
            strength is cut off, so the model would underestimate damage. Because these
            are the very strongest shakes, and so the ones causing the biggest losses,
            CASS will not publish a model version with clipped hazard as a finished
            model.
          </Term>
          <Term term="Footprint">
            The stored record of how hard each imagined earthquake shakes each cell.
            There is one footprint for each intensity measure. It is usually the
            largest part of a model, so CASS stores it compressed.
          </Term>
          <Term term="Occurrence table">
            A list saying which imagined year each earthquake happens in. It lets CASS
            add up the losses from all the earthquakes in the same year.
          </Term>
          <Term term="Hazard set">
            Everything a finished hazard calculation produces, stored together: the
            footprints, the occurrence table, the intensity bands, and a record of the
            calculation that made them. It is made once for a grid and reused by every
            portfolio on that grid.
          </Term>
          <Term term="Datastore">
            The full results of an OpenQuake calculation: every imagined earthquake and
            the exact strength of its shaking in every cell, before it is sorted into
            intensity bands. CASS keeps it for 90 days after the calculation, and removes
            OpenQuake&apos;s own copy so it is only stored once.
          </Term>
          <Term term="Rebuild a footprint">
            Sorting a hazard set&apos;s stored shaking into CASS&apos;s current intensity
            bands, when the bands have changed since the set was made. It reads the
            datastore, so the earthquakes do not have to be calculated again. The result
            is a new hazard set; the old one&apos;s footprint is deleted once no model
            version uses it.
          </Term>
          <Term term="Region">
            The part of the grid a hazard calculation covers. Calculating the whole
            grid of a large country can take many hours. Choosing a smaller region,
            such as Jakarta and Bandung, finishes in minutes, but only properties inside
            that region will get a loss.
          </Term>
          <Term term="Site conditions and Vs30">
            Site conditions describe the ground beneath a place. Soft ground, such as
            the thick clay under many cities, makes shaking stronger than hard rock
            does. Vs30 is the usual measure: it is how fast a certain kind of vibration
            travels through the top 30 metres of ground. A low Vs30 means soft ground;
            a high one means rock. Where the hazard model has no Vs30 measurement within
            15 km of a cell, CASS treats that cell as rock, which makes the shaking, and
            so the loss, lower than it may really be on soft ground.
          </Term>
        </Terms>
      </Card>

      <Card
        title="Vulnerability and model terms"
        description="The building-damage side of the model, and putting a whole model together."
      >
        <Terms>
          <Term term="Vulnerability function">
            A curve for one type of building that gives the expected damage at each
            strength of shaking. The damage is given as a percentage of the cost to
            rebuild. For example, a function might say that at a certain level of
            shaking, a building of that type suffers on average 10% damage, and at
            twice that shaking, 35%.
          </Term>
          <Term term="Vulnerability set">
            All the vulnerability functions for one country, together with the choices
            used to pick between them. It is built from GEM&apos;s published data plus
            the design-era information you provide. When you set up a run, the same
            thing is called the <strong>assumption set</strong>, because it contains
            the assumptions about buildings whose details are unknown.
          </Term>
          <Term term="GEM release">
            A downloaded copy of GEM&apos;s published data, kept on the computer CASS
            runs on. GEM publishes new releases from time to time. v2026.0.0 is the
            release CASS has been checked against.
          </Term>
          <Term term="Building class (taxonomy)">
            A type of building, described by three things: what it is used for
            (occupancy, such as a home, shop or factory), what it is built from
            (construction, such as reinforced concrete or brick), and how tall it is.
            A taxonomy is the naming system used to describe these types.
          </Term>
          <Term term="Building stock">
            All the buildings in a country. GEM estimates what share of a country&apos;s
            buildings are of each type. CASS uses that mix when a property&apos;s own
            details are missing.
          </Term>
          <Term term="Seismic design level">
            How well a building was designed to withstand earthquakes. Countries
            introduce and strengthen earthquake building codes over time, so buildings
            of different ages can have very different levels of protection. GEM uses four
            levels: CDN (no seismic design), CDL (low), CDM (moderate) and CDH (high).
            GEM publishes a separate vulnerability function for each level.
          </Term>
          <Term term="Design era">
            A period of years together with the seismic design levels a building from
            that period might have, and the reason for thinking so. For example, you
            might say buildings up to 2002 have no or low design, because the
            country&apos;s modern earthquake code was introduced that year. A table of
            eras lets CASS use a property&apos;s year of construction to choose the
            right vulnerability functions.
          </Term>
          <Term term="Enrichment">
            The information you add to GEM&apos;s data when you build a vulnerability
            set: the design eras, the reason for each, the stock weighting, and any
            questions still unanswered. You give it a name and a version. The set is
            named by the version, with <span className="mono">-gem</span> added — an
            enrichment at <span className="mono">0.1.0-draft</span> builds a set{" "}
            <span className="mono">0.1.0-draft-gem</span> — so building again under the
            same version replaces that set rather than adding another. Raise the version
            whenever you change what the enrichment says.
          </Term>
          <Term term="Stock weighting">
            How CASS weights the mix of building types when it needs to blend them. By{" "}
            <strong>replacement cost</strong>, building types count in proportion to
            what they would cost to rebuild, so expensive buildings count for more. By{" "}
            <strong>building count</strong>, each building counts once, however
            valuable. Replacement cost is usually the better choice for insurance
            losses, because losses are about money.
          </Term>
          <Term term="Channel (sub-peril item)">
            Sometimes one building class includes buildings that respond to different
            intensity measures (for example, a mix of short and tall buildings when the
            height is unknown). CASS then splits the class into parts, one for each
            intensity measure, and gives each part its share of the class. Each part is
            called a channel. This happens automatically.
          </Term>
          <Term term="Model version">
            A grid, a vulnerability set and a hazard set for one country, joined
            together and given their own version number. It is the complete model a run
            uses. Giving it a version means a result can always be traced back to
            exactly which model produced it.
          </Term>
          <Term term="Research prototype">
            A model version that has been published even though some work on it is
            unfinished. It can be run, and its results are useful for research, but its
            results cannot be approved. The unfinished items are listed on the model
            version.
          </Term>
          <Term term="Publication blocker">
            One unfinished item that stops a model version being published as a
            finished model. CASS lists every blocker on the model version, so you can
            see exactly what is missing rather than having to guess.
          </Term>
          <Term term="Oasis package">
            A model version converted into the files the Oasis loss engine reads. The
            engine can only use one package at a time, so building a package for one
            model version replaces the previous one.
          </Term>
          <Term term="Gate (approval)">
            A point where CASS requires a second person to check and agree before work
            can continue. One person asks for approval, and a reviewer who is not that
            person approves or rejects it. Nothing is lost while work waits at a gate.
          </Term>
          <Term term="Converter-candidate gate">
            The gate that must be approved before an Oasis package can be built. It is
            the reviewer&apos;s agreement to the way the hazard and vulnerability are
            converted into Oasis&apos;s format.
          </Term>
          <Term term="Draft and published">
            A draft can still be changed. Once published, an item is frozen and can
            never change, so anything calculated from it can be repeated exactly. If a
            published item needs correcting, CASS makes a new version instead.
          </Term>
        </Terms>
      </Card>

      <Card
        title="Portfolio and insurance terms"
        description="Your list of properties, and the insurance and reinsurance that sits on it."
      >
        <Terms>
          <Term term="Project">
            <p>
              A folder for one piece of work. It holds the portfolios, the imports, the
              runs and the results that belong together. Anyone can create a project,
              whatever their role, and the person who creates it owns it; everybody else
              sees it only once they are made a member.
            </p>
            <p>A project does three jobs, and each one matters at a different moment.</p>
            <Bullets>
              <li>
                <strong>It decides who can see the work.</strong> Membership of the
                project, not your role, is what grants access to a portfolio or a result.
              </li>
              <li>
                <strong>It names the files.</strong> The short reference you give it
                labels every file the project stores, which is why it cannot be changed
                afterwards and why it is worth keeping short.
              </li>
              <li>
                <strong>It is the unit of deletion.</strong> Delete a project and its
                portfolios, imports, runs and results go with it.
              </li>
            </Bullets>
            <p>
              What a project does <em>not</em> hold is the model. Grids, vulnerability
              sets, hazard sets and model versions belong to the whole installation, not
              to any project: your run borrows one, your colleague&apos;s project uses the
              same one, and deleting your project leaves it untouched.
            </p>
          </Term>
          <Term term="Portfolio (exposure version)">
            Your list of insured properties, as CASS stores it. Each time it is changed,
            CASS keeps the old version and makes a new numbered one (version 1, version
            2 and so on). A run always uses one exact, published version, so its results
            can be traced to the data that produced them.
          </Term>
          <Term term="Schedule">
            The insurer&apos;s own list of insured properties, usually a spreadsheet.
            It is what you bring into CASS to make a portfolio.
          </Term>
          <Term term="OED">
            Open Exposure Data: a standard layout for insurance property data, used
            across the industry so different software reads the same columns in the
            same way. CASS stores every portfolio as OED files: one for locations, one
            for accounts and policies, and files for reinsurance.
          </Term>
          <Term term="Intake template">
            A spreadsheet provided by CASS for bringing in a schedule that is not
            already in OED. You download it, copy your data into its columns, and upload
            it. Its columns are exactly the ones CASS reads.
          </Term>
          <Term term="Location">
            One insured property: its address and coordinates, its building details,
            and its values.
          </Term>
          <Term term="Account and policy">
            An account is one insured customer, who may have several locations. A
            policy is the insurance contract that customer holds, setting out what is
            covered, the deductible and the limits. Each policy has a Policy ID, which
            CASS uses to link locations to their policy.
          </Term>
          <Term term="Deductible">
            The part of a loss the policyholder pays themselves before the insurance
            pays anything. For example, with a deductible of 20,000 and damage of
            120,000, the insurance pays 100,000.
          </Term>
          <Term term="Limit">
            The most the insurance will pay. For example, with a limit of 500,000, damage
            of 800,000 is paid only up to 500,000.
          </Term>
          <Term term="Layer and attachment">
            Insurance is sometimes arranged in slices, one above another, called layers.
            A layer starts paying once the loss reaches its attachment point, and stops
            at its limit. For example, a layer attaching at 1,000,000 with a limit of
            4,000,000 pays the part of a loss between 1,000,000 and 5,000,000.
          </Term>
          <Term term="Share">
            The proportion of a layer or contract that is taken, written as a decimal:
            0.25 means 25%, and 1 means all of it.
          </Term>
          <Term term="TIV (total insured value)">
            The total value insured, either at one location or across the whole
            portfolio.
          </Term>
          <Term term="Coverage">
            Which kind of value is insured. There are four: the building itself, other
            structures (such as a separate garage), the contents inside, and business
            interruption (income lost while the building cannot be used).
          </Term>
          <Term term="Peril code">
            <p>
              A short code in OED saying which perils a policy covers.{" "}
              <span className="mono">QEQ</span> means earthquake shaking only,{" "}
              <span className="mono">QQ1</span> means all earthquake perils (shaking, and
              also tsunami, fire following, liquefaction and landslide), and{" "}
              <span className="mono">AA1</span> means every peril of every kind.
            </p>
            <p>
              Which one to write depends on the route. In the intake template write{" "}
              <span className="mono">QEQ</span>: promotion records{" "}
              <span className="mono">QEQ</span> whatever you type. In your own OED files
              write what is true of the policy, and CASS reads it as OED defines it. Either
              way CASS models shaking only.
            </p>
          </Term>
          <Term term="Class of business">
            What kind of insurance a row belongs to. CASS includes{" "}
            <span className="mono">Fire</span> (property) rows, and leaves out Liability,
            which insures no building at the coordinates, and Engineering, which needs
            different vulnerability treatment. The match is exact, so{" "}
            <span className="mono">fire</span> in lower case is left out too.
          </Term>
          <Term term="Geocode precision (the column)">
            The intake template&apos;s column for how exact a geocode is. It takes one of six
            words: <span className="mono">parcel</span>, <span className="mono">street</span>{" "}
            or <span className="mono">embedded</span> for a location exact enough to trust
            its cell, and <span className="mono">locality</span>,{" "}
            <span className="mono">postcode</span> or <span className="mono">admin</span> for
            an approximate one. Anything else, blank included, makes the row unclassified.
          </Term>
          <Term term="Geocode">
            The latitude and longitude found for an address. How exact it is depends on
            how much of the address could be matched: a geocode to the exact building
            (rooftop) is precise, while one that could only be matched to a postcode or
            town is really just the middle of that area. A coarse geocode can put a
            property in the wrong cell.
          </Term>
          <Term term="Import">
            A spreadsheet that has been uploaded, read and checked, but not yet turned
            into a portfolio. It becomes a portfolio when you promote it.
          </Term>
          <Term term="Cohort">
            <p>
              The group CASS puts each imported location into, which says what has to happen
              before it can be used. There are four, and the screens name the first three
              rather than lettering them.
            </p>
            <p>
              <strong>A</strong>, shown as <strong>Automated test cohort</strong>: precise
              geocode, nothing to check, ready to use. <strong>B</strong>, shown as{" "}
              <strong>Geocoding-sensitivity cohort</strong>: nothing to check, but the
              geocode is approximate, so the cell it lands in may be wrong.{" "}
              <strong>C</strong>, shown as <strong>Analyst-review backlog</strong>: a person
              needs to look at it.
            </p>
            <p>
              <strong>Unclassified</strong> is the fourth, and the one that catches people
              out. A row lands there when it has no coordinates, when they are 0 and 0, when
              they fall outside the country the row names, when CASS has no check for that
              country (only Indonesia and Nepal today), or when its{" "}
              <strong>Geocode precision</strong> is blank or a word CASS does not recognise.
              Unclassified rows are not offered when promoting and can never be included, so
              a template filled in without Geocode precision produces an import that cannot
              become a portfolio.
            </p>
          </Term>
          <Term term="Promote">
            Turn a checked import into a portfolio. When you promote, you choose which cohort
            to include and what CASS should assume where information is missing. A customer
            is taken whole or not at all: if one of an account&apos;s properties does not
            qualify, none of that account is included, because splitting a schedule would
            leave its policy value with nowhere honest to go.
          </Term>
          <Term term="Allocation">
            How a policy&apos;s total value is shared out between its locations, when the
            schedule gives only one total for several locations. The simplest choice,
            and the starting point, is to share it equally.
          </Term>
          <Term term="Coverage split">
            How a single stated value is divided between building, contents and business
            interruption, when the schedule does not say. It is only used when the
            schedule is silent.
          </Term>
          <Term term="Finding">
            A problem CASS has found in the data, such as a missing value or a value
            that does not make sense. An error must be fixed before the portfolio can be
            published. A warning does not stop publication, but it is kept with the
            portfolio and shown with its results.
          </Term>
          <Term term="Geocoding sensitivity">
            A check on the coarse geocodes. CASS moves each such property around the
            area its geocode could really mean (for example, anywhere in the postcode)
            and sees whether it would still fall in the same cell. It reports how much
            insured value sits in a cell that cannot be relied on.
          </Term>
          <Term term="Financial structure">
            All the insurance and reinsurance terms attached to a portfolio: the
            policies, and the reinsurance contracts.
          </Term>
          <Term term="Reinsurance, treaty and ceded">
            Reinsurance is insurance for insurers: an insurer pays a reinsurer to take on
            part of its losses. A treaty is a reinsurance contract. The part of a loss
            passed to the reinsurer is said to be ceded.
          </Term>
          <Term term="Quota share (QS)">
            A treaty where the reinsurer pays a fixed percentage of every loss it covers.
            With a 30% quota share, a loss of 100,000 costs the reinsurer 30,000 and the
            insurer 70,000.
          </Term>
          <Term term="Surplus share (SS)">
            A treaty that works one property (risk) at a time. The insurer keeps up to a
            set amount on each risk, and the reinsurer takes a share of the larger
            risks, set separately for each risk named in the treaty.
          </Term>
          <Term term="Catastrophe excess of loss (CXL)">
            A treaty that protects against one very large event. It adds up everything
            one earthquake costs across the portfolio and pays the part above a set
            amount (the attachment), up to a maximum (the limit). For example, a treaty
            of 2,000,000 over 100,000 pays the part of each event&apos;s loss between
            100,000 and 2,100,000.
          </Term>
          <Term term="Inuring priority">
            The order in which treaties are applied when there is more than one. The
            treaty with priority 1 is applied first, and the next treaty only sees the
            loss that is left after it. The order can change the result considerably.
          </Term>
          <Term term="Placed share">
            The part of a treaty actually sold to reinsurers. If only 80% of a treaty
            was placed, only 80% of what it would pay is recovered.
          </Term>
        </Terms>
      </Card>

      <Card
        title="Run and result terms"
        description="Calculating the losses, and reading the numbers that come out."
      >
        <Terms>
          <Term term="Run (analysis run)">
            One calculation of one portfolio version through one model version. Each
            run is kept, with a record of exactly what it used.
          </Term>
          <Term term="Run mode">
            What a run is for, which decides how far it goes and what its results may
            be used for. <strong>Geometry only</strong> just checks which cell each
            property falls in, and calculates no loss. <strong>KRE-share technical loss</strong>{" "}
            and <strong>Portfolio-loss research</strong> calculate losses for research.{" "}
            <strong>Decision use</strong> produces a result that a reviewer may approve.
          </Term>
          <Term term="Stage">
            One step of a run, such as reading the portfolio or calculating the losses.
            The Run monitor shows each stage as it happens.
          </Term>
          <Term term="Keys">
            The stage that matches each property to its cell and to the vulnerability
            function for its type of building. The name comes from the loss engine: each
            match is a &ldquo;key&rdquo; that unlocks the right hazard and damage data
            for that property.
          </Term>
          <Term term="Keys reconciliation">
            A check that every part of the portfolio&apos;s value has been accounted for.
            Each part must be either matched, deliberately not modelled, or reported as
            not matched. If some value could not be matched, the run pauses until a
            person agrees it should continue, so value never silently goes missing from
            the total.
          </Term>
          <Term term="fail_ap, fail_v and notatrisk">
            Labels the keys stage gives properties it could not simply match.{" "}
            <span className="mono">fail_ap</span>: the property is outside the grid
            (&ldquo;ap&rdquo; is short for area peril).{" "}
            <span className="mono">fail_v</span>: no vulnerability function fits the
            building (&ldquo;v&rdquo; for vulnerability).{" "}
            <span className="mono">notatrisk</span>: deliberately left out, for example
            a coverage with no value.
          </Term>
          <Term term="Exception">
            A request, with a written reason, for a paused run to continue. A reviewer
            who did not make the request decides whether to allow it.
          </Term>
          <Term term="Smoke check">
            A quick trial run using only a few of the largest earthquakes, done before
            the full calculation. It catches a broken setup in minutes rather than after
            a long run. (The name comes from electronics: switch it on and see if smoke
            comes out.)
          </Term>
          <Term term="Resource profile">
            How much computing power a run is given: the number of processors, the
            memory and the time limit.
          </Term>
          <Term term="Artifact">
            A file a run produced, such as its input files or raw outputs. Artifacts are
            kept with the run so they can be downloaded and checked later.
          </Term>
          <Term term="Result">
            The losses from one run for one perspective, stored together with everything
            they depend on: the model version, the portfolio version and the choices
            made.
          </Term>
          <Term term="Average annual loss (AAL)">
            The loss to expect in an average year. It is the total loss across all the
            imagined years divided by the number of years. For example, if 1,000
            imagined years produce 5,000,000 of loss in total, the AAL is 5,000. Most
            years will have far less than this, and a few will have much more. The AAL is
            the most reliable figure a run produces.
          </Term>
          <Term term="Standard deviation">
            A measure of how much the yearly loss varies from one year to the next. A
            large standard deviation compared with the AAL means losses come mainly in
            occasional very bad years.
          </Term>
          <Term term="Return period">
            A way of describing how rare a loss is. The 100-year loss is the loss that is
            exceeded, on average, once every 100 years, which is a 1% chance in any one
            year. It does not mean the loss happens exactly once a century: it could
            happen twice in ten years, or not at all for three hundred.
          </Term>
          <Term term="Exceedance probability (EP) curve">
            A chart showing, for each size of loss, the chance of a loss at least that
            large in any one year. Rare, large losses are at one end and common, small
            ones at the other. The loss at each return period is read from this curve.
          </Term>
          <Term term="Event loss table">
            A list of each imagined earthquake and the loss it caused. It shows which
            earthquakes drive the result.
          </Term>
          <Term term="Scenario range">
            The same calculation repeated under different assumption sets, to show how
            much the answer depends on those assumptions.
          </Term>
          <Term term="Comparison">
            Two results side by side, showing how the numbers differ and what changed
            between the two runs to explain it.
          </Term>
        </Terms>
      </Card>
    </div>
  );
}
