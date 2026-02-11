# server.py
import os, json, asyncio, logging, sys, base64
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import websockets
import uvicorn

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# ================= CONFIG =================
MIN_CHUNK_SIZE = 3200
POST_TTS_DELAY = 0.6

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("voicebot")

app = FastAPI()

# ================= SCRIPT =================
PITCH = (
    "Hi, my name is Neeraja from Rupeek. "
    "You have a pre-approved personal loan at zero interest. "
    "The process is fully digital and money is credited in sixty seconds. "
    "Would you like me to guide you through the process, or answer your questions?"
)

STEPS = [
    "First, download the Rupeek app from the Play Store.",
    "Next, complete your Aadhaar KYC.",
    "Then select your loan amount and confirm disbursal."
]

FAQ_MAP = {
    "interest": "It is zero percent interest if you repay by the due date. Otherwise EMI interest applies as shown in the app.",
    "limit": "Your approved loan limit is visible inside the Rupeek app under the Click Cash banner.",
    "emi": "EMI depends on the tenure you select. The app shows the exact EMI before confirmation.",
    "processing fee": "Processing fee is shown clearly in the app before confirmation. There are no hidden charges.",
    "documents": "No documents or income proof are required. It is fully digital.",
    "cibil": "Yes, timely repayment improves your CIBIL score.",
    "banner": "Please update the Rupeek app and reopen it. You will see the Click Cash banner.",
    "mandate": "The small amount paid during mandate setup is for bank verification and gets refunded.",
    "repayment": "You must repay by month-end to enjoy zero percent interest.",
    "risk": "There is no risk if you repay on time. Otherwise the loan converts into EMI.",
    "did not get money": "Once you complete the steps in the app, money is credited within sixty seconds."
}

def detect_faq(text: str):
    t = text.lower()
    for key in FAQ_MAP:
        if key in t:
            return FAQ_MAP[key]
    return None

async def speak(ws, text, session):
    log.info(f"BOT → {text}")
    session["bot_speaking"] = True
    # keep your existing TTS here
    session["bot_speaking"] = False
    await asyncio.sleep(POST_TTS_DELAY)

# ================= WS =================
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    log.info("📞 Call connected")

    session = {"started": False, "bot_speaking": False, "step": 0}

    dg_url = (
        "wss://api.deepgram.com/v1/listen?"
        "model=nova-2-phonecall"
        "&encoding=linear16"
        "&sample_rate=8000"
        "&language=en-IN"
        "&punctuate=true"
        "&endpointing=300"
    )

    async with websockets.connect(
        dg_url,
        extra_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    ) as dg_ws:

        async def receive_transcripts():
            async for msg in dg_ws:
                data = json.loads(msg)
                if data.get("is_final"):
                    transcript = data["channel"]["alternatives"][0]["transcript"]
                    if transcript:
                        await handle_text(ws, transcript, session)

        receiver_task = asyncio.create_task(receive_transcripts())

        try:
            while True:
                msg = await ws.receive()
                if "text" not in msg:
                    continue

                data = json.loads(msg["text"])

                if data.get("event") == "start" and not session["started"]:
                    await speak(ws, PITCH, session)
                    session["started"] = True
                    continue

                if data.get("event") == "media" and not session["bot_speaking"]:
                    audio = base64.b64decode(data["media"]["payload"])
                    await dg_ws.send(audio)

        except WebSocketDisconnect:
            log.info("📴 Call disconnected")
        finally:
            receiver_task.cancel()

async def handle_text(ws, text, session):
    log.info(f"USER → {text}")

    faq = detect_faq(text)
    if faq:
        await speak(ws, faq, session)
        return

    if "yes" in text.lower() or "guide" in text.lower():
        if session["step"] < len(STEPS):
            await speak(ws, STEPS[session["step"]], session)
            session["step"] += 1
        else:
            await speak(
                ws,
                "Great! Whenever you’re ready, just open the Rupeek app and check your pre-approved loan limit.",
                session
            )
        return

    if "no" in text.lower():
        await speak(ws, "No problem. Thank you for your time.", session)
        return

    await speak(ws, "I can guide you step by step or answer any questions about the loan.", session)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
