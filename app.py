from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'arj_cosmic_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Global State ---
users = {}  # sid -> {'user': name, 'color': color}
chat_history = []  # List of message dicts
typing_users = set()
crowns_list = []  # List of usernames who won a match

# Game State
ping_pong_queue = []  # List of sids
active_matches = {}  # sid -> opponent_sid


def broadcast_users():
    active_users = list(users.values())
    emit('update_users', active_users, broadcast=True)


def broadcast_queue():
    count = len(ping_pong_queue)
    status = count if count < 2 else 'full'
    emit('queue_update', {'count': status}, broadcast=True)


@app.route('/')
def index():
    # Make sure your HTML file is named index.html and is inside a "templates" folder
    return render_template('index.html')


@socketio.on('join_domain')
def handle_join(data):
    users[request.sid] = {'user': data['user'], 'color': data['color']}
    emit('load_history', chat_history[-50:])  # Send last 50 messages
    emit('update_crowns', crowns_list)
    broadcast_users()
    broadcast_queue()


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in users:
        username = users[request.sid]['user']
        typing_users.discard(username)
        del users[request.sid]

        # Handle queue cleanup
        if request.sid in ping_pong_queue:
            ping_pong_queue.remove(request.sid)

        # Handle active match disconnect
        if request.sid in active_matches:
            opponent_sid = active_matches.pop(request.sid)
            if opponent_sid in active_matches:
                del active_matches[opponent_sid]
            socketio.emit('match_ended', room=opponent_sid)

        emit('update_typing', list(typing_users), broadcast=True)
        broadcast_users()
        broadcast_queue()


@socketio.on('send_message')
def handle_message(data):
    chat_history.append(data)
    # Keep history from getting too large
    if len(chat_history) > 100:
        chat_history.pop(0)
    emit('receive_message', data, broadcast=True)


@socketio.on('typing')
def handle_typing(data):
    typing_users.add(data['user'])
    emit('update_typing', list(typing_users), broadcast=True)


@socketio.on('stop_typing')
def handle_stop_typing(data):
    typing_users.discard(data['user'])
    emit('update_typing', list(typing_users), broadcast=True)


@socketio.on('clear_chat')
def handle_clear_chat():
    chat_history.clear()
    emit('chat_cleared', broadcast=True)


@socketio.on('ping')
def handle_ping():
    pass  # Keeps connection alive


# --- Game Engine Routing ---

@socketio.on('toggle_queue')
def handle_toggle_queue():
    if request.sid in active_matches:
        return  # Cannot queue while in a match

    if request.sid in ping_pong_queue:
        ping_pong_queue.remove(request.sid)
    else:
        ping_pong_queue.append(request.sid)

    broadcast_queue()

    # Start match if 2 players are queued
    if len(ping_pong_queue) >= 2:
        p1_sid = ping_pong_queue.pop(0)
        p2_sid = ping_pong_queue.pop(0)

        active_matches[p1_sid] = p2_sid
        active_matches[p2_sid] = p1_sid

        match_config = {
            'p1_name': users[p1_sid]['user'],
            'p1_color': users[p1_sid]['color'],
            'p2_name': users[p2_sid]['user'],
            'p2_color': users[p2_sid]['color']
        }

        socketio.emit('start_match', match_config, room=p1_sid)
        socketio.emit('start_match', match_config, room=p2_sid)
        broadcast_queue()


@socketio.on('game_state_sync')
def handle_state_sync(data):
    if request.sid in active_matches:
        opponent_sid = active_matches[request.sid]
        # Relay state directly to the specific opponent
        socketio.emit('receive_game_state', data, room=opponent_sid)


@socketio.on('match_over')
def handle_match_over(data):
    winner = data.get('winner')
    if winner and winner not in crowns_list:
        crowns_list.append(winner)
        emit('update_crowns', crowns_list, broadcast=True)

    if request.sid in active_matches:
        opponent_sid = active_matches.pop(request.sid)
        if opponent_sid in active_matches:
            del active_matches[opponent_sid]

        socketio.emit('match_ended', room=request.sid)
        socketio.emit('match_ended', room=opponent_sid)


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)