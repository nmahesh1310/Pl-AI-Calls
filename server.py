import os, json, asyncio, logging, sys, base64, requests, io, struct, time
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# ================= AUDIO =================
SAMPLE_RATE = 16000
MIN_CHUNK_SIZE = 3200
SPEECH_THRESHOLD = 520

SILENCE_CHUNKS = 10
MIN_SPEECH_CHUNKS = 6

POST_TTS_DELAY = 0.6
FAIL_COOLDOWN_SEC = 8

MAX_CONFUSION = 3
MAX_SILENCE_PROMPTS = 2

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
log = logging.getLogger("voicebot")

# ================= APP =================
app = FastAPI()

# ================= SCRIPT =================
PITCH_1 = (
    "Hi, my name is Neeraja, calling from Rupeek. "
    "You have a pre approved personal loan at zero interest."
)

PITCH_2 = (
    "The process is completely digital with no paperwork. "
    "You can receive instant disbursal in sixty seconds. "
    "With timely repayments you can improve your CIBIL score. "
    "This is a limited time offer. Are you interested?"
)

STEPS = [
    "Step one. Download the Rupeek app from the Play Store. Say next once done.",
    "Step two. Complete your KYC using Aadhaar. Say next once completed.",
    "Step three. Select your loan amount and confirm disbursal. Say done to finish."
]

FAQS = [
    (["emi", "monthly"], "The EMI depends on the tenure you select. The app shows the exact EMI amount."),
    (["interest", "roi"], "If repayment is missed, the loan converts into EMI with interest as shown in the app."),
    (["limit", "amount"], "Your approved loan amount is visible inside the Rupeek app."),
    (["processing", "fee"], "The processing fee is a one time charge for instant digital disbursal.")
]

NEUTRAL_WORDS = ["okay", "ok", "right", "nice", "thanks", "thank you", "fine"]
PARTIAL_QUESTIONS = ["what is", "what", "rate", "interest", "emi", "amount"]

# ================= INTENT =================
def classify(text):
    t = text.lower().strip()

    if not t:
        return "EMPTY", None

    if any(x in t for x in NEUTRAL_WORDS):
        return "NEUTRAL", None

    if any(t.startswith(p) for p in PARTIAL_QUESTIONS):
        return "PARTIAL", None

    if any(x in t for x in ["hello", "hi", "hey"]):
        return "GREETING", None

    if any(x in t for x in ["yes", "interested", "sure"]):
        return "YES", None

    if any(x in t for x in ["no", "not interested"]):
        return "NO", None

    if any(x in t for x in ["next"]):
        return "NEXT", None

    if any(x in t for x in ["previous", "back"]):
        return "PREVIOUS", None

    if any(x in t for x in ["repeat"]):
        return "REPEAT", None

    if any(x in t for x in ["done", "complete"]):
        return "DONE", None

    if any(x in t for x in ["agent", "human", "representative"]):
        return "HUMAN", None

    for keys, _ in FAQS:
        if any(k in t for k in keys):
            return "FAQ", keys

    return "UNKNOWN", None

# ================= AUDIO =================
def is_speech(pcm):
    energy = sum(abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                 for i in range(0, len(pcm)-1, 2))
    return (energy / max(len(pcm)//2, 1)) > SPEECH_THRESHOLD

def pcm_to_wav(pcm):
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(pcm)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE,
                           SAMPLE_RATE*2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(pcm)))
    buf.write(pcm)
    return buf.getvalue()

def stt_safe(pcm):
    try:
        r = requests.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": SARVAM_API_KEY},
            files={"file": ("audio.wav", pcm_to_wav(pcm), "audio/wav")},
            data={"language_code": "en-IN"},
            timeout=10
        )
        return r.json().get("transcript", "").strip() if r.status_code == 200 else ""
    except:
        return ""

def tts(text):
    r = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
        json={"text": text, "target_language_code": "en-IN", "speech_sample_rate": "16000"},
        timeout=10
    )
    r.raise_for_status()
    return base64.b64decode(r.json()["audios"][0])

async def speak(ws, text, session):
    log.info(f"🗣 BOT → {text[:80]}...")
    session["bot_speaking"] = True
    pcm = await asyncio.to_thread(tts, text)
    for i in range(0, len(pcm), MIN_CHUNK_SIZE):
        await ws.send_text(json.dumps({"event": "media", "media": {"payload": base64.b64encode(pcm[i:i+MIN_CHUNK_SIZE]).decode()}}))
    await asyncio.sleep(POST_TTS_DELAY)
    session["bot_speaking"] = False

# ================= WS =================
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    log.info("🎧 Call connected")

    session = {
        "phase": "PITCH",
        "step": 0,
        "failures": 0,
        "silence_prompts": 0,
        "handoff_asked": False,
        "bot_speaking": False,
        "started": False,
        "last_fail_ts": 0
    }

    buf, speech = b"", b""
    silence_chunks, speech_chunks = 0, 0

    try:
        while True:
            msg = await ws.receive()
            if "text" not in msg:
                continue

            data = json.loads(msg["text"])

            if data.get("event") == "start" and not session["started"]:
                await speak(ws, PITCH_1, session)
                await speak(ws, PITCH_2, session)
                session["started"] = True
                continue

            if data.get("event") != "media" or session["bot_speaking"]:
                continue

            chunk = base64.b64decode(data["media"]["payload"])
            buf += chunk

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
                continue

            intent, meta = classify(text)
            log.info(f"🗣 USER → {text} | intent={intent}")

            # reset failures on healthy interaction
            if intent in ["FAQ", "NEXT", "PREVIOUS", "REPEAT", "GREETING", "NEUTRAL"]:
                session["failures"] = 0
                session["handoff_asked"] = False

            if intent == "PARTIAL":
                continue  # wait for completion

            if intent == "FAQ":
                for keys, ans in FAQS:
                    if keys == meta:
                        await speak(ws, ans, session)
                        await speak(ws, "You can say next, repeat, or previous.", session)
                        break
                continue

            if session["phase"] == "PITCH":
                if intent == "YES":
                    session["phase"] = "STEPS"
                    await speak(ws, STEPS[0], session)
                    continue
                if intent == "NO":
                    await speak(ws, "Thank you for your time. Have a great day.", session)
                    break

            if intent == "NEXT":
                session["step"] += 1
                if session["step"] >= len(STEPS):
                    await speak(ws, "Your process is complete. Thank you.", session)
                    break
                await speak(ws, STEPS[session["step"]], session)
                continue

            if intent == "PREVIOUS":
                session["step"] = max(0, session["step"] - 1)
                await speak(ws, STEPS[session["step"]], session)
                continue

            if intent == "REPEAT":
                await speak(ws, STEPS[session["step"]], session)
                continue

            if intent == "HUMAN":
                await speak(ws, "Connecting you to a representative now.", session)
                break

            # UNKNOWN
            now = time.time()
            if now - session["last_fail_ts"] > FAIL_COOLDOWN_SEC:
                session["failures"] += 1
                session["last_fail_ts"] = now

                if session["failures"] >= MAX_CONFUSION and not session["handoff_asked"]:
                    session["handoff_asked"] = True
                    await speak(ws, "Would you like me to connect you to a representative?", session)
                else:
                    await speak(ws, "You can say next, repeat, previous, or no.", session)

    except WebSocketDisconnect:
        log.info("🔌 Call disconnected")

# ================= START =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
