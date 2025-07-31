/* src/components/ChatWindow.tsx */
import React, { useState, useRef, useEffect } from 'react';
import MessageBubble             from './MessageBubble';
import { TranscriptDrawer }      from './TranscriptDrawer';
import WebSocketClient           from '../api';

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */
interface Message {
  id      : string;
  role    : 'user' | 'assistant';
  content : string;
  timestamp: Date;
}

interface ConversationState {
  currentState: string;
  progress    : number;
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */
const ChatWindow: React.FC = () => {
  /* ---------------- state ---------------- */
  const [messages,          setMessages]          = useState<Message[]>([]);
  const [input,             setInput]             = useState('');
  const [isConnected,       setIsConnected]       = useState(false);
  const [isTyping,          setIsTyping]          = useState(false);
  const [isRecording,       setIsRecording]       = useState(false);
  const [conversationState, setConversationState] = useState<ConversationState>({
    currentState: 'start',
    progress: 0,
  });
  const [showTranscript, setShowTranscript] = useState(false);

  /* ---------------- refs ---------------- */
  const wsClient          = useRef<WebSocketClient | null>(null);
  const messagesEndRef    = useRef<HTMLDivElement>(null);
  const inputRef          = useRef<HTMLInputElement>(null);
  const mediaRecorderRef  = useRef<MediaRecorder | null>(null);
  const recordedChunksRef = useRef<BlobPart[]>([]);

  /* ------------------------------------------------------------------ */
  /*  WebSocket lifecycle                                               */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    wsClient.current = new WebSocketClient(
      /* onMessage */
      (data) => {
        /* ------- assistant text ------- */
        if (data.type === 'bot_message') {
          setIsTyping(false);
          setMessages((prev) => [
            ...prev,
            { id:`msg_${Date.now()}`,role:'assistant',content:data.content,timestamp:new Date() },
          ]);
          if (data.data?.state) {
            setConversationState((prev)=>({...prev,currentState:data.data.state}));
          }
        }

        /* ------- assistant audio ------- */
        if (data.type === 'bot_audio') {
          try {
            const bytes = Uint8Array.from(atob(data.content), c => c.charCodeAt(0));
            const blob  = new Blob([bytes], { type: 'audio/mpeg' });
            const url   = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.play().catch(console.error);
          } catch (err) { console.error('[audio] playback failed', err); }
        }

        /* ------- progress update ------- */
        if (data.type === 'state_update') setConversationState(data.data);
      },

      /* onConnect / onDisconnect */
      () => setIsConnected(true),
      () => setIsConnected(false)
    );

    wsClient.current.connect();
    return () => wsClient.current?.disconnect();
  }, []);

  /* ------------------------------------------------------------------ */
  /*  scroll to bottom on new message                                   */
  /* ------------------------------------------------------------------ */
  useEffect(() => { messagesEndRef.current?.scrollIntoView({behavior:'smooth'}); }, [messages]);

  /* ------------------------------------------------------------------ */
  /*  helpers                                                           */
  /* ------------------------------------------------------------------ */
  const sendMessage = () => {
    if (!input.trim() || !isConnected) return;

    setMessages(prev => [
      ...prev,
      { id:`msg_${Date.now()}`, role:'user', content:input, timestamp:new Date() },
    ]);
    setIsTyping(true);
    wsClient.current?.send({ type:'user_message', content:input });
    setInput('');
    inputRef.current?.focus();
  };

  const handleKeyPress = (e:React.KeyboardEvent) => {
    if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  /* ---------------- audio recording ---------------- */
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
      const rec    = new MediaRecorder(stream, { mimeType:'audio/webm;codecs=opus' });
      recordedChunksRef.current = [];

      rec.ondataavailable = e => { if (e.data.size) recordedChunksRef.current.push(e.data); };
      rec.onstop = () => {
        const blob = new Blob(recordedChunksRef.current, { type:'audio/webm' });
        blob.arrayBuffer().then(buf => wsClient.current?.sendAudio(buf));
        stream.getTracks().forEach(t => t.stop());
        setIsRecording(false);
      };

      mediaRecorderRef.current = rec;
      rec.start();
      setIsRecording(true);
    } catch (err) {
      console.error('Mic permission denied or no mic available', err);
      alert('Unable to access microphone.');
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
  };

  /* ------------------------------------------------------------------ */
  /*  render                                                            */
  /* ------------------------------------------------------------------ */
  return (
    <div className="flex flex-col h-screen bg-gray-50">

      {/* ---------------- Header ---------------- */}
      <header className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Insurance Onboarding</h1>
            <p className="text-sm text-gray-600 mt-1 flex items-center">
              <span className={`w-2 h-2 rounded-full mr-2 ${isConnected?'bg-green-500':'bg-red-500 animate-pulse'}`}/>
              {isConnected ? 'Connected' : 'Connecting…'}
            </p>
          </div>

          <div className="flex items-center space-x-4">
            <button
              onClick={() => setShowTranscript(true)}
              className="text-sm text-blue-600 hover:text-blue-700 font-medium"
            >
              View Transcript
            </button>

            <div className="w-48">
              <div className="text-xs text-gray-600 mb-1">
                Progress: {conversationState.progress}%
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${conversationState.progress}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* ---------------- Messages ---------------- */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-4 py-6">
          {messages.map(m => <MessageBubble key={m.id} message={m}/>)}

          {isTyping && (
            <div className="flex justify-start mb-4">
              <div className="flex items-end">
                <div className="mx-2">
                  <div className="w-8 h-8 rounded-full bg-gray-600 flex items-center justify-center text-white text-sm font-semibold">B</div>
                </div>
                <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-2">
                  <div className="flex space-x-2">
                    {Array.from({length:3}).map((_,i)=>(
                      <div key={i} className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay:`${i*0.1}s`}}/>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef}/>
        </div>
      </div>

      {/* ---------------- Input & Mic ---------------- */}
      <div className="bg-white border-t border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-4">
          <div className="flex space-x-2 items-center">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={e=>setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder={conversationState.currentState==='completed'
                ? 'Onboarding completed!'
                : 'Type your response…'}
              disabled={!isConnected || conversationState.currentState==='completed'}
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600 disabled:bg-gray-100"
            />

            {/* Mic button */}
            <button
              onClick={isRecording ? stopRecording : startRecording}
              disabled={!isConnected}
              className={`p-2 rounded-full transition ${
                isRecording ? 'bg-red-600 text-white animate-pulse' : 'bg-blue-600 text-white hover:bg-blue-700'
              }`}
              title={isRecording ? 'Stop recording' : 'Speak'}
            >
              {isRecording ? (
                <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              ) : (
                <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 1v11m0 0c2.761 0 5-2.239 5-5V6c0-2.761-2.239-5-5-5S7 3.239 7 6v1c0 2.761 2.239 5 5 5zm0 0v9m-4 0h8"/>
                </svg>
              )}
            </button>

            {/* Send button */}
            <button
              onClick={sendMessage}
              disabled={!isConnected || !input.trim() || conversationState.currentState==='completed'}
              className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300"
            >
              Send
            </button>
          </div>
        </div>
      </div>

      {/* ---------------- Transcript Drawer ---------------- */}
      <TranscriptDrawer
        isOpen={showTranscript}
        onClose={()=>setShowTranscript(false)}
        messages={messages}
      />
    </div>
  );
};

export default ChatWindow;
export { ChatWindow };
