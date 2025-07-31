// src/websocketManager.ts
// Robust singleton WebSocket manager with proper state management

import { StreamingWebSocketClient } from './api';

type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'disconnecting';

class WebSocketManager {
  private static instance: WebSocketManager;
  private client: StreamingWebSocketClient | null = null;
  private connectionState: ConnectionState = 'disconnected';
  private initPromise: Promise<void> | null = null;
  private subscribers: Set<(data: any) => void> = new Set();
  private connectSubscribers: Set<() => void> = new Set();
  private disconnectSubscribers: Set<() => void> = new Set();
  private errorSubscribers: Set<(error: string) => void> = new Set();
  private messageQueue: any[] = [];
  private reconnectTimer: NodeJS.Timeout | null = null;
  private referenceCount = 0;  // Track number of components using the connection

  private constructor() {
    // Bind methods to ensure correct context
    this.handleMessage = this.handleMessage.bind(this);
    this.handleConnect = this.handleConnect.bind(this);
    this.handleDisconnect = this.handleDisconnect.bind(this);
    this.handleError = this.handleError.bind(this);
  }

  static getInstance(): WebSocketManager {
    if (!WebSocketManager.instance) {
      WebSocketManager.instance = new WebSocketManager();
    }
    return WebSocketManager.instance;
  }

  private handleMessage(data: any) {
    // Store message if no subscribers yet
    if (this.subscribers.size === 0) {
      this.messageQueue.push(data);
    } else {
      this.subscribers.forEach(callback => callback(data));
    }
  }

  private handleConnect() {
    console.log('[WebSocketManager] Connected');
    this.connectionState = 'connected';
    this.connectSubscribers.forEach(callback => callback());
  }

  private handleDisconnect() {
    console.log('[WebSocketManager] Disconnected');
    this.connectionState = 'disconnected';
    this.disconnectSubscribers.forEach(callback => callback());
    
    // Clear any pending reconnect
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private handleError(error: string) {
    console.error('[WebSocketManager] Error:', error);
    this.errorSubscribers.forEach(callback => callback(error));
  }

  async initialize(): Promise<void> {
    // If already connected, just return
    if (this.connectionState === 'connected' && this.client?.isConnected()) {
      console.log('[WebSocketManager] Already connected');
      return Promise.resolve();
    }

    // If currently connecting, wait for that connection
    if (this.connectionState === 'connecting' && this.initPromise) {
      console.log('[WebSocketManager] Connection in progress, waiting...');
      return this.initPromise;
    }

    // If disconnecting, wait a bit then try again
    if (this.connectionState === 'disconnecting') {
      console.log('[WebSocketManager] Currently disconnecting, waiting...');
      await new Promise(resolve => setTimeout(resolve, 100));
      return this.initialize();
    }

    // Start new connection
    this.connectionState = 'connecting';
    this.initPromise = this._doInitialize();
    
    try {
      await this.initPromise;
    } finally {
      this.initPromise = null;
    }
  }

  private async _doInitialize(): Promise<void> {
    console.log('[WebSocketManager] Starting connection...');
    
    try {
      // Clean up any existing client
      if (this.client) {
        this.client.disconnect();
        this.client = null;
        // Wait a bit for cleanup
        await new Promise(resolve => setTimeout(resolve, 50));
      }

      // Create new client with bound handlers
      this.client = new StreamingWebSocketClient(
        this.handleMessage,
        this.handleConnect,
        this.handleDisconnect,
        this.handleError
      );

      // Connect and wait for connection
      await this.client.connect();
      
      // Give it a moment to stabilize
      await new Promise(resolve => setTimeout(resolve, 100));
      
      // Verify connection is still active
      if (!this.client.isConnected()) {
        throw new Error('Connection failed to establish');
      }

      console.log('[WebSocketManager] Connection established successfully');
      
      // Flush any queued messages to new subscribers
      if (this.messageQueue.length > 0 && this.subscribers.size > 0) {
        const messages = [...this.messageQueue];
        this.messageQueue = [];
        messages.forEach(msg => this.handleMessage(msg));
      }
      
    } catch (error) {
      console.error('[WebSocketManager] Connection failed:', error);
      this.connectionState = 'disconnected';
      throw error;
    }
  }

  subscribe(
    onMessage: (data: any) => void,
    onConnect?: () => void,
    onDisconnect?: () => void,
    onError?: (error: string) => void
  ): () => void {
    // Increment reference count
    this.referenceCount++;
    console.log(`[WebSocketManager] Subscribe called, ref count: ${this.referenceCount}`);
    
    // Add subscribers
    this.subscribers.add(onMessage);
    if (onConnect) this.connectSubscribers.add(onConnect);
    if (onDisconnect) this.disconnectSubscribers.add(onDisconnect);
    if (onError) this.errorSubscribers.add(onError);

    // If already connected, notify immediately
    if (this.connectionState === 'connected' && onConnect) {
      setTimeout(() => onConnect(), 0);
    }

    // Send any queued messages
    if (this.messageQueue.length > 0) {
      const messages = [...this.messageQueue];
      this.messageQueue = [];
      messages.forEach(msg => onMessage(msg));
    }

    // Return unsubscribe function
    return () => {
      this.referenceCount--;
      console.log(`[WebSocketManager] Unsubscribe called, ref count: ${this.referenceCount}`);
      
      this.subscribers.delete(onMessage);
      if (onConnect) this.connectSubscribers.delete(onConnect);
      if (onDisconnect) this.disconnectSubscribers.delete(onDisconnect);
      if (onError) this.errorSubscribers.delete(onError);
      
      // Only disconnect if no more subscribers
      if (this.referenceCount === 0 && this.subscribers.size === 0) {
        console.log('[WebSocketManager] No more subscribers, scheduling disconnect...');
        // Delay disconnect to allow for quick tab switches
        setTimeout(() => {
          if (this.referenceCount === 0 && this.subscribers.size === 0) {
            this.disconnect();
          }
        }, 1000);
      }
    };
  }

  getClient(): StreamingWebSocketClient | null {
    return this.connectionState === 'connected' ? this.client : null;
  }

  isConnected(): boolean {
    return this.connectionState === 'connected' && (this.client?.isConnected() ?? false);
  }

  getConnectionState(): ConnectionState {
    return this.connectionState;
  }

  async disconnect(): Promise<void> {
    if (this.connectionState === 'disconnected' || this.connectionState === 'disconnecting') {
      return;
    }

    console.log('[WebSocketManager] Disconnecting...');
    this.connectionState = 'disconnecting';
    
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.client) {
      this.client.disconnect();
      this.client = null;
    }

    this.connectionState = 'disconnected';
    this.messageQueue = [];
  }

  async cleanup(): Promise<void> {
    console.log('[WebSocketManager] Cleaning up...');
    await this.disconnect();
    this.subscribers.clear();
    this.connectSubscribers.clear();
    this.disconnectSubscribers.clear();
    this.errorSubscribers.clear();
  }
}

export const wsManager = WebSocketManager.getInstance();