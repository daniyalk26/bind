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
  private shouldReconnect = true;
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
  private reconnectInterval = 5_000;
  private shouldReconnect = true;
  private sessionId: string;
  private mediaRecorder: MediaRecorder | null = null;
  private audioContext: AudioContext | null = null;
  private audioWorklet: AudioWorkletNode | null = null;
  private isStreaming = false;

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
    let id = localStorage.getItem('session_id');
    if (!id) {
      id = `session_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem('session_id', id);
    }
    return id;
  }

  /* -------- connection -------- */
  connect() {
    const wsScheme = API_BASE_URL.startsWith('https') ? 'wss' : 'ws';
    const wsHost   = API_BASE_URL.replace(/^https?:\/\//, '');
    const url      = `${wsScheme}://${wsHost}/ws/streaming?session=${this.sessionId}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => { 
      console.log('[Streaming WS] open');  
      this.onConnect(); 
    };

    this.ws.onclose = () => { 
      console.log('[Streaming WS] close'); 
      this.stopStreaming();
      this.onDisconnect();
      if (this.shouldReconnect) {
        setTimeout(() => this.connect(), this.reconnectInterval);
      }
    };

    this.ws.onerror = (e) => {
      console.error('[Streaming WS] error:', e);
      if (this.onError) this.onError('WebSocket error');
    };

    this.ws.onmessage = (e) => {
      try { 
        this.onMessage(JSON.parse(e.data)); 
      } catch { 
        console.error('[Streaming WS] bad JSON', e.data); 
      }
    };
  }

  /* -------- audio streaming -------- */
  async startStreaming(): Promise<boolean> {
    try {
      // Get microphone access
      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000
        } 
      });

      // Create audio context for PCM processing
      this.audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
        sampleRate: 16000
      });

      const source = this.audioContext.createMediaStreamSource(stream);
      const processor = this.audioContext.createScriptProcessor(4096, 1, 1);

      // Process audio to PCM
      processor.onaudioprocess = (e) => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          
          // Convert Float32Array to Int16Array (PCM16)
          const pcm16 = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          
          // Convert to base64 and send
          const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm16.buffer)));
          
          this.send({
            type: 'audio_stream',
            content: base64,
            mime_type: 'audio/pcm16',
            sample_rate: 16000
          });
        }
      };

      source.connect(processor);
      processor.connect(this.audioContext.destination);
      
      this.isStreaming = true;
            // --- keep-alive so Deepgram doesn’t drop us ---
      const pingInterval = setInterval(() => {
        if (!this.isStreaming) { clearInterval(pingInterval); return; }
        this.send({ type: 'ping' });
      }, 4000);

      console.log('[Streaming] Audio streaming started (PCM mode)');
      return true;

    } catch (error) {
      console.error('[Streaming] Failed to start audio streaming:', error);
      if (this.onError) {
        this.onError('Failed to access microphone');
      }
      return false;
    }
  }

  stopStreaming() {
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }

    if (this.mediaRecorder && this.isStreaming) {
      this.mediaRecorder.stop();
      this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
      this.mediaRecorder = null;
    }

    this.isStreaming = false;
    console.log('[Streaming] Audio streaming stopped');
  }

  /* -------- send messages -------- */
  send(payload: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  /* -------- cleanup -------- */
  disconnect() {
    this.shouldReconnect = false;
    this.stopStreaming();
    this.ws?.close();
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export { WebSocketClient, StreamingWebSocketClient };
export default WebSocketClient;