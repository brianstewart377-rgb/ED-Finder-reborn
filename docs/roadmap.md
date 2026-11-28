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

## Also now: Haul

A shopping list of what your builds are still short of, and where to buy
each thing, as the shopping list in Raven Colonial does. The owner moved Haul
up on 25 November ("Yes move Haul up"), and on 28 November agreed its design
(the document "Haul — design") and decided that its first steps start now,
while the ratings fill runs. Find's new page (step 6 above) still comes once
the fill has finished and been measured.

What was decided on 28 November:

- **The list is the game's own.** Each time a player docks at a construction
  site, the game writes the site's whole list to the journal: for each good,
  how much is needed and how much has been delivered by everyone. Haul keeps
  the newest list and never adds up deliveries, so a load hauled with nothing
  running does not leave it wrong.
- **Fed by our own plugin for EDMC**, downloaded from the Account page (not
  listed in EDMC's plugin browser), with a key per member. It sends the
  journal lines Haul uses, the game's market and cargo files, and EDMC's own
  copy of the member's carrier cargo from Frontier; a "send past journals"
  button catches up.
- **Where to buy** comes from the station markets in the Spansh file the Pi
  already takes each night (the importer leaves them out today): stations
  within 50 ly of the build, up to 150 ly, each market's age shown. Fleet
  carriers are left out as sellers at first.
- **The member's own carrier**: transfers change its totals at once, and the
  game's own figures reset them whenever they arrive.
- **Eight new tables**, agreed: the commodity list, site readings, plugin
  keys, two market tables and their fill record, and the carrier pair.
- Builds are private, with a switch to share one with all members (a later
  step). Estimates for builds not started yet are a later step.

| Step | What | State |
|---|---|---|
| 1 | The tables (migration `034`) | Built 28 November; applied on the Pi after the next full backup |
| 2 | Plugin keys and the address that takes the plugin's data | **Next** |
| 3 | The plugin | To do |
| 4 | The Haul page: my builds, what each still needs, carrier and hold | To do |
| 5 | Markets kept each night | To do |
| 6 | Where to buy and fewest stops | To do |
| 7 | The carrier figures in full | To do |
| 8 | Sharing a build | To do |

The two read-only checks that had to come first were done on 28 November:
Spansh's stations file carries each station's market, and a construction
site's needs are only in the docked player's journal (no public feed carries
them). A survey of 129 other colonisation projects is in the document
"Colonisation projects on GitHub: what they are and what we can use".

## Next, in this order

1. **The map's other two jobs.** All of a player's colonies on the map,
   planned ones too; and the nearby systems that sell what a build needs. The
   second uses what Haul builds.
2. **BGS.**

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
