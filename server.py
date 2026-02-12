# server.py
import os
import json
import base64
import asyncio
import logging
import requests
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# ================= CONFIG =================
SAMPLE_RATE = 16000
MIN_CHUNK_SIZE = 3200
POST_TTS_DELAY = 0.6

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("voicebot")

app = FastAPI()

# ================= SCRIPT =================
PITCH = (
    "Hi, my name is Neeraja from Rupeek. "
    "You have a pre approved personal loan at zero percent interest. "
    "The process is fully digital and money is credited in sixty seconds. "
    "Would you like me to guide you step by step, or answer your questions?"
)

STEPS = [
    "First, download the Rupeek app from the Play Store.",
    "Next, complete your Aadhaar KYC inside the app.",
    "Then select your loan amount and confirm disbursal."
]

FAQ_MAP = {
    "interest": "It is zero percent interest if you repay by the due date. Otherwise EMI interest applies as shown in the app.",
    "limit": "Your approved loan limit is visible inside the Rupeek app under the Click Cash banner.",
    "emi": "EMI depends on the tenure you select. The app shows the exact EMI before confirmation.",
    "repayment": "You must repay by the month end due date to enjoy zero percent interest.",
    "documents": "No documents or income proof are required. It is fully digital.",
}

# ================= TTS =================
def tts(text: str) -> bytes:
    r = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "target_language_code": "en-IN",
            "speech_sample_rate": "16000",
        },
        timeout=10,
    )
    return base64.b64decode(r.json()["audios"][0])

async def speak(ws: WebSocket, text: str, session: dict):
    log.info(f"BOT → {text}")
    session["bot_speaking"] = True
    pcm = await asyncio.to_thread(tts, text)

    for i in range(0, len(pcm), MIN_CHUNK_SIZE):
        await ws.send_text(json.dumps({
            "event": "media",
            "media": {"payload": base64.b64encode(pcm[i:i+MIN_CHUNK_SIZE]).decode()}
        }))

    await asyncio.sleep(POST_TTS_DELAY)
    session["bot_speaking"] = False

# ================= INTENT =================
def handle_intent(text: str, session: dict) -> str:
    t = text.lower()

    for k in FAQ_MAP:
        if k in t:
            return FAQ_MAP[k]

    if "guide" in t or "yes" in t:
        step = session["step"]
        if step < len(STEPS):
            session["step"] += 1
            return STEPS[step]
        return "Great! Whenever you’re ready, just open the Rupeek app and check your pre approved loan limit."

    if "no" in t:
        session["end"] = True
        return "No problem. Thank you for your time."

    return "Sorry, I didn’t catch that. Could you please repeat?"

# ================= WS =================
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    log.info("📞 Call connected")

    session = {
        "started": False,
        "bot_speaking": False,
        "step": 0,
        "end": False,
    }

    deepgram_url = (
        "wss://api.deepgram.com/v1/listen"
        "?encoding=linear16"
        "&sample_rate=16000"
        "&language=en-IN"
        "&punctuate=true"
        "&endpointing=300"
    )

    async with websockets.connect(
        deepgram_url,
        extra_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    ) as dg_ws:

        async def dg_receiver():
            async for msg in dg_ws:
                data = json.loads(msg)
                if data.get("is_final"):
                    transcript = data["channel"]["alternatives"][0]["transcript"].strip()
                    if transcript:
                        log.info(f"USER → {transcript}")
                        reply = handle_intent(transcript, session)
                        await speak(ws, reply, session)
                        if session["end"]:
                            await ws.close()

        dg_task = asyncio.create_task(dg_receiver())

        try:
            while True:
                msg = await ws.receive_text()
                data = json.loads(msg)

                if data.get("event") == "start" and not session["started"]:
                    log.info("📡 Exotel start event received")
                    await speak(ws, PITCH, session)
                    session["started"] = True
                    continue

                if data.get("event") == "media" and not session["bot_speaking"]:
                    pcm = base64.b64decode(data["media"]["payload"])
                    await dg_ws.send(pcm)

        except WebSocketDisconnect:
            log.info("🔌 WebSocket disconnected")

        finally:
            dg_task.cancel()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
