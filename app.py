import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from pymongo import MongoClient

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

USER_COLORS = [
    "#FF6B6B", "#1BFF9B", "#FFD700", "#A855F7", "#FB923C",
    "#00D4FF", "#FF00FF", "#ADFF2F", "#FF4500", "#9370DB"
]

MONGO_URI = os.environ.get("MONGO_URI")
if MONGO_URI:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, tls=True, tlsAllowInvalidCertificates=True,
                         retryWrites=True)
    db = client['arj_domain']
    messages_collection = db['messages']
else:
    messages_collection = None
    chat_history = []

typing_users = set()
active_users = {}
game_queue = []
match_active = False
players_in_match = []


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('connect')
def handle_connect():
    history = list(messages_collection.find({}, {'_id': 0})) if messages_collection is not None else chat_history
    emit('load_history', history)
    emit('queue_update', {'count': 'full' if match_active else len(game_queue)})


@socketio.on('join_domain')
def handle_join(data):
    active_users[request.sid] = {"user": data['user'], "color": data.get('color', USER_COLORS[0])}
    emit('update_users', list(active_users.values()), broadcast=True)


@socketio.on('toggle_queue')
def handle_toggle_queue():
    global match_active
    if match_active or request.sid not in active_users: return
    if request.sid in game_queue:
        game_queue.remove(request.sid)
    else:
        game_queue.append(request.sid)

    if len(game_queue) >= 2:
        match_active = True
        p1, p2 = game_queue.pop(0), game_queue.pop(0)
        players_in_match.extend([p1, p2])
        emit('start_match', {'p1': active_users[p1]['user'], 'p2': active_users[p2]['user']}, broadcast=True)
    emit('queue_update', {'count': 'full' if match_active else len(game_queue)}, broadcast=True)


@socketio.on('match_won')
def handle_win(data):
    global match_active
    match_active = False
    players_in_match.clear()
    emit('end_game_sequence', data, broadcast=True)


@socketio.on('disconnect')
def handle_disconnect():
    global match_active
    if request.sid in active_users:
        if request.sid in players_in_match:
            match_active = False
            players_in_match.clear()
            emit('match_ended', broadcast=True)
        del active_users[request.sid]
        emit('update_users', list(active_users.values()), broadcast=True)


@socketio.on('send_message')
def handle_send_message(data):
    if messages_collection is not None:
        messages_collection.insert_one(data.copy())
    else:
        chat_history.append(data)
    emit('receive_message', data, broadcast=True)


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))