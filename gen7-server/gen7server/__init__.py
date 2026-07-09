"""gen7server — clean-room server backend for Pokémon Gen 7 (Sun/Moon/Ultra Sun/Ultra Moon) online features.

Recreates the server-side logic that Nintendo/Game Freak shut down on
2024-04-08: GTS (Global Trade System), Wonder Trade, and Mystery Gift
distribution. Built entirely from publicly documented formats (PKHeX /
Project Pokémon / Kinnay's NintendoClients wiki); contains no Nintendo
code or assets.

The transport a real 3DS speaks is NEX DataStore (protocol 115) — this
package implements the game-logic layer behind it, plus a JSON/HTTP
development API, so the NEX transport can be bolted on once Gen 7's
custom method captures are analyzed. See docs/RESEARCH.md.
"""

__version__ = "0.1.0"
