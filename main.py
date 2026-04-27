from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
import asyncio, uuid, json
from datetime import datetime, timedelta

app = FastAPI(title="Live Auction Server")

class Auction:
    def __init__(self, item: str, start_price: float, duration_sec: int, min_raise: float):
        self.id = str(uuid.uuid4())[:8]
        self.item = item
        self.current_price = start_price
        self.min_raise = min_raise
        self.end_time = datetime.utcnow() + timedelta(seconds=duration_sec)
        self.last_bidder = None
        self.bids = []
        self.connections = set()
        self.active = True
        self.timer_task = None

    async def start_timer(self):
        try:
            while self.active:
                remaining = (self.end_time - datetime.utcnow()).total_seconds()
                if remaining <= 0:
                    self.active = False
                    await self.broadcast({"type": "closed", "winner": self.last_bidder, "price": self.current_price})
                    return
                await self.broadcast({"type": "timer", "remaining": round(remaining, 1)})
                await asyncio.sleep(1)
        except asyncio.CancelledError: pass

    async def place_bid(self, user: str, amount: float):
        if not self.active:
            return {"error": "Auksion yopildi"}
        if amount < self.current_price + self.min_raise:
            return {"error": f"Minimal narx: {self.current_price + self.min_raise}"}
        
        self.current_price = amount
        self.last_bidder = user
        self.bids.append({"user": user, "amount": amount, "time": datetime.utcnow().isoformat()})
        
        # Oxirgi 5 soniyada taklif bo'lsa, vaqtni 10s ga uzaytir
        remaining = (self.end_time - datetime.utcnow()).total_seconds()
        if remaining < 5:
            self.end_time = datetime.utcnow() + timedelta(seconds=10)
            
        await self.broadcast({"type": "bid", "user": user, "price": amount, "end_time": self.end_time.isoformat()})
        return {"status": "ok"}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.add(ws)
        await self.broadcast({"type": "join", "count": len(self.connections)})

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, msg: dict):
        msg_json = json.dumps(msg)
        dead = set()
        for ws in self.connections:
            try: await ws.send_text(msg_json)
            except: dead.add(ws)
        self.connections -= dead

# Auksionlarni saqlash
auctions = {}

@app.post("/auction/create")
async def create_auction(item: str, start_price: float, duration: int, min_raise: float = 10.0):
    a = Auction(item, start_price, duration, min_raise)
    auctions[a.id] = a
    a.timer_task = asyncio.create_task(a.start_timer())
    return {"auction_id": a.id, "item": a.item, "start_price": a.start_price}

@app.websocket("/ws/auction/{auction_id}")
async def auction_ws(websocket: WebSocket, auction_id: str):
    if auction_id not in auctions:
        await websocket.close(code=4004, reason="Auction not found")
        return
    a = auctions[auction_id]
    await a.connect(websocket)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            if data.get("type") == "bid":
                res = await a.place_bid(data["user"], float(data["amount"]))
                if "error" in res:
                    await websocket.send_text(json.dumps(res))
    except WebSocketDisconnect:
        a.disconnect(websocket)

@app.get("/auction/{auction_id}/status")
def auction_status(auction_id: str):
    a = auctions.get(auction_id)
    if not a: raise HTTPException(404, "Topilmadi")
    return {"active": a.active, "current_price": a.current_price, "last_bidder": a.last_bidder, "bids_count": len(a.bids)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
#a
