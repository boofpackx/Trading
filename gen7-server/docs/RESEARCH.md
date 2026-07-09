# Recreating Pokémon Gen 7 (USUM) online services — research notes

*Compiled July 2026. Everything here comes from public community documentation;
no Nintendo code, ROMs, or leaked material was used.*

## Background

Nintendo shut down Nintendo Network for 3DS/Wii U on **2024-04-08**, killing
online battles, Link Trade over internet, GTS, Wonder Trade, Battle Spot,
Festival Plaza online features, and internet Mystery Gift for the Gen 6
(X/Y/ORAS) and Gen 7 (SM/USUM) games. The Pokémon Global Link (PGL) had
already closed separately in **February 2020**.

The games are fully capable clients with no servers to talk to. Server
recreation is the established preservation answer (Wiimmfi for Wii/DS,
Pretendo for 3DS/Wii U).

## Server architecture the games expect

A Gen 7 game online session traverses these layers:

1. **NASC / account layer** (`nasc.nintendowifi.net`) — console-level
   authentication, hands out the game-server address + NEX token.
   *Status: fully reimplemented by Pretendo (open source).*
2. **Friends server** — a NEX server (game-independent).
   *Status: fully reimplemented by Pretendo.*
3. **Game server (NEX / PRUDP)** — per-game matchmaking + game logic.
   Games are identified by a **game server ID** and authenticate with an
   **access key** embedded in the game binary. Kinnay's wiki documents
   X/Y's (`00055D01` / access key `876138df`); USUM's is extractable the
   same way from a legally dumped copy.
   *Status: protocols documented generically; USUM specifics need captures.*
4. **DataStore custom methods (GTS)** — Gen 6 GTS runs on NEX **DataStore
   (protocol 115)** extended with 8 custom methods, documented on Kinnay's
   wiki and implemented by Pretendo's `pokemon-gen6` server:

   | ID | Method | This repo |
   |----|--------|-----------|
   | 40 | `PrepareUploadPokemon` | `GTS.prepare_upload()` |
   | 41 | `UploadPokemon` | `GTS.upload()` |
   | 42 | `SearchPokemon` | `GTS.search()` |
   | 43 | `PrepareTradePokemon` | `GTS.prepare_trade()` |
   | 44 | `TradePokemon` | `GTS.trade()` |
   | 45 | `DownloadOtherPokemon` | `GTS.download_other()` |
   | 46 | `DownloadMyPokemon` | `GTS.download_my()` |
   | 47 | `DeletePokemon` | `GTS.delete()` |

   Key structures: `GlobalTradeStationRecordKey` (data id + password),
   `GlobalTradeStationSearchPokemonParam` (filters, ordering, pagination),
   `GlobalTradeStationUploadPokemonParam`, `GlobalTradeStationTradePokemonParam`,
   `GlobalTradeStationDownloadPokemonResult`.
   Gen 7 is expected to use the same scheme with additions (Festival Plaza
   services); confirming the deltas is the core remaining reverse-engineering
   task.
5. **BOSS / SpotPass** — background content delivery, used by Mystery Gift
   "via Internet". *Status: Pretendo has a BOSS reimplementation.*

## What was verified during this investigation

- **No public Gen 7 game server exists** (searched July 2026) — Gen 4/5 GTS
  servers are plentiful (that era used plain HTTP), Gen 6 is covered by
  Pretendo's `pokemon-gen6` (Go, PostgreSQL, gRPC to their account/friends
  services), Gen 7 is an open gap.
- Pretendo ran a **network dump campaign** before shutdown (HokakuCTR
  homebrew, dumps traffic to PCAP on-console; NEX traffic decryptable with
  the capturing account's NEX password). USUM captures likely exist in that
  archive — their coverage determines how much binary reverse-engineering
  remains.
- The **PK7 format** (232-byte stored Pokémon: LCRNG xor cipher, 4×56-byte
  block shuffle keyed on bits 13–17 of the encryption constant, 16-bit
  additive checksum) is exhaustively documented by PKHeX and is implemented
  and round-trip-tested in this repo.
- **WC7 wondercards**: every distributed event is archived by the community
  (Project Pokémon event gallery), so Mystery Gift revival is a distribution
  problem, not a data-recovery problem. GTS contents, by contrast, died with
  the official database.

## What this repo implements (and why this slice)

The strategy: build the **transport-independent game logic** now — it is
fully specifiable from public docs and fully testable without hardware —
and leave a clean 1:1 seam where the NEX transport attaches.

- `pk7.py` — PK7 codec (crypto, shuffle, checksum, field access)
- `legality.py` — structural upload validation (the server-side tier)
- `gts.py` — GTS record lifecycle mapped 1:1 onto DataStore methods 40–47
- `wonder_trade.py` — FIFO pool matching with self-match prevention
- `mystery_gift.py` — wondercard catalog, delivery windows, serial codes
- `api.py` — JSON/HTTP façade for development, testing, and tool access

## Roadmap to a real 3DS connecting

1. **Obtain USUM protocol ground truth** — locate community PCAPs
   (Pretendo's dump archive, Project Pokémon) or capture from a real
   console against a local NASC (pre-shutdown dumps are the gold standard).
2. **Extract USUM's game server ID + NEX access key** from a legally
   dumped copy (same procedure as X/Y's documented values).
3. **Diff Gen 7 vs Gen 6**: confirm which DataStore custom methods changed,
   enumerate Festival Plaza's additional services (Wonder Trade transport,
   Global Missions, facility visitors).
4. **Attach a NEX transport** — either port this logic into Pretendo's
   `pokemon-gen6` Go codebase as Gen 7 support (recommended: inherits their
   account/friends/BOSS infra and user base), or build a PRUDP front-end
   that calls this backend.
5. **Hardware loop**: hacked 3DS (Luma3DS + Pretendo's Nimbus patches to
   redirect `nintendowifi.net` domains and accept replacement TLS certs),
   iterate until Festival Plaza completes a live trade.

Steps 1–2 require materials only obtainable from real hardware/dumps;
steps 3–5 are engineering against this codebase.

## Legal posture

- Clean-room server reimplementation from public protocol documentation:
  the established, unchallenged model (Wiimmfi since 2014, Pretendo).
- Modding a console you own: lawful in most jurisdictions.
- Not acceptable and not done here: Nintendo ROMs, keys, SDK code, or
  leaked server software.

## Sources

- Kinnay, *NintendoClients* wiki — NEX/PRUDP protocol documentation,
  game server list, Pokémon X/Y DataStore methods:
  <https://github.com/kinnay/NintendoClients/wiki>
- Pretendo Network — open-source Nintendo Network replacement:
  <https://pretendo.network/>, <https://github.com/PretendoNetwork>
  (`nex-go`, `nex-protocols-go`, `pokemon-gen6`, `PKHaX`)
- Pretendo network dump campaign: <https://pretendo.network/docs/network-dumps>
- PKHeX — PK7/WC7 format reference: <https://github.com/kwsch/PKHeX>
- Project Pokémon — format docs and event wondercard archive:
  <https://projectpokemon.org/>
