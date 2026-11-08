# -*- coding: utf-8 -*-
import chess, chess.svg, base64

games = {}  # user_id -> board

def start_game(user_id):
    games[user_id] = chess.Board()
    return board_text(games[user_id])

def move(user_id, san):
    if user_id not in games:
        return "Сначала /chess"
    b = games[user_id]
    try:
        b.push_san(san)
    except Exception as e:
        return f"Не понимаю ход: {e}"
    if b.is_game_over():
        res = b.result()
        del games[user_id]
        return f"Партия окончена: {res}\n{board_text(b)}"
    # Питон думает над ответом (простой: случайный ход из лучших 3)
    import random
    moves = list(b.legal_moves)
    if not moves:
        del games[user_id]
        return f"Партия окончена\n{board_text(b)}"
    # лёгкая стратегия: берём ход с наибольшим взятием
    scored = sorted(moves, key=lambda m: b.is_capture(m), reverse=True)
    best = scored[:max(3, len(scored)//3)]
    my_move = random.choice(best)
    b.push(my_move)
    text = board_text(b)
    if b.is_game_over():
        text += f"\n\nПартия окончена: {b.result()}"
        del games[user_id]
    return text

def board_text(b):
    rows = []
    rows.append("  a b c d e f g h")
    for r in range(8):
        row = f"{8-r} "
        for c in range(8):
            sq = chess.square(c, 7-r)
            p = b.piece_at(sq)
            row += (p.symbol() if p else ".") + " "
        rows.append(row)
    return "\n".join(rows)
