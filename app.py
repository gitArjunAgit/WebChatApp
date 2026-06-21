import os
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'arj-domain-super-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Memory list to store messages
chat_history = []

@app.route('/')
def index():
    return render_template('index.html')

# Send history to anyone who joins
@socketio.on('connect')
def handle_connect():
    emit('load_history', chat_history)

@socketio.on('send_message')
def handle_message(data):
    chat_history.append(data)
    # Keep history from lagging (saves the last 150 messages)
    if len(chat_history) > 150:
        chat_history.pop(0)
    emit('receive_message', data, broadcast=True)

# Instantly clear chat for everyone
@socketio.on('clear_chat')
def handle_clear():
    chat_history.clear()
    emit('chat_cleared', broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)