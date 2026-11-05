"""Simple CLI for manual play: python -m royalfruits.cli"""
from decimal import Decimal

from .config import Config
from .engine import GameEngine
from .gamble import Gamble, GambleError
from .history import GameHistory
from .jackpot import Jackpot
from .rng import SecureRNG
from .wallet import InsufficientFunds, Wallet

ABBR = {"CHERRY": "Ch", "LEMON": "Le", "ORANGE": "Or", "PLUM": "Pl", "GRAPES": "Gr",
        "WATERMELON": "Wm", "SEVEN": "77", "WILD": "WD", "STAR": "**", "DOLLAR": "$$"}


def show(grid):
    return "\n".join("  " + " ".join(f"{ABBR[s]:>2}" for s in row) for row in grid)


def main():
    cfg = Config.load()
    rng = SecureRNG()
    engine = GameEngine(cfg, rng)
    jp = Jackpot(cfg.jackpot, rng) if cfg.jackpot else None
    wallet = Wallet(1000)
    hist = GameHistory("history.jsonl")
    bet = Decimal("1")
    print("Royal Fruits. Commands: Enter=spin, b=change bet/line, q=quit")
    while True:
        cmd = input(f"[balance {wallet.balance} | bet/line {bet} | total {bet * 10}] > ").strip().lower()
        if cmd == "q":
            break
        if cmd == "b":
            try:
                bet = engine.check_bet(input(f"bet per line {[str(b) for b in cfg.bet_levels]}: "))
            except Exception as e:
                print(e)
            continue
        try:
            wallet.debit(bet * 10)
        except InsufficientFunds as e:
            print(e)
            continue
        r = engine.spin(bet)
        extra = {}
        if jp:
            j = jp.play(r.total_bet)
            if j:
                wallet.credit(j[1], "jackpot")
                extra["jackpot"] = {"level": j[0], "prize": str(j[1])}
                print(f"*** MYSTERY JACKPOT {j[0]}: {j[1]} ***")
        hist.record(r, **extra)
        print(show(r.grid_before))
        if r.expanded:
            print("Wilds expanded on reels", [x + 1 for x in r.expanded_reels])
            print(show(r.grid_after))
        for w in r.line_wins:
            print(f"  line {w.line_index + 1}: {w.count}x {w.symbol} = {w.multiplier * bet}")
        for w in r.scatter_wins:
            print(f"  scatter {w.symbol} x{w.count} = {w.multiplier * r.total_bet}")
        print(f"WIN {r.total_win}" + (" (MAX WIN CAP)" if r.capped else ""))
        if r.total_win > 0:
            win = r.total_win
            try:
                g = Gamble(rng, win, cfg.gamble.get("max_amount", 100), cfg.gamble.get("max_steps", 5))
                while g.can_gamble():
                    c = input(f"Gamble {g.amount}? red/black or Enter to collect: ").strip().lower()
                    if not c:
                        break
                    try:
                        print("Won!" if g.pick(c) else "Lost.")
                    except GambleError as e:
                        print(e)
                win = g.collect()
            except GambleError:
                pass
            wallet.credit(win)


if __name__ == "__main__":
    main()
