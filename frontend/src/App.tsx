// src/App.tsx
import React, { useState, useEffect } from 'react';
import ChatWindow from './components/ChatWindow';
import { ContinuousChatWindow } from './components/ContinuousChatWindow';
import { WebSocketTest } from './components/WebSocketTest';
import { wsManager } from './websocketManager';

const App: React.FC = () => {
  const [mode, setMode] = useState<'manual' | 'continuous' | 'test'>('continuous');

  useEffect(() => {
    // Only cleanup when the entire app unmounts (page refresh/close)
    window.addEventListener('beforeunload', () => {
      wsManager.cleanup();
    });
    
    return () => {
      // Don't cleanup here - let the manager handle it
    };
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mode selector */}
      <div className="fixed top-4 right-4 z-50 bg-white rounded-lg shadow-lg p-2">
        <div className="flex space-x-2">
          <button
            onClick={() => setMode('continuous')}
            className={`px-4 py-2 rounded-md font-medium transition-colors ${
              mode === 'continuous'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Voice Mode
          </button>
          <button
            onClick={() => setMode('manual')}
            className={`px-4 py-2 rounded-md font-medium transition-colors ${
              mode === 'manual'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Click Mode
          </button>
          <button
            onClick={() => setMode('test')}
            className={`px-4 py-2 rounded-md font-medium transition-colors ${
              mode === 'test'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Test WS
          </button>
        </div>
      </div>

      {/* Render appropriate component */}
      {mode === 'continuous' && <ContinuousChatWindow />}
      {mode === 'manual' && <ChatWindow />}
      {mode === 'test' && <WebSocketTest />}
    </div>
  );
};

export default App;