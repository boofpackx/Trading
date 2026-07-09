# gen7server — Pokémon Ultra Sun/Ultra Moon online services, recreated

A clean-room, server-side recreation of the Gen 7 Pokémon online features
Nintendo shut down on April 8, 2024: **GTS (Global Trade System)**,
**Wonder Trade**, and **Mystery Gift** distribution.

No public Gen 7 server implementation existed when this was written —
Gen 4/5 have several (their era used plain HTTP) and Gen 6 is covered by
[Pretendo's `pokemon-gen6`](https://github.com/PretendoNetwork/pokemon-gen6);
Gen 7 was an open gap.

## What works today

- **PK7 codec** — full implementation of the 232-byte Gen 7 Pokémon format:
  LCRNG xor encryption, block shuffle, checksum, field accessors. Round-trip
  tested across all 32 shuffle values.
- **GTS backend** — deposit / search / trade / withdraw with the real
  system's semantics (record passwords, one-time tickets, request matching,
  race-safe "someone else took it" handling). The API maps **1:1 onto the
  eight NEX DataStore custom methods** (IDs 40–47) the actual games call,
  so a real-console transport can be attached without reshaping the logic.
- **Wonder Trade** — FIFO pool matching; never matches you with yourself.
- **Mystery Gift** — wondercard catalog with delivery windows, "via
  Internet" listing, and single/multi-use serial code redemption.
- **Structural legality checks** on every upload (checksum, species/move/EV
  ranges, no eggs), with a hook point for a full legality engine.
- **JSON/HTTP dev API + demo** so all of it can be exercised end to end.

Stdlib-only Python; SQLite storage; 47 tests.

```bash
cd gen7-server
python3 -m pytest tests/          # run the suite
python3 -m gen7server.demo        # watch two simulated players trade
python3 -m gen7server.api --port 8272 --db gen7.sqlite3   # run the dev server
```

## What this is not (yet)

A real 3DS speaks NEX (PRUDP) — not HTTP — and needs account/friends
servers plus console patches (Luma3DS + Pretendo Nimbus) to reach a
replacement network at all. This repo implements the game-logic layer
behind that transport; the remaining path to a live console connecting —
protocol captures, USUM's game-server credentials, the NEX front-end —
is laid out in [docs/RESEARCH.md](docs/RESEARCH.md).

## Legal

Clean-room implementation from long-published community documentation
(PKHeX, Project Pokémon, Kinnay's NintendoClients wiki, Pretendo). Contains
no Nintendo code, keys, ROMs, or assets, and none were used to build it.
Pokémon is a trademark of Nintendo/Creatures/GAME FREAK; this is an
unaffiliated preservation project.
