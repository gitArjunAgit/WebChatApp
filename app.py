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

# NEW: 3-person private room support
private_queue = []
private_rooms = {}


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
            # Add spin based on where it hits the paddle
            hit_pos = (self.ball_y - self.p1_y) / self.paddle_height - 0.5
            self.ball_vy += hit_pos * 5
        
        # Right paddle
        if (self.ball_x + self.ball_radius > self.width - self.paddle_width and
            self.p2_y < self.ball_y < self.p2_y + self.paddle_height):
            self.ball_vx = -self.ball_vx
            self.ball_x = self.width - self.paddle_width - self.ball_radius
            # Add spin based on where it hits the paddle
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


def notify_mentions(data):
    msg = data.get('msg', '')
    sender = data.get('user')
    mentioned = set()
    for token in msg.split():
        if token.startswith('@') and len(token) > 1:
            candidate = token[1:].strip('.,!?:;()[]{}<>"\'')
            if candidate and candidate != sender:
                mentioned.add(candidate)

    if not mentioned:
        return

    recipients = [sid for sid, info in active_users.items() if info.get('user') in mentioned]
    payload = {
        'from_user': sender,
        'message': msg,
        'mentions': sorted(mentioned)
    }
    for sid in recipients:
        socketio.emit('mention_notification', payload, room=sid)


def get_user_by_sid(sid):
    user_data = active_users.get(sid)
    if user_data:
        return user_data.get('user')
    return None


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
    # Send the current queue status to this user immediately upon joining
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

    # Remove user from pong queue if they disconnect
    for p in pong_queue[:]:
        if p['sid'] == request.sid:
            pong_queue.remove(p)
            socketio.emit('pong_queue_update', {'count': len(pong_queue)})

    # NEW: Remove from private queue if they disconnect
    for p in private_queue[:]:
        if p['sid'] == request.sid:
            private_queue.remove(p)
            socketio.emit('private_queue_update', {'count': len(private_queue)})

    # If user was in a game, end the game
    for game_id, game in list(active_games.items()):
        if request.sid in [game.player1_sid, game.player2_sid]:
            game.running = False
            socketio.emit('game_ended', {'reason': 'opponent_disconnected'}, room=game_id)
            del active_games[game_id]

    # NEW: Remove from private rooms
    for room_id, room in list(private_rooms.items()):
        if request.sid in room['members']:
            room['members'].discard(request.sid)
            socketio.emit('private_left', {
                'room_id': room_id,
                'user': get_user_by_sid(request.sid)
            }, room=room_id)
            if not room['members']:
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

    notify_mentions(data)
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
    
    # Prevent the same user from taking both slots
    if not any(p['sid'] == sid for p in pong_queue):
        pong_queue.append({'sid': sid, 'user': user, 'color': color})

    # Broadcast globally using socketio.emit to ensure everyone sees the update
    socketio.emit('pong_queue_update', {'count': len(pong_queue)})

    if len(pong_queue) >= 2:
        p1 = pong_queue.pop(0)
        p2 = pong_queue.pop(0)
        
        # Create game instance with colors
        game = PongGame(p1['sid'], p1['user'], p1['color'], p2['sid'], p2['user'], p2['color'])
        active_games[game.game_id] = game
        
        # Add both players to the game room
        join_room(game.game_id, sid=p1['sid'])
        join_room(game.game_id, sid=p2['sid'])
        
        # Send game start to both players with colors
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


# NEW: 3-person private room
@socketio.on('join_private')
def handle_join_private(data):
    sid = request.sid
    user = data.get('user')
    color = data.get('color')

    if not any(p['sid'] == sid for p in private_queue):
        private_queue.append({'sid': sid, 'user': user, 'color': color})

    socketio.emit('private_queue_update', {'count': len(private_queue)})

    if len(private_queue) >= 3:
        p1 = private_queue.pop(0)
        p2 = private_queue.pop(0)
        p3 = private_queue.pop(0)

        room_id = f"private-{uuid.uuid4()}"
        private_rooms[room_id] = {
            'members': {p1['sid'], p2['sid'], p3['sid']},
            'users': [p1['user'], p2['user'], p3['user']]
        }

        join_room(room_id, sid=p1['sid'])
        join_room(room_id, sid=p2['sid'])
        join_room(room_id, sid=p3['sid'])

        socketio.emit('private_started', {
            'room_id': room_id,
            'player_number': 1,
            'members': [p1['user'], p2['user'], p3['user']]
        }, room=p1['sid'])

        socketio.emit('private_started', {
            'room_id': room_id,
            'player_number': 2,
            'members': [p1['user'], p2['user'], p3['user']]
        }, room=p2['sid'])

        socketio.emit('private_started', {
            'room_id': room_id,
            'player_number': 3,
            'members': [p1['user'], p2['user'], p3['user']]
        }, room=p3['sid'])

        socketio.emit('private_queue_update', {'count': len(private_queue)})


@socketio.on('private_message')
def handle_private_message(data):
    room_id = data.get('room_id')
    if room_id in private_rooms:
        emit('private_message', data, room=room_id)


@socketio.on('leave_private')
def handle_leave_private(data):
    room_id = data.get('room_id')
    if not room_id or room_id not in private_rooms:
        return

    user = get_user_by_sid(request.sid)


    private_rooms[room_id]['members'].discard(request.sid)
    leave_room(room_id)

    socketio.emit('private_left', {
        'room_id': room_id,
        'user': get_user_by_sid(request.sid)
    }, room=room_id)

    if not private_rooms[room_id]['members']:
        del private_rooms[room_id]


@socketio.on('paddle_move')
def handle_paddle_move(data):
    game_id = data.get('game_id')
    direction = data.get('direction')  # 'up', 'down', 'stop'
    player_number = data.get('player_number')
    
    if game_id not in active_games:
        return
    
    game = active_games[game_id]
    
    if player_number == 1:
        if direction == 'up':
            game.p1_dy = -game.paddle_speed
        elif direction == 'down':
            game.p1_dy = game.paddle_speed
        elif direction == 'stop':
            game.p1_dy = 0
    else:
        if direction == 'up':
            game.p2_dy = -game.paddle_speed
        elif direction == 'down':
            game.p2_dy = game.paddle_speed
        elif direction == 'stop':
            game.p2_dy = 0


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
                
                # Check for win condition (first to 5)
                if game.p1_score >= 5 or game.p2_score >= 5:
                    winner = 1 if game.p1_score >= 5 else 2
                    socketio.emit('game_ended', {
                        'winner': winner,
                        'p1_score': game.p1_score,
                        'p2_score': game.p2_score,
                        'winner_name': game.player1_name if winner == 1 else game.player2_name
                    }, room=game_id)
                    game.running = False
                    del active_games[game_id]
        
        socketio.sleep(0.016)  # ~60 FPS


# Start game loop in background
socketio.start_background_task(game_loop)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)
    
