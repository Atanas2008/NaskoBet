# Royal Fruits (NaskoBet)

Original 5x3, 10 fixed-line fruit slot engine (Python 3.9+, standard library only).

    config/game.json          game config (paytable, strips, lines, gamble, jackpot); keys starting "_" are comments
    config/schema.json        JSON schema
    royalfruits/              config, rng, reelset, grid, wild_expansion, line_evaluator,
                              scatter_evaluator, engine, gamble, jackpot, wallet, history, simulator, tuner, cli
    tests/                    unit tests

    python -m unittest discover -s tests -t .       # tests
    python -m royalfruits.cli                       # manual play
    python -m royalfruits.simulator --spins 100000000 --workers 8   # Monte Carlo (multi-process)

Notes
- Wild (reels 2-4) expands when it takes part in a win; lines are then re-evaluated once. Scatters are
  evaluated on the grid as landed (wilds never substitute for scatters).
- Star pays when it is on all three of reels 1, 3, 5. Dollar pays for 3/4/5+ anywhere.
- `simulator.exact_rtp` does a full-cycle enumeration (feasible for small strips only); the shipped
  strips (~50^5 combos) use Monte Carlo. Paytable values are placeholders tuned to ~96.0% base RTP.
- Jackpot RTP (sum of contribution %) is reported separately from base RTP.
- `tuner.suggest_adjustments` gives heuristic strip changes toward a target RTP.
