import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from pymongo import MongoClient

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Astronomy-themed color palette
USER_COLORS = [
    "#FF6B6B", "#1BFF9B", "#FFD700", "#A855F7", "#FB923C",
    "#00D4FF", "#FF00FF", "#ADFF2F", "#FF4500", "#9370DB"
]

# Connect to MongoDB using an Environment Variable
MONGO_URI = os.environ.get("MONGO_URI")

# Using connection parameters to bypass handshake/timeout issues in cloud
if MONGO_URI:
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        tls=True,
        tlsAllowInvalidCertificates=True,
        retryWrites=True
    )
    db = client['arj_domain']
    messages_collection = db['messages']
else:
    messages_collection = None
    chat_history = []

typing_users = set()
active_users = {}
pong_queue = []


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('connect')
def handle_connect():
    if messages_collection is not None:
        try:
            history = list(messages_collection.find({}, {'_id': 0}))
        except Exception:
            history = []
    else:
        history = chat_history
    emit('load_history', history)


@socketio.on('join_domain')
def handle_join(data):
    user_color = data.get('color', USER_COLORS[0])
    active_users[request.sid] = {"user": data['user'], "color": user_color}
    emit('update_users', list(active_users.values()), broadcast=True)


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in active_users:
        user_data = active_users[request.sid]
        if user_data['user'] in typing_users:
            typing_users.remove(user_data['user'])
            emit('update_typing', list(typing_users), broadcast=True)
        del active_users[request.sid]
        emit('update_users', list(active_users.values()), broadcast=True)

    # Remove user from pong queue if they disconnect
    for p in pong_queue:
        if p['sid'] == request.sid:
            pong_queue.remove(p)
            emit('pong_queue_update', {'count': len(pong_queue)}, broadcast=True)


@socketio.on('send_message')
def handle_send_message(data):
    if messages_collection is not None:
        messages_collection.insert_one(data.copy())
    else:
        chat_history.append(data)

    if data['user'] in typing_users:
        typing_users.remove(data['user'])
        emit('update_typing', list(typing_users), broadcast=True)

    emit('receive_message', data, broadcast=True)


@socketio.on('clear_chat')
def handle_clear_chat():
    if messages_collection is not None:
        messages_collection.delete_many({})
    else:
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


@socketio.on('join_pong')
def handle_join_pong(data):
    sid = request.sid
    # Prevent the same user from taking both slots
    if not any(p['sid'] == sid for p in pong_queue):
        pong_queue.append({'sid': sid, 'user': data['user']})

    emit('pong_queue_update', {'count': len(pong_queue)}, broadcast=True)

    if len(pong_queue) >= 2:
        p1 = pong_queue.pop(0)
        p2 = pong_queue.pop(0)
        # Privately teleport only these two users
        emit('teleport_to_arena', room=p1['sid'])
        emit('teleport_to_arena', room=p2['sid'])
        emit('pong_queue_update', {'count': len(pong_queue)}, broadcast=True)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)