// Chat-UI-Block - 3 lines of code usage
// import { ChatBlock } from './blocks/Chat'
// <ChatBlock apiKey="cb_key" provider="deepseek" />

import { useState, useRef, useEffect } from 'react';

interface ChatBlockProps {
  apiKey: string;
  provider?: 'deepseek' | 'groq' | 'openai';
  model?: string;
  streaming?: boolean;
  maxHeight?: string;
}

export const ChatBlock: React.FC<ChatBlockProps> = ({ 
  apiKey, 
  provider = 'deepseek',
  streaming = true,
  maxHeight = '500px'
}) => {
  const [messages, setMessages] = useState<Array<{role: string, content: string}>>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim()) return;
    
    const userMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      if (streaming) {
        // Streaming response
        const response = await fetch(`${API_BASE}/v1/chat/stream`, {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`
          },
          body: JSON.stringify({ 
            message: input, 
            provider,
            stream: true 
          })
        });

        const reader = response.body?.getReader();
        const decoder = new TextDecoder();
        let assistantContent = '';

        setMessages(prev => [...prev, { role: 'assistant', content: '' }]);

        while (reader) {
          const { done, value } = await reader.read();
          if (done) break;
          
          const chunk = decoder.decode(value);
          const lines = chunk.split('\n');
          
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') continue;
              try {
                const parsed = JSON.parse(data);
                const content = parsed.choices?.[0]?.delta?.content || '';
                assistantContent += content;
                setMessages(prev => {
                  const newMessages = [...prev];
                  newMessages[newMessages.length - 1].content = assistantContent;
                  return newMessages;
                });
              } catch {}
            }
          }
        }
      } else {
        // Non-streaming
        const response = await fetch(`${API_BASE}/v1/chat`, {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`
          },
          body: JSON.stringify({ message: input, provider, stream: false })
        });
        
        const data = await response.json();
        setMessages(prev => [...prev, { 
          role: 'assistant', 
          content: data.text || data.error || 'Error'
        }]);
      }
    } catch (error) {
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: 'Error: Failed to connect to Chat-Block'
      }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="chat-block" style={{ maxHeight, display: 'flex', flexDirection: 'column' }}>
      <div className="chat-messages" style={{ flex: 1, overflow: 'auto', padding: '10px' }}>
        {messages.map((msg, idx) => (
          <div key={idx} className={`message ${msg.role}`} style={{
            padding: '8px 12px',
            margin: '5px 0',
            borderRadius: '8px',
            background: msg.role === 'user' ? '#007bff' : '#f0f0f0',
            color: msg.role === 'user' ? 'white' : 'black',
            alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: '80%'
          }}>
            {msg.content}
          </div>
        ))}
        {loading && <div className="loading">...</div>}
        <div ref={messagesEndRef} />
      </div>
      
      <div className="chat-input" style={{ display: 'flex', padding: '10px', borderTop: '1px solid #ddd' }}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
          placeholder="Type message..."
          style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
        />
        <button 
          onClick={sendMessage}
          disabled={loading}
          style={{ 
            marginLeft: '10px', 
            padding: '8px 16px',
            background: '#007bff',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: loading ? 'not-allowed' : 'pointer'
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
};
