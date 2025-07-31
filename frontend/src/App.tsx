// src/App.tsx
import React, { useState } from 'react';
import ChatWindow from './components/ChatWindow';
import { ContinuousChatWindow } from './components/ContinuousChatWindow';

const App: React.FC = () => {
  const [mode, setMode] = useState<'manual' | 'continuous'>('continuous');

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
        </div>
      </div>

      {/* Render appropriate component */}
      {mode === 'continuous' ? <ContinuousChatWindow /> : <ChatWindow />}
    </div>
  );
};

export default App;