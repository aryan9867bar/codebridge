from fastapi import FastAPI, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import random
import string
import uuid
import json

app = FastAPI(title="Collaborative Code Editor API")

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== DATA MODELS ====================

class Room:
    def __init__(self, room_code: str, language: str = "python", initial_code: str = None):
        self.id = str(uuid.uuid4())
        self.room_code = room_code
        self.language = language
        self.code = initial_code or self.get_initial_template(language)
        self.version = 0
        self.participants_count = 0
        self.max_participants = 5
        self.created_at = datetime.now()
        self.expires_at = datetime.now() + timedelta(hours=24)
        self.participants: Dict[str, 'Participant'] = {}
        self.websocket_connections: Dict[str, WebSocket] = {}
    
    @staticmethod
    def get_initial_template(language: str) -> str:
        templates = {
            "python": "def solution():\n    # Write your code here\n    pass\n\n# Test your solution\nif __name__ == '__main__':\n    result = solution()\n    print(result)"
        }
        return templates.get(language, "# Start coding here")


class Participant:
    def __init__(self, participant_id: str, room_id: str, client_id: str):
        self.id = participant_id
        self.room_id = room_id
        self.client_id = client_id
        self.connected = True
        self.last_seen_at = datetime.now()
        self.cursor_position = 0


# ==================== REQUEST/RESPONSE MODELS ====================

class CreateRoomRequest(BaseModel):
    language: Optional[str] = "python"
    initial_code: Optional[str] = None


class CreateRoomResponse(BaseModel):
    room_id: str
    room_code: str
    language: str
    code: str
    max_participants: int
    websocket_url: str


class RoomStatusResponse(BaseModel):
    exists: bool
    room_id: Optional[str] = None
    language: Optional[str] = None
    participants: int
    max_participants: int
    is_full: bool


class JoinRoomRequest(BaseModel):
    client_id: Optional[str] = None


class JoinRoomResponse(BaseModel):
    participant_id: str
    role: str
    code: str
    version: int


class LeaveRoomRequest(BaseModel):
    participant_id: str


class RunCodeRequest(BaseModel):
    code: Optional[str] = None
    language: Optional[str] = "python"
    input: Optional[str] = None


class RunCodeResponse(BaseModel):
    stdout: str
    stderr: str
    exitCode: int
    timeMs: int


# ==================== IN-MEMORY STORAGE ====================

# Store rooms by room_code
rooms: Dict[str, Room] = {}


# ==================== HELPER FUNCTIONS ====================

def generate_room_code(length: int = 6) -> str:
    """Generate a random room code"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        if code not in rooms:
            return code


def cleanup_expired_rooms():
    """Remove expired rooms (can be called periodically)"""
    now = datetime.now()
    expired = [code for code, room in rooms.items() if room.expires_at < now]
    for code in expired:
        del rooms[code]
    return len(expired)


async def broadcast_to_room(room_code: str, message: dict, exclude_client: str = None):
    """Broadcast a message to all participants in a room"""
    room = rooms.get(room_code)
    if not room:
        return
    
    disconnected_clients = []
    
    for client_id, ws in room.websocket_connections.items():
        if exclude_client and client_id == exclude_client:
            continue
        
        try:
            await ws.send_json(message)
        except Exception as e:
            print(f"Error sending to {client_id}: {e}")
            disconnected_clients.append(client_id)
    
    # Clean up disconnected clients
    for client_id in disconnected_clients:
        if client_id in room.websocket_connections:
            del room.websocket_connections[client_id]


# ==================== API ENDPOINTS ====================

@app.get("/")
def root():
    return {
        "message": "Collaborative Code Editor API",
        "version": "1.0.0",
        "endpoints": {
            "create_room": "POST /rooms",
            "get_room_status": "GET /rooms/{room_code}/status",
            "join_room": "POST /rooms/{room_code}/join",
            "leave_room": "POST /rooms/{room_code}/leave",
            "run_code": "POST /rooms/{room_code}/run",
            "websocket": "WS /ws/rooms/{room_code}"
        }
    }


@app.post("/rooms", response_model=CreateRoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(request: CreateRoomRequest):
    """Create a new collaborative coding room"""
    room_code = generate_room_code()
    room = Room(
        room_code=room_code,
        language=request.language,
        initial_code=request.initial_code
    )
    
    rooms[room_code] = room
    
    # NOTE: Creator will be added as participant when they connect via WebSocket
    # We don't add them here because we need their clientId from the frontend
    
    # In production, use your actual domain
    websocket_url = f"ws://localhost:8000/ws/rooms/{room_code}"
    
    return CreateRoomResponse(
        room_id=room.id,
        room_code=room.room_code,
        language=room.language,
        code=room.code,
        max_participants=room.max_participants,
        websocket_url=websocket_url
    )


@app.get("/rooms/{room_code}/status", response_model=RoomStatusResponse)
def get_room_status(room_code: str):
    """Get the current status of a room"""
    room = rooms.get(room_code)
    
    if not room:
        return RoomStatusResponse(
            exists=False,
            participants=0,
            max_participants=2,
            is_full=False
        )
    
    return RoomStatusResponse(
        exists=True,
        room_id=room.id,
        language=room.language,
        participants=room.participants_count,
        max_participants=room.max_participants,
        is_full=room.participants_count >= room.max_participants
    )


@app.post("/rooms/{room_code}/join", response_model=JoinRoomResponse)
def join_room(room_code: str, request: JoinRoomRequest):
    """Join an existing room"""
    room = rooms.get(room_code)
    
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found"
        )
    
    if room.participants_count >= room.max_participants:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "ROOM_FULL", "message": "Room is full. Maximum 5 participants allowed."}
        )
    
    # Generate participant ID
    participant_id = request.client_id or str(uuid.uuid4())
    client_id = request.client_id or participant_id
    
    participant = Participant(
        participant_id=participant_id, 
        room_id=room.id,
        client_id=client_id
    )
    
    room.participants[participant_id] = participant
    room.participants_count += 1
    
    # First participant is the owner
    role = "owner" if room.participants_count == 1 else "participant"
    
    return JoinRoomResponse(
        participant_id=participant_id,
        role=role,
        code=room.code,
        version=room.version
    )


@app.post("/rooms/{room_code}/leave")
def leave_room(room_code: str, request: LeaveRoomRequest):
    """Leave a room"""
    room = rooms.get(room_code)
    
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found"
        )
    
    if request.participant_id in room.participants:
        # Remove from websocket connections
        if request.participant_id in room.websocket_connections:
            del room.websocket_connections[request.participant_id]
        
        del room.participants[request.participant_id]
        room.participants_count = max(0, room.participants_count - 1)  # Fix: decrement count
    
    return {"message": "Left room successfully", "participants_remaining": room.participants_count}


@app.post("/rooms/{room_code}/run", response_model=RunCodeResponse)
async def run_code(room_code: str, request: RunCodeRequest):
    """Run code in a sandboxed environment"""
    room = rooms.get(room_code)
    
    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found"
        )
    
    # Get code from request or use room's current code
    code = request.code if request.code is not None else room.code
    
    # For now, we'll simulate code execution
    # In Phase 4, we'll implement actual sandboxed execution
    import time
    import sys
    from io import StringIO
    
    stdout_capture = StringIO()
    stderr_capture = StringIO()
    exit_code = 0
    start_time = time.time()
    
    try:
        # Redirect stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        
        # Execute the code
        exec(code, {"__name__": "__main__"})
        
    except Exception as e:
        stderr_capture.write(f"{type(e).__name__}: {str(e)}")
        exit_code = 1
    finally:
        # Restore stdout and stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    
    end_time = time.time()
    execution_time_ms = int((end_time - start_time) * 1000)
    
    return RunCodeResponse(
        stdout=stdout_capture.getvalue(),
        stderr=stderr_capture.getvalue(),
        exitCode=exit_code,
        timeMs=execution_time_ms
    )


@app.delete("/rooms/{room_code}")
def delete_room(room_code: str):
    """Delete a room (admin/cleanup operation)"""
    if room_code not in rooms:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found"
        )
    
    del rooms[room_code]
    return {"message": f"Room {room_code} deleted successfully"}


@app.get("/rooms")
def list_rooms():
    """List all active rooms (for debugging/admin)"""
    cleanup_expired_rooms()
    return {
        "total_rooms": len(rooms),
        "rooms": [
            {
                "room_code": code,
                "room_id": room.id,
                "language": room.language,
                "participants": room.participants_count,
                "connected_ws": len(room.websocket_connections),
                "created_at": room.created_at.isoformat(),
                "expires_at": room.expires_at.isoformat()
            }
            for code, room in rooms.items()
        ]
    }


# ==================== WEBSOCKET ENDPOINT ====================

@app.websocket("/ws/rooms/{room_code}")
async def websocket_endpoint(websocket: WebSocket, room_code: str):
    """WebSocket endpoint for real-time collaboration"""
    await websocket.accept()
    
    room = rooms.get(room_code)
    if not room:
        await websocket.send_json({
            "type": "ERROR",
            "message": "Room not found"
        })
        await websocket.close()
        return
    
    # Migration: Add websocket_connections if old room object
    if not hasattr(room, 'websocket_connections'):
        room.websocket_connections = {}
    
    client_id = None
    participant_id = None
    
    try:
        # Wait for INIT message
        init_message = await websocket.receive_json()
        
        if init_message.get("type") != "INIT":
            await websocket.send_json({
                "type": "ERROR",
                "message": "Expected INIT message"
            })
            await websocket.close()
            return
        
        client_id = init_message.get("clientId")
        participant_id = init_message.get("participantId") or client_id
        
        if not client_id:
            await websocket.send_json({
                "type": "ERROR",
                "message": "clientId required"
            })
            await websocket.close()
            return
        
        # Check if participant exists, if not create one (for room creator)
        if participant_id not in room.participants:
            # Auto-add participant if room is not full
            if room.participants_count >= room.max_participants:
                await websocket.send_json({
                    "type": "ERROR",
                    "message": "Room is full"
                })
                await websocket.close()
                return
            
            # Create new participant
            participant = Participant(
                participant_id=participant_id,
                room_id=room.id,
                client_id=client_id
            )
            room.participants[participant_id] = participant
            room.participants_count += 1
        
        # Register WebSocket connection
        room.websocket_connections[client_id] = websocket
        
        # Send current state to the newly connected client
        await websocket.send_json({
            "type": "STATE",
            "code": room.code,
            "version": room.version,
            "participants": room.participants_count
        })
        
        # Notify others that a participant joined
        await broadcast_to_room(room_code, {
            "type": "PARTICIPANT_JOINED",
            "clientId": client_id,
            "participantCount": room.participants_count
        }, exclude_client=client_id)
        
        # Listen for messages
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            
            if message_type == "EDIT":
                # Update room code
                new_code = message.get("code")
                if new_code is not None:
                    room.code = new_code
                    room.version += 1
                    
                    # Broadcast to other participants
                    await broadcast_to_room(room_code, {
                        "type": "PATCH",
                        "code": room.code,
                        "version": room.version,
                        "clientId": client_id
                    }, exclude_client=client_id)
            
            elif message_type == "CURSOR":
                # Broadcast cursor position to all other participants
                await broadcast_to_room(room_code, {
                    "type": "CURSOR",
                    "clientId": client_id,
                    "position": message.get("position"),
                    "selection": message.get("selection")
                }, exclude_client=client_id)
            
            elif message_type == "PING":
                await websocket.send_json({"type": "PONG"})
    
    except WebSocketDisconnect:
        print(f"Client {client_id} disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Clean up on disconnect
        if client_id and room_code in rooms:
            room = rooms[room_code]

            room.participants_count -= 1
            rooms[room_code] = room
        
            # Auto-cleanup if no participants left
            if room.participants_count == 0:
                del rooms[room_code]
            
            # Remove WebSocket connection
            if client_id in room.websocket_connections:
                del room.websocket_connections[client_id]
            
            # Notify others
            await broadcast_to_room(room_code, {
                "type": "PARTICIPANT_LEFT",
                "clientId": client_id,
                "participantCount": room.participants_count
            })
            


# ==================== RUN SERVER ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
