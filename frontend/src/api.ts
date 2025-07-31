// frontend/src/api.ts
import axios from 'axios';

/* ───────────── Base URL helpers ───────────── */
const ENV_BASE = import.meta.env.VITE_API_URL as string | undefined;
export const API_BASE_URL =
  ENV_BASE || `${window.location.protocol}//${window.location.hostname}:8000`;

/* ③  Axios instance — still handy for REST calls */
export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

/* ───────────── WebSocket helper ───────────── */
class WebSocketClient {
  private ws: WebSocket | null = null;
  private reconnectInterval = 5_000;
  private shouldReconnect = false;
  private sessionId: string;

  constructor(
    private onMessage: (data: any) => void,
    private onConnect: () => void,
    private onDisconnect: () => void
  ) {
    this.sessionId = this.getOrCreateSessionId();
  }

  /* -------- utils -------- */
  private getOrCreateSessionId() {
    let id = localStorage.getItem('session_id');
    if (!id) {
      id = `session_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem('session_id', id);
    }
    return id;
  }

  /* -------- public -------- */
  connect() {
    const wsScheme = API_BASE_URL.startsWith('https') ? 'wss' : 'ws';
    const wsHost   = API_BASE_URL.replace(/^https?:\/\//, '');
    const url      = `${wsScheme}://${wsHost}/ws?session=${this.sessionId}`;

    this.ws = new WebSocket(url);

    this.ws.onopen    = () => { console.log('[WS] open');  this.onConnect(); };
    this.ws.onclose   = () => { console.log('[WS] close'); this.onDisconnect();
                                if (this.shouldReconnect) setTimeout(()=>this.connect(),this.reconnectInterval); };
    this.ws.onerror   = (e) =>  console.error('[WS] error:', e);
    this.ws.onmessage = (e) => {
      try   { this.onMessage(JSON.parse(e.data)); }
      catch { console.error('[WS] bad JSON', e.data); }
    };
  }

  /** generic JSON payload */
  send(payload: any) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
  }

  /** helper for audio -> base64 (future voice feature) */
  sendAudio(buf: ArrayBuffer) {
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    this.send({ type: 'user_audio', content: b64 });
  }

  disconnect() { this.shouldReconnect = false; this.ws?.close(); }
}

/* ───────────── Streaming WebSocket Client for continuous voice ───────────── */
class StreamingWebSocketClient {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 3;
  private reconnectInterval = 5_000;
  private sessionId: string;
  private mediaRecorder: MediaRecorder | null = null;
  private mediaStream: MediaStream | null = null;
  private isStreaming = false;
  private keepAliveInterval: number | null = null;
  private connectionPromise: Promise<void> | null = null;
  private isConnecting = false;
  private isDisconnecting = false;
  private messageQueue: any[] = [];

  constructor(
    private onMessage: (data: any) => void,
    private onConnect: () => void,
    private onDisconnect: () => void,
    private onError?: (error: string) => void
  ) {
    this.sessionId = this.getOrCreateSessionId();
  }

  /* -------- utils -------- */
  private getOrCreateSessionId() {
    // Generate unique session ID for each connection
    return `session_${Date.now()}_${crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`;
  }

  /* -------- connection management -------- */
  async connect(): Promise<void> {
    // Prevent multiple simultaneous connections
    if (this.connectionPromise) {
      console.log('[Streaming WS] Connection already in progress');
      return this.connectionPromise;
    }

    if (this.ws?.readyState === WebSocket.OPEN) {
      console.log('[Streaming WS] Already connected');
      return Promise.resolve();
    }

    if (this.isConnecting || this.isDisconnecting) {
      console.log('[Streaming WS] Connection state changing, please wait');
      return Promise.resolve();
    }

    this.connectionPromise = this._doConnect();
    try {
      await this.connectionPromise;
    } finally {
      this.connectionPromise = null;
    }
  }

  private async _doConnect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.isConnecting = true;
        
        // Clean up any existing connection
        if (this.ws) {
          console.log('[Streaming WS] Cleaning up existing connection, state:', this.ws.readyState);
          this.ws.onopen = null;
          this.ws.onmessage = null;
          this.ws.onerror = null;
          this.ws.onclose = null;
          if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
            this.ws.close();
          }
          this.ws = null;
          // Wait for cleanup
          setTimeout(() => {}, 50);
        }

        const wsScheme = API_BASE_URL.startsWith('https') ? 'wss' : 'ws';
        const wsHost = API_BASE_URL.replace(/^https?:\/\//, '');
        const url = `${wsScheme}://${wsHost}/ws/streaming?session=${this.sessionId}`;

        console.log('[Streaming WS] Connecting to:', url);
        this.ws = new WebSocket(url);

        // Set a connection timeout
        const connectionTimeout = setTimeout(() => {
          if (this.ws?.readyState !== WebSocket.OPEN) {
            console.error('[Streaming WS] Connection timeout');
            this.ws?.close();
            this.isConnecting = false;
            reject(new Error('Connection timeout'));
          }
        }, 10000); // 10 second timeout

        this.ws.onopen = () => {
          clearTimeout(connectionTimeout);
          console.log('[Streaming WS] open');
          this.isConnecting = false;
          this.reconnectAttempts = 0;
          this.startKeepAlive();
          this.flushMessageQueue();
          this.onConnect();
          resolve();
        };

        this.ws.onmessage = (e) => {
          try {
            const data = JSON.parse(e.data);
            this.onMessage(data);
          } catch (error) {
            console.error('[Streaming WS] Failed to parse message:', error);
          }
        };

        this.ws.onerror = (event) => {
          clearTimeout(connectionTimeout);
          console.error('[Streaming WS] error:', event);
          this.isConnecting = false;
        };

        this.ws.onclose = (event) => {
          clearTimeout(connectionTimeout);
          console.log('[Streaming WS] close');
          this.isConnecting = false;
          this.stopKeepAlive();
          this.stopStreaming();
          this.onDisconnect();
          
          // Only attempt reconnect if we're not explicitly disconnecting and haven't exceeded attempts
          if (!this.isDisconnecting && this.isStreaming && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.attemptReconnect();
          }
        };
      } catch (error) {
        this.isConnecting = false;
        console.error('[Streaming WS] Connection failed:', error);
        reject(error);
      }
    });
  }

  private attemptReconnect() {
    this.reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 10000);
    console.log(`[Streaming WS] Reconnecting in ${delay}ms... (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    
    setTimeout(() => {
      if (!this.isDisconnecting) {
        this.connect().catch(err => {
          console.error('[Streaming WS] Reconnection failed:', err);
          if (this.onError) {
            this.onError('Failed to reconnect to server');
          }
        });
      }
    }, delay);
  }

  private startKeepAlive() {
    this.stopKeepAlive();
    this.keepAliveInterval = window.setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.send({ type: 'keep_alive' });
      }
    }, 30000); // Every 30 seconds
  }

  private stopKeepAlive() {
    if (this.keepAliveInterval !== null) {
      clearInterval(this.keepAliveInterval);
      this.keepAliveInterval = null;
    }
  }

  private flushMessageQueue() {
    while (this.messageQueue.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
      const message = this.messageQueue.shift();
      this.ws.send(JSON.stringify(message));
    }
  }

  /* -------- continuous audio streaming -------- */
  async startStreaming(): Promise<boolean> {
    if (!this.isConnected()) {
      console.error('[Streaming] Cannot start streaming: not connected');
      return false;
    }

    if (this.mediaRecorder?.state === 'recording') {
      console.log('[Streaming] Already recording');
      return true;
    }

    try {
      console.log('[Streaming] Requesting microphone access...');
      
      // Get microphone access with optimal settings for Deepgram
      this.mediaStream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          channelCount: 1,
          sampleRate: 48000, // Browser default, Deepgram will handle
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        } 
      });

      console.log('[Streaming] Microphone access granted');
      
      // Create MediaRecorder with opus codec (native browser format)
      const options = {
        mimeType: 'audio/webm;codecs=opus',
        audioBitsPerSecond: 128000
      };

      this.mediaRecorder = new MediaRecorder(this.mediaStream, options);
      
      this.mediaRecorder.ondataavailable = async (event) => {
        if (event.data.size > 0 && this.isConnected()) {
          try {
            // Convert blob to base64 and send
            const reader = new FileReader();
            reader.onloadend = () => {
              const base64 = reader.result?.toString().split(',')[1];
              if (base64) {
                this.send({
                  type: 'audio_stream',
                  content: base64
                });
              }
            };
            reader.readAsDataURL(event.data);
          } catch (error) {
            console.error('[Streaming] Error sending audio:', error);
          }
        }
      };

      this.mediaRecorder.onerror = (event) => {
        console.error('[Streaming] MediaRecorder error:', event);
        if (this.onError) {
          this.onError('Audio recording error');
        }
      };

      // Start recording with 100ms chunks for low latency
      this.mediaRecorder.start(100);
      this.isStreaming = true;
      console.log('[Streaming] Audio streaming started');
      return true;

    } catch (error: any) {
      console.error('[Streaming] Failed to start audio:', error);
      if (this.onError) {
        this.onError(`Microphone access failed: ${error.message}`);
      }
      return false;
    }
  }

  stopStreaming() {
    console.log('[Streaming] Stopping audio stream...');
    
    this.isStreaming = false;
    
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      try {
        this.mediaRecorder.stop();
      } catch (e) {
        console.error('[Streaming] Error stopping recorder:', e);
      }
      this.mediaRecorder = null;
    }

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => {
        track.stop();
        console.log('[Streaming] Stopped track:', track.label);
      });
      this.mediaStream = null;
    }

    console.log('[Streaming] Audio streaming stopped');
  }

  /* -------- send messages -------- */
  send(payload: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    } else {
      // Queue messages if not connected
      this.messageQueue.push(payload);
      console.warn('[Streaming WS] Not connected, message queued. State:', this.ws?.readyState);
    }
  }

  /* -------- cleanup -------- */
  disconnect() {
    console.log('[Streaming WS] Disconnecting...');
    this.isDisconnecting = true;
    this.connectionPromise = null;
    this.stopKeepAlive();
    this.stopStreaming();
    
    if (this.ws) {
      this.ws.onclose = null; // Prevent reconnection attempts
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close();
      }
      this.ws = null;
    }
    
    this.messageQueue = [];
    this.isDisconnecting = false;
    this.reconnectAttempts = 0;
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export { WebSocketClient, StreamingWebSocketClient };
export default WebSocketClient;