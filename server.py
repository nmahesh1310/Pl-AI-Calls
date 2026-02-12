# server.py
import os, json, asyncio, logging, base64, io, struct, time
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import whisper

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("voicebot")

# ================= APP =================
app = FastAPI()

# ================= WHISPER =================
log.info("🔊 Loading Whisper model...")
whisper_model = whisper.load_model("tiny")
log.info("✅ Whisper model loaded")

# ================= AUDIO CONFIG =================
SAMPLE_RATE = 16000
MIN_CHUNK_SIZE = 3200
SILENCE_CHUNKS = 8
MIN_SPEECH_CHUNKS = 4
POST_TTS_DELAY = 0.5

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
    "emi": "EMI depends on the tenure you select. The exact EMI is shown in the app.",
    "repayment": "You must repay by the month end due date to enjoy zero percent interest.",
    "mandate": "The small mandate amount is only for bank verification and will not be deducted."
}

# ================= HELPERS =================
def pcm_to_wav(pcm):
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(pcm)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(pcm)))
    buf.write(pcm)
    return buf.getvalue()

def whisper_stt(pcm):
    try:
        wav = pcm_to_wav(pcm)
        result = whisper_model.transcribe(
            wav,
            language="en",
            fp16=False,
            condition_on_previous_text=False
        )
        return result["text"].strip().lower()
    except Exception as e:
        log.error(f"STT error: {e}")
        return ""

async def speak(ws, text, session):
    log.info(f"BOT → {text}")
    session["bot_speaking"] = True

    # Silence packet keeps Exotel alive
    silence = base64.b64encode(b"\x00" * MIN_CHUNK_SIZE).decode()
    await ws.send_text(json.dumps({
        "event": "media",
        "media": {"payload": silence}
    }))

    # Text-only mode (Exotel plays TTS)
    await ws.send_text(json.dumps({
        "event": "say",
        "text": text
    }))

    await asyncio.sleep(POST_TTS_DELAY)
    session["bot_speaking"] = False

# ================= WS =================
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    log.info("📞 Call connected")

    session = {
        "started": False,
        "bot_speaking": False,
        "step": 0,
        "failures": 0
    }

    buf, speech = b"", b""
    silence_chunks, speech_chunks = 0, 0

    try:
        while True:
            try:
                msg = await ws.receive()
            except RuntimeError:
                log.info("🔌 WebSocket closed safely")
                break

            if "text" not in msg:
                continue

            data = json.loads(msg["text"])

            if data.get("event") == "start" and not session["started"]:
                log.info("📡 Exotel start event received")
                await speak(ws, PITCH, session)
                session["started"] = True
                continue

            if data.get("event") != "media" or session["bot_speaking"]:
                continue

            chunk = base64.b64decode(data["media"]["payload"])
            buf += chunk

            if len(buf) < MIN_CHUNK_SIZE:
                continue

            frame, buf = buf[:MIN_CHUNK_SIZE], buf[MIN_CHUNK_SIZE:]
            speech += frame
            speech_chunks += 1
            silence_chunks += 1

            if speech_chunks < MIN_SPEECH_CHUNKS and silence_chunks < SILENCE_CHUNKS:
                continue

            text = whisper_stt(speech)
            speech, speech_chunks, silence_chunks = b"", 0, 0

            if not text:
                session["failures"] += 1
                if session["failures"] >= 2:
                    await speak(ws, "Sorry, I didn’t catch that. Please repeat slowly.", session)
                    session["failures"] = 0
                continue

            log.info(f"USER → {text}")
            session["failures"] = 0

            # FAQ
            for key, answer in FAQ_MAP.items():
                if key in text:
                    await speak(ws, answer, session)
                    await speak(ws, "You can ask another question or say guide me.", session)
                    break
            else:
                if "guide" in text or "yes" in text:
                    if session["step"] < len(STEPS):
                        await speak(ws, STEPS[session["step"]], session)
                        session["step"] += 1
                    else:
                        await speak(ws, "Great! Whenever you’re ready, just open the Rupeek app and check your pre approved loan limit.", session)
                elif "no" in text:
                    await speak(ws, "No problem. Thank you for your time.", session)
                    break
                else:
                    await speak(ws, "Please say interest, repayment, loan limit, or guide me.", session)

    except WebSocketDisconnect:
        log.info("📴 Call disconnected")

# ================= START =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
