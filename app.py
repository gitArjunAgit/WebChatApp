import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

chat_history = []
typing_users = set()
# Track active users by mapping session IDs to user details
active_users = {}

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    emit('load_history', chat_history)

@socketio.on('join_domain')
def handle_join(data):
    # Register user session details
    active_users[request.sid] = {"user": data['user'], "color": data['color']}
    emit('update_users', list(active_users.values()), broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    # Remove user if they exit or close the tab
    if request.sid in active_users:
        user_data = active_users[request.sid]
        if user_data['user'] in typing_users:
            typing_users.remove(user_data['user'])
            emit('update_typing', list(typing_users), broadcast=True)
        del active_users[request.sid]
    emit('update_users', list(active_users.values()), broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    chat_history.append(data)
    if data['user'] in typing_users:
        typing_users.remove(data['user'])
        emit('update_typing', list(typing_users), broadcast=True)
    emit('receive_message', data, broadcast=True)

@socketio.on('clear_chat')
def handle_clear_chat():
    global chat_history
    chat_history = []
    emit('chat_cleared', broadcast=True)

@socketio.on('typing')
def handle_typing(data):
    typing_users.add(data['user'])
    emit('update_typing', list(typing_users), broadcast=True)

@socketio.on('stop_typing')
def handle_stop_typing(data):
    if data['user'] in typing_users:
        typing_users.remove(data['user'])
    emit('update_typing', list(typing_users), broadcast=True)

@socketio.on('ping')
def handle_ping():
    pass

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)