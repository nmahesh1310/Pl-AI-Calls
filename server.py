# server.py
import os, json, asyncio, logging, base64, requests, io, struct, time
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# ================= AUDIO CONFIG =================
SAMPLE_RATE = 16000
CHUNK_SIZE = 3200
SPEECH_THRESHOLD = 500
MIN_SPEECH_CHUNKS = 5
SILENCE_CHUNKS = 10
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
    "Next, open the app and complete your Aadhaar KYC.",
    "Finally, select your loan amount and confirm disbursal."
]

FAQ_MAP = {
    "interest": "It is zero percent interest if you repay by the due date. Otherwise EMI interest applies as shown in the app.",
    "repayment": "You must repay by the month end due date to enjoy zero percent interest.",
    "limit": "Your approved loan limit is visible inside the Rupeek app under the Click Cash banner.",
    "emi": "EMI depends on the tenure you select. The app shows the exact EMI before confirmation.",
    "processing": "Processing fee is shown clearly in the app. There are no hidden charges.",
    "mandate": "The small amount paid during mandate setup is for bank verification and gets refunded."
}

CONFIRM_WORDS = [
    "done", "completed", "finished", "downloaded",
    "installed", "opened", "yes", "okay", "ok"
]

EXIT_WORDS = ["no", "not interested", "stop", "call later"]

# ================= AUDIO HELPERS =================
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

def is_speech(pcm):
    energy = sum(abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                 for i in range(0, len(pcm)-1, 2))
    return (energy / max(len(pcm)//2, 1)) > SPEECH_THRESHOLD

def stt_safe(pcm):
    try:
        r = requests.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": SARVAM_API_KEY},
            files={"file": ("audio.wav", pcm_to_wav(pcm), "audio/wav")},
            data={"language_code": "en-IN"},
            timeout=10
        )
        return r.json().get("transcript", "").strip()
    except Exception as e:
        log.error(f"STT error: {e}")
        return ""

def tts(text):
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

async def speak(ws, text, session):
    log.info(f"BOT → {text}")
    session["bot_speaking"] = True
    pcm = await asyncio.to_thread(tts, text)
    for i in range(0, len(pcm), CHUNK_SIZE):
        await ws.send_text(json.dumps({
            "event": "media",
            "media": {"payload": base64.b64encode(pcm[i:i+CHUNK_SIZE]).decode()}
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
        "retry": 0
    }

    buf, speech = b"", b""
    silence, speech_chunks = 0, 0

    try:
        while True:
            msg = await ws.receive()
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

            buf += base64.b64decode(data["media"]["payload"])

            if len(buf) < CHUNK_SIZE:
                continue

            frame, buf = buf[:CHUNK_SIZE], buf[CHUNK_SIZE:]

            if is_speech(frame):
                speech += frame
                speech_chunks += 1
                silence = 0
            else:
                silence += 1

            if speech_chunks < MIN_SPEECH_CHUNKS and silence < SILENCE_CHUNKS:
                continue

            text = await asyncio.to_thread(stt_safe, speech)
            speech, speech_chunks, silence = b"", 0, 0

            if not text:
                session["retry"] += 1
                if session["retry"] <= 2:
                    await speak(ws, "Sorry, I didn’t get that. Could you please repeat?", session)
                continue

            session["retry"] = 0
            log.info(f"USER → {text}")
            t = text.lower()

            # Exit
            if any(x in t for x in EXIT_WORDS):
                await speak(ws, "No problem. Thank you for your time.", session)
                break

            # FAQ
            for key, ans in FAQ_MAP.items():
                if key in t:
                    await speak(ws, ans, session)
                    await speak(ws, "You can ask another question or say guide me.", session)
                    break
            else:
                # Step confirmation
                if any(x in t for x in CONFIRM_WORDS):
                    if session["step"] < len(STEPS):
                        await speak(ws, STEPS[session["step"]], session)
                        session["step"] += 1
                    else:
                        await speak(
                            ws,
                            "Great! Whenever you’re ready, just open the Rupeek app and check your pre-approved loan limit.",
                            session
                        )
                elif "guide" in t:
                    await speak(ws, STEPS[session["step"]], session)
                    session["step"] += 1
                else:
                    await speak(
                        ws,
                        "I can guide you step by step or answer questions about interest, repayment, or loan limit.",
                        session
                    )

    except WebSocketDisconnect:
        log.info("🔌 WebSocket disconnected safely")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
