# Real journal lines for Haul's tests

`eddi-colonisation-samples.jsonl`: five journal lines the game wrote in April
2025 (three `ColonisationContribution`, two `ColonisationConstructionDepot`),
copied unchanged from the sample lines in EDDI's source,
[`Events/ColonisationContributionEvent.cs`](https://github.com/EDCD/EDDI/blob/a3572ab7a3a541c4b1834738d1d85c6f69dd4202/Events/ColonisationContributionEvent.cs)
and
[`Events/ColonisationConstructionDepotEvent.cs`](https://github.com/EDCD/EDDI/blob/a3572ab7a3a541c4b1834738d1d85c6f69dd4202/Events/ColonisationConstructionDepotEvent.cs)
at commit `a3572ab7`. EDDI is under the Apache License 2.0; see
`THIRD_PARTY_NOTICES.md`.

`tests/test_plugin_events.py` sends them as ED-Finder's EDMC plugin would.
