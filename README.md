

```md
# 📞 AI Voicebot – Exotel + Sarvam (FastAPI)

This repository contains a **production-ready outbound AI voicebot** built using **FastAPI**, **Exotel Voicebot**, and **Sarvam AI** for real-time **Speech-to-Text (STT)** and **Text-to-Speech (TTS)**.

The system supports:
- Outbound calls via Exotel
- Real-time bidirectional audio streaming
- Intent-based FAQ handling
- Step-wise conversational flows
- Silence retries & human handoff escalation
- Multi-language TTS support

---

## 🧱 Architecture

```

Customer Phone
↑↓
Exotel Voicebot
↑↓ (WebSocket – PCM Audio)
FastAPI Server
↑↓
Sarvam AI
(STT + TTS)

```

---

## ⚙️ Tech Stack

| Component | Technology |
|--------|------------|
| Backend | FastAPI |
| ASGI Server | Uvicorn |
| Telephony | Exotel Voicebot |
| Transport | WebSocket |
| Speech-to-Text | Sarvam AI |
| Text-to-Speech | Sarvam AI (Bulbul v3) |
| Language | Python 3.13 |
| Deployment | Render / Cloud VM |

---

## 📦 Requirements

### System
- Python **3.13+**
- Public HTTPS URL (required by Exotel)
- Linux / macOS recommended

### Python Dependencies

```

fastapi
uvicorn
requests
python-dotenv
websockets
g711
python-multipart
pydantic

````

Install dependencies:
```bash
pip install -r requirements.txt
````

---

## 🔐 Environment Variables

Create a `.env` file:

```env
PORT=10000
SARVAM_API_KEY=your_sarvam_api_key
```

> ⚠️ Exotel credentials are configured inside the Exotel Dashboard and are not stored in code.

---

## ☎️ Exotel Configuration (Mandatory)

### 1️⃣ Create a Voicebot App

* Login to **Exotel Dashboard**
* Go to **Apps → Voicebot**
* Create a new Voicebot application

### 2️⃣ Configure WebSocket URL

Set the Voicebot stream URL to:

```
wss://<your-public-domain>/ws
```

Example:

```
wss://ai-calling-somil.onrender.com/ws
```

### 3️⃣ Outbound Call URL

Use this URL while triggering calls:

```
http://my.exotel.com/<ACCOUNT_SID>/exoml/start_voice/<VOICEBOT_APP_ID>
```

---

## 🧠 Conversation Flow

```
START
 ↓
Play Pitch
 ↓
Ask Interest (Yes / No)
 ↓
 ┌─────────────┐
 │ Yes         │ → Step-wise guidance
 │ No          │ → Thank you → End call
 │ FAQ         │ → Intent-based response
 │ Silence     │ → Retry → Apology → Flag
 └─────────────┘
```

---

## 📚 FAQ Handling

FAQ responses are handled using **keyword-based intent detection**.

Example:

```
["loan", "eligible", "amount"] → eligibility response
```

No LLM dependency is used for FAQ logic.

---

## 🔁 Silence & Retry Logic

* If no speech is detected:

  * Retry **up to 2 times**
  * Small pause between retries
* After retries:

  * Apologize
  * Flag for human intervention
  * End call gracefully

---

## 🚩 Human Intervention Flag

A call is flagged when:

* Repeated silence
* Unrecognized responses
* Flow breakdown

Voice message played:

```
Sorry, I am unable to understand.
Our representative will connect with you shortly.
```

Flags are logged server-side for escalation.

---

## 🌍 Language & Voice Support

Supported languages:

| Language | Code  |
| -------- | ----- |
| English  | en-IN |
| Hindi    | hi-IN |
| Kannada  | kn-IN |
| Tamil    | ta-IN |
| Telugu   | te-IN |

Voice model:

```
bulbul:v3-beta
```

---

## 🔊 Audio Configuration

| Parameter      | Value              |
| -------------- | ------------------ |
| Sample Rate    | 16000 Hz           |
| Encoding       | PCM                |
| Chunk Size     | 3200 bytes         |
| Silence Window | ~600 ms            |
| Duplex         | Full bidirectional |

---

## ▶️ Run Locally

```bash
python server.py
```

Runs on:

```
http://localhost:10000
```

> ⚠️ Local Exotel testing requires HTTPS (use ngrok or deploy to cloud).

---

## 🚀 Deployment (Render)

Recommended start command:

```bash
uvicorn server:app --host 0.0.0.0 --port 10000
```

Ensure:

* HTTPS enabled
* WebSocket support
* `.env` configured

---

## 🧾 Logging

The system logs:

* Call lifecycle events
* WebSocket activity
* STT transcripts
* TTS playback
* Silence retries
* Human escalation flags
* Errors & disconnects

Logs are essential for debugging voice behavior.

---

## ❌ Known Limitations

* No LLM reasoning (FAQ only)
* No automatic language detection
* No CRM integration (flags logged only)

---

## 🔮 Possible Extensions

* CRM webhook on escalation
* Live agent transfer
* Call analytics dashboard
* Auto language detection
* Multi-tenant support

---

## 🧑‍💻 Maintainers

Designed for:

* Fintech outbound calling
* Loan sales automation
* High-volume voice campaigns

```

---

If you want next:
- **Flowchart diagram**
- **State machine table**
- **Production hardening checklist**
- **Exotel troubleshooting guide**

Just say the word.
::contentReference[oaicite:0]{index=0}
```
