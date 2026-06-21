from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
# Keep this secret
app.config['SECRET_KEY'] = 'arj-domain-super-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('send_message')
def handle_message(data):
    # This broadcasts incoming text AND user identity data to everyone instantly
    emit('receive_message', data, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, debug=True)
    import os

if __name__ == '__main__':
    # Render provides a PORT environment variable dynamically
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)