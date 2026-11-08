# -*- coding: utf-8 -*-
import chess, time, random, os
from datetime import datetime

LOG = os.path.join(os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory", "diary", "chess_games.txt")

def play_one():
    b = chess.Board()
    moves = []
    while not b.is_game_over():
        legal = list(b.legal_moves)
        # немного стратегии
        scored = sorted(legal, key=lambda m: b.is_capture(m), reverse=True)
        best = scored[:max(2, len(scored)//4)]
        m = random.choice(best)
        moves.append(b.san(m))
        b.push(m)
    res = b.result()
    line = f"[{datetime.now():%H:%M}] партия: {' '.join(moves[:20])}... -> {res}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
    return res

if __name__ == "__main__":
    try:
        import piton_guard
        piton_guard.ensure_single("piton_chess_ai.py")
    except Exception:
        pass
    while True:
        try:
            r = play_one()
            print("игра сыграна:", r)
        except Exception as e:
            print("err:", e)
        time.sleep(3600 * 3)  # раз в 3 часа
