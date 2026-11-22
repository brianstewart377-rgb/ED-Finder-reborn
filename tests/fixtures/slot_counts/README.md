# In-game ground-slot counts

`in_game_slot_counts.csv`: 7,403 bodies in 446 systems. Each has its ground
slots as read in the game's System Architect view (`ground_slots`) and the
body facts the slot rule uses, as Spansh holds them.

From the owner's "Colonization slot analysis" sheet, tab "Body data", on
22 November 2026. Left out: 610 rows Spansh gave no facts for, and 95 rows that
repeat a body. The sheet's "Error correction expedition" tab lists the 86
counts that disagreed with the prediction: 84 were read again in the game, and
81 of those had been entered wrongly.

`tests/test_slot_rule.py` checks the slot rule against every row.
