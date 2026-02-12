import os
import json
import asyncio
import logging
import base64
import io
import struct
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import requests

from faster_whisper import WhisperModel

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# ================= CONFIG =================
SAMPLE_RATE = 16000
CHUNK_SIZE = 3200
SPEECH_THRESHOLD = 520
MIN_SPEECH_CHUNKS = 6
MAX_SILENCE_CHUNKS = 10
POST_TTS_DELAY = 0.6
MAX_REPEAT_PROMPTS = 2

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("ai-caller")

# ================= APP =================
app = FastAPI()

# ================= LOAD WHISPER =================
log.info("🔊 Loading Faster-Whisper model (tiny, CPU, int8)...")
whisper_model = WhisperModel(
    "tiny",
    device="cpu",
    compute_type="int8"
)
log.info("✅ Faster-Whisper loaded successfully")

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
    "interest": "It is zero percent interest if you repay by the due date. Otherwise EMI interest applies.",
    "limit": "Your approved loan limit is visible inside the Rupeek app under the Click Cash banner.",
    "emi": "EMI depends on the tenure you choose. The app shows it clearly before confirmation.",
    "repayment": "You must repay by the month end due date to enjoy zero percent interest.",
    "processing": "Processing fees are shown clearly in the app. There are no hidden charges.",
    "documents": "No income documents are required. The process is fully digital."
}

# ================= HELPERS =================
def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(pcm)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(pcm)))
    buf.write(pcm)
    return buf.getvalue()

def is_speech(pcm: bytes) -> bool:
    energy = sum(
        abs(int.from_bytes(pcm[i:i + 2], "little", signed=True))
        for i in range(0, len(pcm) - 1, 2)
    )
    return (energy / max(len(pcm) // 2, 1)) > SPEECH_THRESHOLD

def stt_safe(pcm: bytes) -> str:
    try:
        wav = pcm_to_wav(pcm)
        segments, _ = whisper_model.transcribe(
            wav,
            language="en",
            vad_filter=True,
            beam_size=1
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text
    except Exception as e:
        log.error(f"STT error: {e}")
        return ""

def detect_faq(text: str):
    t = text.lower()
    for k in FAQ_MAP:
        if k in t:
            return FAQ_MAP[k]
    return None

def tts(text: str) -> bytes:
    r = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "text": text,
            "target_language_code": "en-IN",
            "speech_sample_rate": "16000"
        },
        timeout=10
    )
    return base64.b64decode(r.json()["audios"][0])

async def speak(ws: WebSocket, text: str, session: dict):
    log.info(f"BOT → {text}")
    session["bot_speaking"] = True
    pcm = await asyncio.to_thread(tts, text)

    for i in range(0, len(pcm), CHUNK_SIZE):
        await ws.send_text(json.dumps({
            "event": "media",
            "media": {"payload": base64.b64encode(pcm[i:i + CHUNK_SIZE]).decode()}
        }))

    await asyncio.sleep(POST_TTS_DELAY)
    session["bot_speaking"] = False

# ================= WEBSOCKET =================
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    log.info("📞 Call connected")

    session = {
        "started": False,
        "bot_speaking": False,
        "step": 0,
        "repeat_count": 0
    }

    buf = b""
    speech = b""
    speech_chunks = 0
    silence_chunks = 0

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)

            if data.get("event") == "start" and not session["started"]:
                log.info("📡 Exotel start event received")
                await speak(ws, PITCH, session)
                session["started"] = True
                continue

            if data.get("event") != "media" or session["bot_speaking"]:
                continue

            buf += base64.b64decode(data["media"]["payload"])

            while len(buf) >= CHUNK_SIZE:
                frame, buf = buf[:CHUNK_SIZE], buf[CHUNK_SIZE:]

                if is_speech(frame):
                    speech += frame
                    speech_chunks += 1
                    silence_chunks = 0
                else:
                    silence_chunks += 1

                if speech_chunks < MIN_SPEECH_CHUNKS:
                    continue

                if silence_chunks < MAX_SILENCE_CHUNKS:
                    continue

                text = await asyncio.to_thread(stt_safe, speech)
                speech = b""
                speech_chunks = 0
                silence_chunks = 0

                if not text:
                    session["repeat_count"] += 1
                    if session["repeat_count"] <= MAX_REPEAT_PROMPTS:
                        await speak(ws, "Sorry, I didn’t catch that. Could you please repeat?", session)
                    continue

                session["repeat_count"] = 0
                log.info(f"USER → {text}")

                faq = detect_faq(text)
                if faq:
                    await speak(ws, faq, session)
                    await speak(ws, "You can ask another question or say guide me.", session)
                    continue

                t = text.lower()

                if "guide" in t or "yes" in t:
                    if session["step"] < len(STEPS):
                        await speak(ws, STEPS[session["step"]], session)
                        session["step"] += 1
                    else:
                        await speak(
                            ws,
                            "Great! Whenever you’re ready, just open the Rupeek app and check your pre approved loan limit.",
                            session
                        )
                    continue

                if "no" in t:
                    await speak(ws, "No problem. Thank you for your time.", session)
                    return

                await speak(
                    ws,
                    "I can guide you step by step or answer questions about interest, repayment, or loan limit.",
                    session
                )

    except WebSocketDisconnect:
        log.info("🔌 WebSocket disconnected safely")

# ================= MAIN =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
