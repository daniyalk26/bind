/* src/components/ContinuousChatWindow.tsx */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import MessageBubble from './MessageBubble';
import { TranscriptDrawer } from './TranscriptDrawer';
import { StreamingWebSocketClient } from '../api';

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */
interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
}

interface ConversationState {
  currentState: string;
  progress: number;
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */
const ContinuousChatWindow: React.FC = () => {
  /* ---------------- state ---------------- */
  const [messages, setMessages] = useState<Message[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState('');
  const [conversationActive, setConversationActive] = useState(false);
  const [conversationState, setConversationState] = useState<ConversationState>({
    currentState: 'start',
    progress: 0,
  });
  const [showTranscript, setShowTranscript] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* ---------------- refs ---------------- */
  const wsClient = useRef<StreamingWebSocketClient | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const audioQueueRef = useRef<string[]>([]);

  /* ------------------------------------------------------------------ */
  /*  WebSocket message handler                                         */
  /* ------------------------------------------------------------------ */
  const handleWebSocketMessage = useCallback((data: any) => {
    console.log('[WS Message]', data.type, data);

    switch (data.type) {
      case 'bot_message':
        setMessages((prev) => [
          ...prev,
          {
            id: `msg_${Date.now()}`,
            role: 'assistant',
            content: data.content,
            timestamp: new Date()
          },
        ]);
        if (data.data?.state) {
          setConversationState((prev) => ({ ...prev, currentState: data.data.state }));
        }
        break;

      case 'bot_audio':
        // Queue audio for playback
        if (data.content) {
          audioQueueRef.current.push(data.content);
          playNextAudio();
        }
        break;

      case 'live_transcript':
        // Show real-time transcription
        if (data.is_final && data.text) {
          setMessages((prev) => [
            ...prev,
            {
              id: `msg_${Date.now()}`,
              role: 'user',
              content: data.text,
              timestamp: new Date()
            },
          ]);
          setLiveTranscript('');
        } else {
          setLiveTranscript(data.text || '');
        }
        break;

      case 'user_speaking':
        setIsUserSpeaking(data.status);
        break;

      case 'bot_speaking':
        setIsBotSpeaking(data.status);
        break;

      case 'processing':
        setLiveTranscript('');
        break;

      case 'state_update':
        setConversationState(data.data);
        break;

      case 'ready':
        console.log('Streaming ready');
        setIsListening(true);
        break;

      case 'error':
        setError(data.message);
        console.error('[WS Error]', data.message);
        break;
    }
  }, []);

  /* ------------------------------------------------------------------ */
  /*  Audio playback management                                         */
  /* ------------------------------------------------------------------ */
  const playNextAudio = useCallback(() => {
    if (currentAudioRef.current || audioQueueRef.current.length === 0) {
      return;
    }

    const audioBase64 = audioQueueRef.current.shift();
    if (!audioBase64) return;

    try {
      const bytes = Uint8Array.from(atob(audioBase64), c => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: 'audio/mpeg' });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);

      audio.onended = () => {
        currentAudioRef.current = null;
        // Notify backend that bot finished speaking
        wsClient.current?.send({ type: 'bot_finished_speaking' });
        // Play next audio if available
        setTimeout(() => playNextAudio(), 100);
      };

      audio.onerror = (err) => {
        console.error('[Audio] Playback failed', err);
        currentAudioRef.current = null;
        audioQueueRef.current = []; // Clear queue on error
      };

      currentAudioRef.current = audio;
      audio.play().catch(console.error);

    } catch (err) {
      console.error('[Audio] Failed to create audio', err);
    }
  }, []);

  /* ------------------------------------------------------------------ */
  /*  WebSocket lifecycle                                               */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    wsClient.current = new StreamingWebSocketClient(
      handleWebSocketMessage,
      () => {
        setIsConnected(true);
        setError(null);
      },
      () => {
        setIsConnected(false);
        setIsListening(false);
        setConversationActive(false);
      },
      (error) => setError(error)
    );

    wsClient.current.connect();

    return () => {
      stopConversation();
      wsClient.current?.disconnect();
    };
  }, [handleWebSocketMessage]);

  /* ------------------------------------------------------------------ */
  /*  Scroll to bottom on new message                                  */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, liveTranscript]);

  /* ------------------------------------------------------------------ */
  /*  Conversation controls                                             */
  /* ------------------------------------------------------------------ */
  const startConversation = async () => {
    if (!wsClient.current?.isConnected()) {
      setError('Not connected to server');
      return;
    }

    setError(null);
    console.log('Starting conversation...');
    
    try {
      const started = await wsClient.current.startStreaming();
      console.log('Streaming started:', started);
      
      if (started) {
        setConversationActive(true);
        setIsListening(true);
      } else {
        setError('Failed to start audio streaming. Please check microphone permissions.');
      }
    } catch (error) {
      console.error('Error starting conversation:', error);
      setError(`Failed to start: ${error}`);
    }
  };

  const stopConversation = () => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    audioQueueRef.current = [];
    
    wsClient.current?.stopStreaming();
    wsClient.current?.send({ type: 'stop' });
    
    setConversationActive(false);
    setIsListening(false);
    setLiveTranscript('');
  };

  /* ------------------------------------------------------------------ */
  /*  Render                                                           */
  /* ------------------------------------------------------------------ */
  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* ---------------- Header ---------------- */}
      <header className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Voice Insurance Assistant</h1>
            <p className="text-sm text-gray-600 mt-1 flex items-center">
              <span className={`w-2 h-2 rounded-full mr-2 ${
                isConnected ? 'bg-green-500' : 'bg-red-500 animate-pulse'
              }`} />
              {isConnected ? 'Connected' : 'Connecting…'}
              {isListening && ' • Listening'}
              {isUserSpeaking && ' • You are speaking'}
              {isBotSpeaking && ' • Assistant is speaking'}
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
          {/* Welcome message when no conversation */}
          {messages.length === 0 && (
            <div className="text-center py-12">
              <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-100 rounded-full mb-4">
                <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} 
                    d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                </svg>
              </div>
              <h2 className="text-xl font-semibold text-gray-900 mb-2">
                Ready to start your insurance quote?
              </h2>
              <p className="text-gray-600 mb-6">
                Click the button below and speak naturally. I'll guide you through the process.
              </p>
            </div>
          )}

          {/* Messages */}
          {messages.map(m => <MessageBubble key={m.id} message={m} />)}

          {/* Live transcript */}
          {liveTranscript && (
            <div className="flex justify-end mb-4">
              <div className="bg-blue-50 text-blue-900 rounded-2xl rounded-br-sm px-4 py-2 max-w-2xl italic">
                {liveTranscript}...
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="mx-auto max-w-md mt-4 p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* ---------------- Control Button ---------------- */}
      <div className="bg-white border-t border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-6">
          <div className="flex justify-center">
            {!conversationActive ? (
              <button
                onClick={startConversation}
                disabled={!isConnected || conversationState.currentState === 'completed'}
                className={`
                  px-8 py-4 rounded-full font-medium text-lg transition-all
                  ${isConnected && conversationState.currentState !== 'completed'
                    ? 'bg-blue-600 text-white hover:bg-blue-700 hover:scale-105 shadow-lg'
                    : 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  }
                `}
              >
                <div className="flex items-center space-x-3">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} 
                      d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                  </svg>
                  <span>Start Conversation</span>
                </div>
              </button>
            ) : (
              <button
                onClick={stopConversation}
                className="px-8 py-4 bg-red-600 text-white rounded-full font-medium text-lg 
                         hover:bg-red-700 transition-all hover:scale-105 shadow-lg"
              >
                <div className="flex items-center space-x-3">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} 
                      d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  <span>End Conversation</span>
                </div>
              </button>
            )}
          </div>

          {/* Visual indicator */}
          {conversationActive && (
            <div className="mt-6 flex justify-center">
              <div className="flex space-x-2">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div
                    key={i}
                    className={`w-3 h-12 bg-blue-600 rounded-full transition-all ${
                      isUserSpeaking ? 'animate-pulse' : 'opacity-30'
                    }`}
                    style={{
                      animationDelay: `${i * 0.1}s`,
                      transform: isUserSpeaking ? `scaleY(${0.3 + Math.random() * 0.7})` : 'scaleY(0.3)'
                    }}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ---------------- Transcript Drawer ---------------- */}
      <TranscriptDrawer
        isOpen={showTranscript}
        onClose={() => setShowTranscript(false)}
        messages={messages}
      />
    </div>
  );
};

export default ContinuousChatWindow;
export { ContinuousChatWindow };