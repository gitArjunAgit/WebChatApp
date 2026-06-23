import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from pymongo import MongoClient
import uuid
import json

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
active_games = {}  # game_id -> game_state
private_queue = []
private_rooms = {}  # room_id -> {members: set(sid), messages: list, left: set(sid)}


class PongGame:
    def __init__(self, player1_sid, player1_name, player1_color, player2_sid, player2_name, player2_color):
        self.game_id = str(uuid.uuid4())
        self.player1_sid = player1_sid
        self.player1_name = player1_name
        self.player1_color = player1_color
        self.player2_sid = player2_sid
        self.player2_name = player2_name
        self.player2_color = player2_color
        
        # Game state
        self.width = 1200
        self.height = 600
        self.ball_x = self.width / 2
        self.ball_y = self.height / 2
        self.ball_vx = 8
        self.ball_vy = 8
        self.ball_radius = 10
        
        # Paddles
        self.paddle_width = 12
        self.paddle_height = 100
        self.paddle_speed = 12
        
        # Player 1 (left paddle)
        self.p1_y = (self.height - self.paddle_height) / 2
        self.p1_score = 0
        self.p1_dy = 0
        
        # Player 2 (right paddle)
        self.p2_y = (self.height - self.paddle_height) / 2
        self.p2_dy = 0
        self.p2_score = 0
        
        self.running = True
        self.room = self.game_id
    
    def update(self):
        if not self.running:
            return
        
        # Move paddles
        self.p1_y += self.p1_dy
        self.p2_y += self.p2_dy
        
        # Boundary check for paddles
        if self.p1_y < 0:
            self.p1_y = 0
        if self.p1_y + self.paddle_height > self.height:
            self.p1_y = self.height - self.paddle_height
        if self.p2_y < 0:
            self.p2_y = 0
        if self.p2_y + self.paddle_height > self.height:
            self.p2_y = self.height - self.paddle_height
        
        # Move ball
        self.ball_x += self.ball_vx
        self.ball_y += self.ball_vy
        
        # Ball collision with top/bottom
        if self.ball_y - self.ball_radius < 0 or self.ball_y + self.ball_radius > self.height:
            self.ball_vy = -self.ball_vy
            self.ball_y = max(self.ball_radius, min(self.height - self.ball_radius, self.ball_y))
        
        # Ball collision with paddles
        # Left paddle
        if (self.ball_x - self.ball_radius < self.paddle_width and
            self.p1_y < self.ball_y < self.p1_y + self.paddle_height):
            self.ball_vx = -self.ball_vx
            self.ball_x = self.paddle_width + self.ball_radius
            hit_pos = (self.ball_y - self.p1_y) / self.paddle_height - 0.5
            self.ball_vy += hit_pos * 5
        
        # Right paddle
        if (self.ball_x + self.ball_radius > self.width - self.paddle_width and
            self.p2_y < self.ball_y < self.p2_y + self.paddle_height):
            self.ball_vx = -self.ball_vx
            self.ball_x = self.width - self.paddle_width - self.ball_radius
            hit_pos = (self.ball_y - self.p2_y) / self.paddle_height - 0.5
            self.ball_vy += hit_pos * 5
        
        # Scoring
        if self.ball_x < 0:
            self.p2_score += 1
            self.reset_ball()
        elif self.ball_x > self.width:
            self.p1_score += 1
            self.reset_ball()
        
        # Cap ball speed
        max_speed = 15
        if abs(self.ball_vx) > max_speed:
            self.ball_vx = max_speed if self.ball_vx > 0 else -max_speed
        if abs(self.ball_vy) > max_speed:
            self.ball_vy = max_speed if self.ball_vy > 0 else -max_speed
    
    def reset_ball(self):
        self.ball_x = self.width / 2
        self.ball_y = self.height / 2
        self.ball_vx = 8 if self.p1_score > self.p2_score else -8
        self.ball_vy = 0
    
    def get_state(self):
        return {
            'game_id': self.game_id,
            'ball': {'x': self.ball_x, 'y': self.ball_y, 'radius': self.ball_radius},
            'p1': {
                'name': self.player1_name,
                'color': self.player1_color,
                'y': self.p1_y,
                'score': self.p1_score,
                'width': self.paddle_width,
                'height': self.paddle_height
            },
            'p2': {
                'name': self.player2_name,
                'color': self.player2_color,
                'y': self.p2_y,
                'score': self.p2_score,
                'width': self.paddle_width,
                'height': self.paddle_height
            },
            'width': self.width,
            'height': self.height
        }


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
    emit('pong_queue_update', {'count': len(pong_queue)})
    emit('private_queue_update', {'count': len(private_queue)})


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in active_users:
        user_data = active_users[request.sid]
        if user_data['user'] in typing_users:
            typing_users.remove(user_data['user'])
            emit('update_typing', list(typing_users), broadcast=True)
        del active_users[request.sid]
        emit('update_users', list(active_users.values()), broadcast=True)

    for p in pong_queue[:]:
        if p['sid'] == request.sid:
            pong_queue.remove(p)
            socketio.emit('pong_queue_update', {'count': len(pong_queue)})

    for p in private_queue[:]:
        if p['sid'] == request.sid:
            private_queue.remove(p)
            socketio.emit('private_queue_update', {'count': len(private_queue)})

    for game_id, game in list(active_games.items()):
        if request.sid in [game.player1_sid, game.player2_sid]:
            game.running = False
            socketio.emit('game_ended', {'reason': 'opponent_disconnected'}, room=game_id)
            del active_games[game_id]

    for room_id, room in list(private_rooms.items()):
        if request.sid in room['members']:
            room['members'].discard(request.sid)
            room['left'].add(request.sid)
            leave_room(room_id)
            if not room['members']:
                room['messages'].clear()
                socketio.emit('private_room_cleared', {'room_id': room_id}, room=room_id)
                del private_rooms[room_id]


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
    user = data.get('user')
    color = data.get('color')
    
    if not any(p['sid'] == sid for p in pong_queue):
        pong_queue.append({'sid': sid, 'user': user, 'color': color})

    socketio.emit('pong_queue_update', {'count': len(pong_queue)})

    if len(pong_queue) >= 2:
        p1 = pong_queue.pop(0)
        p2 = pong_queue.pop(0)
        game = PongGame(p1['sid'], p1['user'], p1['color'], p2['sid'], p2['user'], p2['color'])
        active_games[game.game_id] = game
        join_room(game.game_id, sid=p1['sid'])
        join_room(game.game_id, sid=p2['sid'])
        socketio.emit('game_started', {
            'game_id': game.game_id,
            'player_number': 1,
            'opponent': p2['user'],
            'opponent_color': p2['color'],
            'your_color': p1['color']
        }, room=p1['sid'])
        socketio.emit('game_started', {
            'game_id': game.game_id,
            'player_number': 2,
            'opponent': p1['user'],
            'opponent_color': p1['color'],
            'your_color': p2['color']
        }, room=p2['sid'])
        socketio.emit('pong_queue_update', {'count': len(pong_queue)})


@socketio.on('join_private')
def handle_join_private(data):
    sid = request.sid
    user = data.get('user')
    color = data.get('color')

    if not any(p['sid'] == sid for p in private_queue):
        private_queue.append({'sid': sid, 'user': user, 'color': color})
    socketio.emit('private_queue_update', {'count': len(private_queue)})

    if len(private_queue) >= 2:
        p1 = private_queue.pop(0)
        p2 = private_queue.pop(0)
        room_id = f"private-{uuid.uuid4()}"
        private_rooms[room_id] = {'members': {p1['sid'], p2['sid']}, 'messages': [], 'left': set()}
        join_room(room_id, sid=p1['sid'])
        join_room(room_id, sid=p2['sid'])
        socketio.emit('private_started', {
            'room_id': room_id,
            'player_number': 1,
            'opponent': p2['user'],
            'opponent_color': p2['color'],
            'your_color': p1['color']
        }, room=p1['sid'])
        socketio.emit('private_started', {
            'room_id': room_id,
            'player_number': 2,
            'opponent': p1['user'],
            'opponent_color': p1['color'],
            'your_color': p2['color']
        }, room=p2['sid'])
        socketio.emit('private_queue_update', {'count': len(private_queue)})


@socketio.on('leave_private')
def handle_leave_private(data):
    room_id = data.get('room_id')
    if room_id not in private_rooms:
        return
    room = private_rooms[room_id]
    room['members'].discard(request.sid)
    room['left'].add(request.sid)
    leave_room(room_id)
    socketio.emit('private_left', {'room_id': room_id}, room=request.sid)
    if not room['members']:
        room['messages'].clear()
        socketio.emit('private_room_cleared', {'room_id': room_id}, room=room_id)
        del private_rooms[room_id]


@socketio.on('private_message')
def handle_private_message(data):
    room_id = data.get('room_id')
    if room_id in private_rooms:
        private_rooms[room_id]['messages'].append(data.copy())
        emit('private_message', data, room=room_id)


@socketio.on('paddle_move')
def handle_paddle_move(data):
    game_id = data.get('game_id')
    direction = data.get('direction')
    player_number = data.get('player_number')
    if game_id not in active_games:
        return
    game = active_games[game_id]
    if player_number == 1:
        if direction == 'up': game.p1_dy = -game.paddle_speed
        elif direction == 'down': game.p1_dy = game.paddle_speed
        elif direction == 'stop': game.p1_dy = 0
    else:
        if direction == 'up': game.p2_dy = -game.paddle_speed
        elif direction == 'down': game.p2_dy = game.paddle_speed
        elif direction == 'stop': game.p2_dy = 0


@socketio.on('request_game_state')
def handle_request_game_state(data):
    game_id = data.get('game_id')
    if game_id in active_games:
        game = active_games[game_id]
        emit('game_state_update', game.get_state(), room=game_id)


def game_loop():
    while True:
        for game_id, game in list(active_games.items()):
            if game.running:
                game.update()
                socketio.emit('game_state_update', game.get_state(), room=game_id)
                if game.p1_score >= 5 or game.p2_score >= 5:
                    winner = 1 if game.p1_score >= 5 else 2
                    winner_name = game.player1_name if winner == 1 else game.player2_name
                    socketio.emit('game_ended', {
                        'winner': winner,
                        'p1_score': game.p1_score,
                        'p2_score': game.p2_score,
                        'winner_name': winner_name
                    }, room=game_id)
                    socketio.emit('receive_message', {
                        'msg': f'won Cosmic Pong and claimed the crown 👑',
                        'user': winner_name,
                        'color': game.player1_color if winner == 1 else game.player2_color,
                        'time': None,
                        'winner_name': winner_name
                    }, broadcast=True)
                    game.running = False
                    del active_games[game_id]
        socketio.sleep(0.016)


socketio.start_background_task(game_loop)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)
