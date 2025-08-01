// src/App.tsx
import React, { useState, useEffect } from 'react';
import ChatWindow from './components/ChatWindow';
import { ContinuousChatWindow } from './components/ContinuousChatWindow';
import { wsManager } from './websocketManager';

const App: React.FC = () => {
  const [mode, setMode] = useState<'manual' | 'continuous' >('manual');

  useEffect(() => {
    window.addEventListener('beforeunload', () => {
      wsManager.cleanup();
    });
    
    return () => {
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
            Text Mode
          </button>

        </div>
      </div>

      {/* Render appropriate component */}
      {mode === 'continuous' && <ContinuousChatWindow />}
      {mode === 'manual' && <ChatWindow />}
    </div>
  );
};

export default App;