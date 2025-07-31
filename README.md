# Bind IQ Insurance Chatbot

A conversational AI chatbot for collecting insurance quotes, built with FastAPI (backend) and React/TypeScript (frontend). The system features both text-based and voice-based interaction modes, with a PostgreSQL database for data persistence.

## 🚀Features

- **Dual Interaction Modes**:
  - Text Mode (Click Mode) - Fully functional text-based conversation
  - Voice Mode - Real-time voice interaction using Deepgram (currently experiencing connectivity issues)
- **Structured Conversation Flow**: Guides users through insurance quote collection
- **Data Persistence**: PostgreSQL database for storing user information and conversation history
- **Real-time Communication**: WebSocket-based communication for instant responses
- **Voice Capabilities**: 
  - Speech-to-Text (STT) using Deepgram
  - Text-to-Speech (TTS) for audio responses
- **Progress Tracking**: Visual progress indicator for quote completion

## 🏗️ Architecture

### Backend (FastAPI)
- **FastAPI** for REST API and WebSocket endpoints
- **SQLAlchemy** for database ORM
- **PostgreSQL** for data storage
- **OpenAI GPT** for natural language generation
- **Deepgram** for voice transcription and synthesis
- **Alembic** for database migrations

### Frontend (React + TypeScript)
- **React 18** with TypeScript
- **Tailwind CSS** for styling
- **WebSocket client** for real-time communication
- **Web Audio API** for voice recording

## 📋 Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 13+
- Docker and Docker Compose (recommended)
- API Keys:
  - OpenAI API key
  - Deepgram API key

## 🛠️ Installation & Setup

### Using Docker (Recommended)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/daniyalk26/bind.git
   cd bind
   ```

2. **Create environment file**:
   Create a `.env` file in the `backend` directory:
   ```env
   # Database
   DATABASE_URL=postgresql://postgres:postgres@db:5432/insurance_chatbot
   
   # API Keys
   OPENAI_API_KEY=your_openai_api_key_here
   DEEPGRAM_API_KEY=your_deepgram_api_key_here
   
   # Optional
   LOG_LEVEL=INFO
   ```

3. **Start the application**:
   ```bash
   docker-compose up --build
   ```

   This will start:
   - Backend API on `http://localhost:8000`
   - Frontend on `http://localhost:5173`
   - PostgreSQL database on `localhost:5432`




## 🎮 Usage

1. Open your browser and navigate to `http://localhost:5173`
2. Choose between two modes:
   - **Click Mode** (Text): Type your responses in the chat interface
   - **Voice Mode**: Click "Start Conversation" to begin voice interaction

### Conversation Flow

The chatbot will guide you through collecting:
1. ZIP code
2. Full name
3. Email address
4. Vehicle information (year, make, model)
5. Vehicle usage details
6. Safety features
7. Commute information
8. Driver's license details

## 🔧 API Endpoints

### REST Endpoints
- `GET /api/health` - Health check
- `GET /api/deepgram-info` - Deepgram configuration status
- `GET /api/test-deepgram` - Test Deepgram TTS functionality

### WebSocket Endpoints
- `/ws` - Text-based chat endpoint
- `/ws/streaming` - Voice streaming endpoint
- `/ws/test` - WebSocket connectivity test
- `/ws/echo` - Echo test endpoint

## ⚠️ Known Issues

### Voice Mode Connectivity
The voice mode is currently experiencing WebSocket connection issues:
- WebSocket connections close prematurely before data transmission
- Deepgram streaming may timeout due to connection instability
- Audio chunks may not be properly transmitted to the backend

**Workaround**: Use Click Mode (text-based) for a fully functional experience.



## 📁 Project Structure

```
bind/
├── backend/
│   ├── alembic/           # Database migrations
│   ├── backend/           # Main application code
│   │   ├── audio_utils.py # Audio processing utilities
│   │   ├── conversation_engine.py # Conversation flow logic
│   │   ├── crud.py        # Database operations
│   │   ├── db.py          # Database connection
│   │   ├── deepgram_client.py # Deepgram integration
│   │   ├── main.py        # FastAPI application
│   │   ├── models.py      # SQLAlchemy models
│   │   ├── openai_client.py # OpenAI integration
│   │   └── schemas.py     # Pydantic schemas
│   ├── requirements.txt   # Python dependencies
│   └── .env              # Environment variables
├── frontend/
│   ├── src/
│   │   ├── components/    # React components
│   │   ├── utils/         # Utility functions
│   │   ├── api.ts         # API client
│   │   ├── App.tsx        # Main React component
│   │   └── main.tsx       # Entry point
│   ├── package.json       # Node dependencies
│   └── vite.config.ts     # Vite configuration
└── docker-compose.yml     # Docker orchestration
```

## 🗄️ Database Schema

The application uses PostgreSQL with the following main tables:
- `users` - User information and insurance details
- `sessions` - Conversation session state
- `messages` - Chat message history
- `vehicles` - Vehicle information


