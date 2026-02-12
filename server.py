# server.py
import os, json, asyncio, logging, base64, requests, io, struct
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# ================= AUDIO CONFIG =================
SAMPLE_RATE = 16000
MIN_CHUNK_SIZE = 3200
SPEECH_THRESHOLD = 520
SILENCE_CHUNKS = 10
MIN_SPEECH_CHUNKS = 6
POST_TTS_DELAY = 0.6

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)
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
    "Next, complete your Aadhaar KYC.",
    "Then select your loan amount and confirm disbursal."
]

FAQ_MAP = {
    "interest": "It is zero percent interest if you repay by the due date. Otherwise EMI interest applies as shown in the app.",
    "repayment": "You must repay by the month end due date to enjoy zero percent interest.",
    "limit": "Your approved loan limit is visible inside the Rupeek app under the Click Cash banner.",
    "emi": "The EMI depends on the tenure you select. The app shows the exact EMI before confirmation.",
    "processing": "The processing fee is clearly shown in the app before confirmation. There are no hidden charges.",
    "mandate": "The small amount paid during mandate setup is only for bank verification and gets refunded.",
    "risk": "There is no risk if you repay on time. Otherwise the loan converts into EMI as shown in the app."
}

FILLERS = ["hello", "hi", "yeah", "yes", "ok", "okay", "haan", "hmm", "right", "sure"]

# ================= HELPERS =================
def is_meaningful(text: str) -> bool:
    words = [w for w in text.split() if len(w) > 2]
    return len(words) >= 2

def detect_faq(text: str):
    t = text.lower()
    for key in FAQ_MAP:
        if key in t:
            return FAQ_MAP[key]
    return None

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
    energy = sum(
        abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
        for i in range(0, len(pcm)-1, 2)
    )
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
    except:
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
    for i in range(0, len(pcm), MIN_CHUNK_SIZE):
        await ws.send_text(json.dumps({
            "event": "media",
            "media": {"payload": base64.b64encode(pcm[i:i+MIN_CH_SIZE]).decode()}
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
        "step": 0
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

            buf += base64.b64decode(data["media"]["payload"])

            if len(buf) < MIN_CHUNK_SIZE:
                continue

            frame, buf = buf[:MIN_CHUNK_SIZE], buf[MIN_CHUNK_SIZE:]

            if is_speech(frame):
                speech += frame
                speech_chunks += 1
                silence_chunks = 0
            else:
                silence_chunks += 1

            if speech_chunks < MIN_SPEECH_CHUNKS and silence_chunks < SILENCE_CHUNKS:
                continue

            text = await asyncio.to_thread(stt_safe, speech)
            speech, speech_chunks, silence_chunks = b"", 0, 0

            if not text:
                continue  # silence → do nothing

            log.info(f"USER → {text}")
            t = text.lower()

            # Handle fillers
            if any(f in t for f in FILLERS):
                await speak(
                    ws,
                    "I can guide you step by step or you can ask about interest, repayment, or loan limit.",
                    session
                )
                continue

            # FAQ
            faq = detect_faq(t)
            if faq:
                await speak(ws, faq, session)
                await speak(ws, "You can ask another question or say guide me.", session)
                continue

            # Guide flow
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

            # Negative
            if "no" in t or "not interested" in t:
                await speak(ws, "No problem. Thank you for your time.", session)
                break

            # Unclear but spoken sentence
            if is_meaningful(t):
                await speak(ws, "Sorry, I didn’t catch that. Could you please repeat?", session)

    except WebSocketDisconnect:
        log.info("📴 Call disconnected")

# ================= START =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
