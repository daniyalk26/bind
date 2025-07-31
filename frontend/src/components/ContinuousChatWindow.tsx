/* src/components/ContinuousChatWindow.tsx */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import MessageBubble from './MessageBubble';
import { TranscriptDrawer } from './TranscriptDrawer';
import { wsManager } from '../websocketManager';

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
  const [userClicked, setUserClicked] = useState(false);
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
  const [isStreamingReady, setIsStreamingReady] = useState(false);

  /* ---------------- refs ---------------- */
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const audioQueueRef = useRef<string[]>([]);
  const conversationActiveRef = useRef(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  /* ------------------------------------------------------------------ */
  /*  Audio playback management                                         */
  /* ------------------------------------------------------------------ */
  const playNextAudio = useCallback(() => {
    // Check if user has interacted using DOM attribute
    const hasUserInteracted = document.body.getAttribute('data-user-clicked') === 'true';
    
    if (!hasUserInteracted || currentAudioRef.current || audioQueueRef.current.length === 0) {
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
        URL.revokeObjectURL(url);
        // Notify backend that bot finished speaking
        const client = wsManager.getClient();
        if (client?.isConnected()) {
          client.send({ type: 'bot_finished_speaking' });
        }
        // Play next audio if available
        setTimeout(() => playNextAudio(), 100);
      };

      audio.onerror = (err) => {
        console.error('[Audio] Playback failed', err);
        currentAudioRef.current = null;
        audioQueueRef.current = [];
      };

      currentAudioRef.current = audio;
      audio.play().catch(err => {
        console.warn('[Audio] autoplay blocked', err);
        currentAudioRef.current = null;
      });

    } catch (err) {
      console.error('[Audio] Failed to create audio', err);
    }
  }, []);

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
          // Use setTimeout to ensure state is updated
          setTimeout(() => playNextAudio(), 0);
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
        console.log('Backend streaming ready, starting audio...');
        setIsStreamingReady(true);
        // Only start audio streaming after backend is ready
        if (conversationActiveRef.current) {
          const client = wsManager.getClient();
          if (client?.isConnected()) {
            // Add a small delay to ensure everything is initialized
            setTimeout(() => {
              client.startStreaming().then(started => {
                if (started) {
                  setIsListening(true);
                } else {
                  setError('Failed to start audio streaming. Please check microphone permissions.');
                  setConversationActive(false);
                  conversationActiveRef.current = false;
                }
              });
            }, 100);
          }
        }
        break;

      case 'error':
        setError(data.message);
        console.error('[WS Error]', data.message);
        // If we get an error, stop the conversation
        if (conversationActiveRef.current) {
          stopConversation();
        }
        break;
    }
  }, [playNextAudio]);

  /* ------------------------------------------------------------------ */
  /*  Stop conversation callback                                        */
  /* ------------------------------------------------------------------ */
  const stopConversation = useCallback(() => {
    console.log('Stopping conversation...');
    
    // Update ref immediately
    conversationActiveRef.current = false;
    
    // Stop audio playback
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    audioQueueRef.current = [];
    
    // Stop streaming
    const client = wsManager.getClient();
    if (client) {
      client.stopStreaming();
      // Only send stop if connected
      if (client.isConnected()) {
        client.send({ type: 'stop' });
      }
    }
    
    // Reset states
    setConversationActive(false);
    setIsListening(false);
    setLiveTranscript('');
    setIsStreamingReady(false);
    setError(null);
  }, []);

  /* ------------------------------------------------------------------ */
  /*  WebSocket lifecycle                                               */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    let mounted = true;

    // Initialize WebSocket connection
    const initConnection = async () => {
      try {
        await wsManager.initialize();
        
        if (!mounted) return;

        // Subscribe to WebSocket events
        unsubscribeRef.current = wsManager.subscribe(
          handleWebSocketMessage,
          () => {
            if (mounted) {
              setIsConnected(true);
              setError(null);
            }
          },
          () => {
            if (mounted) {
              setIsConnected(false);
              setIsListening(false);
              setConversationActive(false);
              conversationActiveRef.current = false;
              setIsStreamingReady(false);
            }
          },
          (error) => {
            if (mounted) {
              setError(error);
            }
          }
        );

        // Check if already connected
        const client = wsManager.getClient();
        if (client?.isConnected() && mounted) {
          setIsConnected(true);
        }
      } catch (err) {
        console.error('Failed to initialize WebSocket:', err);
        if (mounted) {
          setError('Failed to connect to server');
        }
      }
    };

    initConnection();

    // Cleanup on unmount
    return () => {
      mounted = false;
      if (conversationActiveRef.current) {
        stopConversation();
      }
      if (unsubscribeRef.current) {
        unsubscribeRef.current();
      }
      // Don't disconnect the WebSocket here - let the manager handle it
    };
  }, [handleWebSocketMessage, stopConversation]);

  /* ------------------------------------------------------------------ */
  /*  Update conversation active ref when state changes                 */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    conversationActiveRef.current = conversationActive;
  }, [conversationActive]);

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
    const client = wsManager.getClient();
    if (!client?.isConnected()) {
      setError('Not connected to server');
      return;
    }

    setError(null);
    setUserClicked(true);
    // Mark user interaction in DOM for playNextAudio
    document.body.setAttribute('data-user-clicked', 'true');
    console.log('Starting conversation...');
    
    // Set conversation active - audio will start when we receive 'ready' from backend
    setConversationActive(true);
    conversationActiveRef.current = true;
  };

  // Test microphone function
  const testMicrophone = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      console.log('Microphone test successful:', stream.getAudioTracks());
      stream.getTracks().forEach(track => track.stop());
      alert('Microphone access working!');
    } catch (e: any) {
      console.error('Microphone test failed:', e);
      alert('Microphone access failed: ' + e.message);
    }
  };

  // Test Deepgram function
  const testDeepgram = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/test-deepgram');
      const data = await response.json();
      console.log('Deepgram test result:', data);
      
      if (data.status === 'ok' && data.tts_test.working) {
        alert('Deepgram is working correctly!');
      } else {
        alert(`Deepgram test failed: ${data.error || 'Unknown error'}\n\nAPI Key Set: ${data.deepgram_api_key_set}`);
      }
    } catch (e: any) {
      console.error('Deepgram test failed:', e);
      alert('Failed to test Deepgram: ' + e.message);
    }
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
              {conversationActive && !isStreamingReady && ' • Initializing...'}
            </p>
          </div>

          <div className="flex items-center space-x-4">
            <button
              onClick={testMicrophone}
              className="text-sm text-gray-600 hover:text-gray-800"
            >
              Test Mic
            </button>

            <button
              onClick={testDeepgram}
              className="text-sm text-gray-600 hover:text-gray-800"
            >
              Test Deepgram
            </button>
            
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
                onPointerDown={() => {
                  setUserClicked(true);
                  document.body.setAttribute('data-user-clicked', 'true');
                }}
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