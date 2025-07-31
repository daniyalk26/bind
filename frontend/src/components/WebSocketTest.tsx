// src/components/WebSocketTest.tsx
import React, { useState, useEffect } from 'react';
import { StreamingWebSocketClient } from '../api';

const WebSocketTest: React.FC = () => {
  const [logs, setLogs] = useState<string[]>([]);
  const [ws, setWs] = useState<StreamingWebSocketClient | null>(null);
  const [connected, setConnected] = useState(false);

  const addLog = (message: string) => {
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    setLogs(prev => [...prev, `[${timestamp}] ${message}`]);
  };

  const testDirectConnection = async () => {
    addLog('Starting direct WebSocket test...');
    
    // Clean up any existing connection
    if (ws) {
      addLog('Cleaning up existing connection...');
      ws.disconnect();
      setWs(null);
      await new Promise(resolve => setTimeout(resolve, 100));
    }

    const client = new StreamingWebSocketClient(
      (data) => {
        addLog(`Message received: ${data.type}`);
        console.log('Message data:', data);
      },
      () => {
        addLog('✅ Connected!');
        setConnected(true);
      },
      () => {
        addLog('❌ Disconnected');
        setConnected(false);
      },
      (error) => {
        addLog(`Error: ${error}`);
      }
    );

    setWs(client);

    try {
      addLog('Calling connect()...');
      await client.connect();
      addLog('Connect promise resolved');
      
      // Check connection state
      setTimeout(() => {
        if (client.isConnected()) {
          addLog('✅ Connection verified after 1 second');
        } else {
          addLog('❌ Connection lost after 1 second');
        }
      }, 1000);
      
    } catch (error) {
      addLog(`Connection failed: ${error}`);
    }
  };

  const testNativeWebSocket = () => {
    addLog('Starting native WebSocket test...');
    
    const url = 'ws://localhost:8000/ws/streaming?session=test_' + Date.now();
    addLog(`Connecting to: ${url}`);
    
    const ws = new WebSocket(url);
    
    ws.onopen = () => {
      addLog('✅ Native WebSocket opened');
      
      // Try to keep it alive
      const keepAlive = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'keep_alive' }));
          addLog('Sent keep-alive');
        } else {
          clearInterval(keepAlive);
        }
      }, 5000);
    };
    
    ws.onclose = (event) => {
      addLog(`❌ Native WebSocket closed: code=${event.code}, reason=${event.reason}`);
    };
    
    ws.onerror = (event) => {
      addLog(`❌ Native WebSocket error`);
      console.error('WebSocket error:', event);
    };
    
    ws.onmessage = (event) => {
      addLog(`Message: ${event.data.substring(0, 100)}...`);
    };
  };

  const clearLogs = () => {
    setLogs([]);
  };

  const disconnect = () => {
    if (ws) {
      addLog('Disconnecting...');
      ws.disconnect();
      setWs(null);
    }
  };

  return (
    <div className="p-4 max-w-4xl mx-auto">
      <h2 className="text-xl font-bold mb-4">WebSocket Connection Test</h2>
      
      <div className="space-x-2 mb-4">
        <button
          onClick={testDirectConnection}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Test StreamingWebSocketClient
        </button>
        
        <button
          onClick={testNativeWebSocket}
          className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600"
        >
          Test Native WebSocket
        </button>
        
        <button
          onClick={disconnect}
          disabled={!ws}
          className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600 disabled:opacity-50"
        >
          Disconnect
        </button>
        
        <button
          onClick={clearLogs}
          className="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600"
        >
          Clear Logs
        </button>
        
        <span className={`ml-4 px-2 py-1 rounded text-sm ${connected ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
          {connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>
      
      <div className="bg-gray-900 text-gray-100 p-4 rounded font-mono text-sm h-96 overflow-y-auto">
        {logs.length === 0 ? (
          <div className="text-gray-500">No logs yet. Click a test button to start.</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="mb-1">{log}</div>
          ))
        )}
      </div>
    </div>
  );
};

export default WebSocketTest;
export { WebSocketTest };