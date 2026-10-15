'use client';
import { useEffect, useRef, useState } from 'react';
import type { ChatAction, ChatMessage } from '@/lib/types';

export function actionLabel(a: ChatAction) {
  const verb = { buy: 'Bought', sell: 'Sold', watch_add: 'Added to watchlist', watch_remove: 'Removed from watchlist' }[a.type];
  if (a.status === 'failed') return `Failed ${a.type} ${a.ticker}: ${a.error?.message ?? a.error?.code ?? 'error'}`;
  if (a.quantity != null) return `${verb} ${a.quantity} ${a.ticker}${a.price != null ? ` @ $${a.price.toFixed(2)}` : ''}`;
  return `${verb}: ${a.ticker}`;
}

interface Props {
  messages: ChatMessage[];
  loading: boolean;
  error: string;
  open: boolean;
  onToggle: () => void;
  onSend: (text: string) => void;
}

export default function Chat({ messages, loading, error, open, onToggle, onSend }: Props) {
  const [text, setText] = useState('');
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' });
  }, [messages, loading]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || loading) return;
    setText('');
    onSend(t);
  };

  if (!open) {
    return (
      <button
        data-testid="chat-toggle"
        onClick={onToggle}
        className="h-full w-10 border-l border-line bg-panel text-xs text-primary [writing-mode:vertical-rl]"
      >
        AI Assistant
      </button>
    );
  }

  return (
    <section className="flex h-full w-80 flex-col border-l border-line bg-panel">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <h2 className="text-xs font-semibold uppercase text-primary">FinAlly AI</h2>
        <button data-testid="chat-toggle" onClick={onToggle} className="text-muted hover:text-white" aria-label="Collapse chat">
          &raquo;
        </button>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 && <p className="text-muted">Ask about your portfolio or tell me to trade.</p>}
        {messages.map((m, i) => (
          <div key={i} data-testid="chat-message" data-role={m.role} className={m.role === 'user' ? 'text-right' : ''}>
            <div
              className={`inline-block max-w-full whitespace-pre-wrap rounded px-2 py-1.5 text-left ${
                m.role === 'user' ? 'bg-secondary/60' : 'bg-bg'
              }`}
            >
              {m.content}
            </div>
            {m.actions?.map((a, j) => (
              <div
                key={j}
                data-testid="chat-action"
                data-status={a.status}
                className={`mt-1 rounded border px-2 py-1 text-left text-xs ${
                  a.status === 'executed' ? 'border-up/50 text-up' : 'border-down/50 text-down'
                }`}
              >
                {actionLabel(a)}
              </div>
            ))}
          </div>
        ))}
        {loading && (
          <div data-testid="chat-loading" className="animate-pulse text-muted">
            Thinking...
          </div>
        )}
        {error && (
          <div data-testid="chat-error" className="text-xs text-down">
            {error}
          </div>
        )}
        <div ref={end} />
      </div>
      <form onSubmit={submit} className="flex gap-1 border-t border-line p-2">
        <input
          data-testid="chat-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Message FinAlly..."
          className="w-full rounded border border-line bg-bg px-2 py-1 outline-none focus:border-primary"
        />
        <button
          data-testid="chat-send"
          type="submit"
          disabled={loading}
          className="rounded bg-secondary px-3 py-1 font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </section>
  );
}
