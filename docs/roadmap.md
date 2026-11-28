# Roadmap

As it stands on 28 November 2026. The plan this follows is
[`plan-2026-11-08.pdf`](plan-2026-11-08.pdf). Where the order has changed
since, the owner's decision and its date are given here.

The journey is **Find → Look up → Plan → Haul**, with BGS alongside and the
galaxy map underneath.

## Done

- **The move to the Raspberry Pi.** The Pi has served the site since
  14 November. The old server ended on 15 November. The record of the move is
  [`pi-move.md`](pi-move.md).
- **The Pi looks after itself each night.** The galaxy is brought up to date
  twice a day and changed systems are given new scores; which systems are
  within 500 ly of an inhabited one is worked out afresh each week; the
  system state (who lives where, what is being claimed) is refreshed each
  night; what users have written is backed up each night and the whole
  database each week, onto the 12 TB drive; an email follows when a job
  fails, says nothing, or its timer has stopped.
- **The EDDN listener** has kept station sightings since 14 November. Nothing
  on the site reads them yet.
- **Find** hides systems that are inhabited or being claimed, and shows
  systems within 250 ly of inhabited space by default, 500 ly on request.
- **The galaxy map** (Almanac's, on Explore) and the **System Map** (drawn
  with Cosmos pictures). "Show on map" from a Find result is in: the first of
  the map's three jobs.
- **Plan**: ground slots are predicted by Raven Colonial's rule, which was
  right for 99.93% of the 4,599 bodies on the owner's sheet of in-game counts.

## Now: the ratings rebuild

Find stops giving made-up scores. You pick what you want to build, one
economy or two together, and each result names the body to build on, with
numbers that come from the game's rules. The design, the decisions and the
reviews are in the document "Ratings rebuild — design".

Eight steps. Five are built; nothing on screen has changed yet, because the
new search gets its page in step 6.

| Step | What | State |
|---|---|---|
| 1 | Speed trial, and the ground-slot rule put right | Done 22 November |
| 2 | Body facts made without the old scorer | Done 22 November |
| 3 | One set of rules for Find and Plan | Done 23 November |
| 4 | Find's facts for each system, kept every night | Built. The fill of all 41 million systems in reach starts again on 28 November and should finish about 3 December |
| 5 | The search on those facts: near a system, and across the whole band | Built as the API. It answers once the fill has finished |
| 6 | Find's page: the economy and pair picker, the new card and its "Why here?" | **Next** |
| 7 | The system page and Compare use the new facts | To do |
| 8 | Retire the old scores, tier letters and colony types | To do |

Before step 6 is built:

- measure both searches on the Pi once the fill has finished, read how long
  the nightly lists take to write, and settle whether 250 ly is the right
  limit for a search near a system;
- recheck the orbital ranges against ED-Finder's own body counts for the
  systems on the owner's sheet (decision 10 in the design).

Step 6 also carries: a search near a system with no economy chosen (nearest
first), the browser journeys for the new search, and the gentle warning on
cards for bodies near white dwarfs and neutron stars (decision 9).

## Next, in this order

The owner's decision of 25 November ("Yes move Haul up"): Haul comes before
the rest of the map.

1. **Haul.** A shopping list of what your builds are still short of, and
   where to buy each thing, as the shopping list in Raven Colonial does. The
   two read-only checks that had to come first are done (28 November):
   - **Markets.** The Spansh stations file the Pi already takes each night
     carries each station's market: the goods, their prices, how much is in
     stock and when the market was last seen. The importer leaves it out on
     purpose today. Keeping it needs a new table, so its design comes to the
     owner first. Many markets are months old, so each one's age is shown.
   - **A construction site's needs.** The game writes them only to the
     journal of a player docked at the site, in one event,
     `ColonisationConstructionDepot` (for each good: how much is needed, how
     much has been delivered, and the payment). No public feed carries it,
     so it has to come from members' own PCs.

   How members' journals reach the site is settled in the design: the
   journal import we already have, the page reading the journal folder in
   Chrome or Edge, BGS-Tally (a plugin many players already run), or a
   plugin of our own for EDMC (the owner leaned that way on 25 November). A
   survey of 129 other colonisation projects, in the document "Colonisation
   projects on GitHub: what they are and what we can use", suggests the
   plugin can come last. The design is being written now, while the ratings
   fill runs. Nothing for Haul is built until the owner has agreed the
   design and the ratings rebuild is done.
2. **The map's other two jobs.** All of a player's colonies on the map,
   planned ones too; and the nearby systems that sell what a build needs. The
   second uses what Haul builds.
3. **BGS.**

Also agreed, smaller:

- **A maths solver on Plan** (HiGHS), after the ratings rebuild.

## Housekeeping on the Pi

None of these stops the site working. In rough order of worth:

- **See that the new power supply holds through a night's work** (fitted
  27 November; `vcgencmd get_throttled` should say `throttled=0x0` the
  morning after).
- **The containers' memory limits are not kept.** Putting that right means
  changing the file the Pi starts from, and a restart of its own.
- **The 12 TB backup drive:** a health check of the drive; a cover for the
  caddy's power button, which was pressed by accident on 14 November; then
  the spare copy of the first full backup can come off the Pi's own drive.
- **Backups do not leave the house yet.** Not built: an encrypted copy
  off-site, and a restore test that runs by itself. A restore of the whole
  database at full size has not been tried on the Pi.

## Not planned, or parked

- **Heatmaps on the map:** parked.
- **A program of our own like SRV Survey** (a separate Windows program that
  draws over the game): not planned. The shopping list goes on our own site.
- **Thousands of markers on the map at once, a heat layer on the 3D map, a
  version of Almanac of our own:** left out unless the owner asks. The map's
  three jobs do not need them.

## One rule learnt the hard way

No heavy one-off job on the Pi where the nightly work, in small pieces, can do
the same thing. On 25 November a single statement over 7.4 million rows was
running when the Pi froze and restarted itself. The Pi was on a power supply
too weak for it at the time, which is the most likely cause, but the rule
stands on its own: small steps can stop and carry on, and one big statement
cannot. What happened is in [`pi-move.md`](pi-move.md), under migration `033`.
