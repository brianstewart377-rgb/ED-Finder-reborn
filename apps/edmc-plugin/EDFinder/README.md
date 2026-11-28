# ED-Finder plugin for EDMC

Sends your construction sites' shopping lists to your ED-Finder account, and
shows the site you are docked at in EDMC's main window: how far along it is
and what it still needs, as the game itself writes it.

## Installing

1. On ED-Finder's Account page, make a key and copy it.
2. Download the plugin from the same page and unzip it.
3. In EDMC open **File → Settings → Plugins**, press **Open** to open the
   plugins folder, and put the `EDFinder` folder there.
4. Restart EDMC. In **File → Settings**, open the **ED-Finder** tab, paste
   the key, and press **OK**.
5. With the game running, press **Test connection** in that tab. EDMC's main
   window should say "Connected to ED-Finder".
6. Press **Send past journals** once, so builds you started before installing
   the plugin appear too.

## What it sends

Only the journal events ED-Finder's Haul page uses: the colonisation events,
docking, your ship's cargo and loadout, carrier transfers and carrier and
market trades, and your commander's Frontier ID, so ED-Finder can check the
commander is linked to your account. From the game's Commander and LoadGame
events only the time and the Frontier ID are sent, not your credits, ship or
name. Nothing is sent from the game's beta.

MIT licence: see `LICENSE`.
