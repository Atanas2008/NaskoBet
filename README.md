# Royal Fruits & Royal Cats (NaskoBet)

Two original 5x3, 10 fixed-line slot games on one engine (Python 3.9+, standard library only),
played in the browser from a lobby with one shared play-money balance.

    config/game.json          Royal Fruits config (paytable, strips, lines, gamble, jackpot); keys starting "_" are comments
    config/cats.json          Royal Cats config (cat symbols, own strips and paytable)
    config/schema.json        JSON schema
    royalfruits/              config, rng, reelset, grid, wild_expansion, line_evaluator,
                              scatter_evaluator, engine, gamble, jackpot, wallet, history, simulator, tuner, cli,
                              casino (two games + shared wallet), web (HTTP server)
    web/                      lobby (index.html) and game page (game.html, app.js, style.css)
    tests/                    unit tests
    assets/symbols/           Royal Fruits SVG art (256x256); manifest.json maps symbol names to files
    assets/symbols/cats/      Royal Cats SVG art + manifest.json

    python -m royalfruits.web                       # website: http://127.0.0.1:8000  (/ lobby, /fruits, /cats)
    python -m unittest discover -s tests -t .       # tests
    python -m royalfruits.cli                       # manual play in the terminal (Royal Fruits)
    python -m royalfruits.simulator --spins 100000000 --workers 8                      # Monte Carlo, Royal Fruits
    python -m royalfruits.simulator --config config/cats.json --spins 100000000 --workers 8   # Royal Cats

Notes
- Wild (reels 2-4; Crown in Royal Fruits, King Cat in Royal Cats) expands when it takes part in a win; lines
  are then re-evaluated once. Scatters are evaluated on the grid as landed (wilds never substitute for scatters).
- Royal Fruits: Star pays when it is on all three of reels 1, 3, 5. Dollar pays for 3/4/5+ anywhere.
- Royal Cats: one scatter, Yarn, pays for 3/4/5+ anywhere like the Dollar (no Star equivalent, no free spins).
- Both games keep gamble (red/black) and the 4-level mystery jackpot; jackpot pools are per game.
  An uncollected win is credited to the shared balance when you spin again, switch game or return to the lobby.
- `simulator.exact_rtp` does a full-cycle enumeration (feasible for small strips only); the shipped
  strips (~50^5 combos) use Monte Carlo. Both games are tuned to ~96.2% base RTP (Royal Cats measured
  96.24%, 95% CI 96.11-96.37 over 48M spins; Royal Fruits 96.15% over 8M spins).
- Jackpot RTP (sum of contribution %) is reported separately from base RTP.
- `tuner.suggest_adjustments` gives heuristic strip changes toward a target RTP.
