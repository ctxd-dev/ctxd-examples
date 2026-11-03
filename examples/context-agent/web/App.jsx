import React, {createContext, useContext, useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {
  AssistantRuntimeProvider, ComposerPrimitive, MessagePrimitive, ThreadPrimitive,
  ThreadListPrimitive, ThreadListItemPrimitive, useAuiState, useLocalRuntime,
} from '@assistant-ui/react';
import {MarkdownTextPrimitive} from '@assistant-ui/react-markdown';
import {
  ArrowDown, ArrowLeft, ArrowUp, BookOpen, Check, ChevronLeft, ChevronRight, Copy,
  Database, ExternalLink, FileText, LoaderCircle, MessageCircle, Moon, PanelLeft,
  Plus, Settings2, Square, Sun, Terminal, Unplug, X,
} from 'lucide-react';
import {chatAdapter, messageText} from './adapter.js';
import './styles.css';

const UI = createContext(null);
const pageName = (path) => ({'ctxd/README.md': 'Repository README', 'ctxd/docs/public/README.md': 'Documentation index'}[path]
  || path.split('/').at(-1).replace(/\.md$/, '').replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase()));
const Mark = () => <span className="wordmark">ctxd<span>_</span></span>;
const IconButton = ({title, children, ...props}) => <button type="button" className="icon-button" aria-label={title} title={title} {...props}>{children}</button>;

async function getJSON(url, signal) {
  const response = await fetch(url, {signal});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to load documentation.');
  return data;
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const active = document.activeElement;
  const input = document.createElement('textarea');
  input.value = text;
  input.style.cssText = 'position:fixed;top:-9999px';
  document.body.append(input);
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  active?.focus();
  if (!copied) throw new Error('Copy unavailable');
}

function CopyButton({text, title = 'Copy answer', withLabel = false}) {
  const [state, setState] = useState('');
  useEffect(() => { if (state) { const timer = setTimeout(() => setState(''), 2000); return () => clearTimeout(timer); } }, [state]);
  return <button type="button" className={withLabel ? 'text-button' : 'icon-button'} aria-label={state || title} title={state || title}
    onClick={() => copyText(text).then(() => setState('Copied'), () => setState('Select text to copy'))}>
    {state === 'Copied' ? <Check size={15}/> : <Copy size={15}/>} {withLabel && (state || title)}
  </button>;
}

function CodeHeader({language, code}) {
  return <div className="code-header"><span>{language || 'text'}</span><CopyButton text={code} title="Copy code" withLabel/></div>;
}
const markdownComponents = {
  CodeHeader,
  a: ({href, children}) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  img: ({alt}) => <span>{alt || 'Image'}</span>,
};
function Markdown() { return <MarkdownTextPrimitive className="markdown" smooth={false} components={markdownComponents}/>; }

function UserMessage() {
  return <MessagePrimitive.Root className="message user-message"><div className="user-bubble"><MessagePrimitive.Parts/></div></MessagePrimitive.Root>;
}

function AssistantMessage() {
  const message = useAuiState((s) => s.message);
  const meta = message.metadata?.custom || {};
  const running = message.status?.type === 'running';
  const stopped = message.status?.reason === 'cancelled';
  const {openPanel} = useContext(UI);
  const sources = [...new Map((meta.citations || []).map((c) => [c.path, c])).values()];
  return <MessagePrimitive.Root className="message assistant-message">
    <div className="assistant-label"><Mark/><span>Support</span></div>
    {running && !meta.revealing && !meta.done && <div className="progress" role="status"><span className="thinking-dots" aria-hidden="true"><span/><span/><span/></span>{meta.progress || 'Thinking…'}</div>}
    <MessagePrimitive.Parts components={{Text: Markdown}}/>
    {running && meta.revealing && <span className="response-cursor" aria-label="Displaying answer" role="status"/>}
    {meta.warning && <p className="notice" role="status">{meta.warning}</p>}
    {meta.error && <div className="error-message" role="alert"><p>{meta.error}</p><p>You can still browse the documentation or send another message.</p></div>}
    {stopped && !meta.done && <p className="muted">Response stopped.</p>}
    {meta.done && <>
      {sources.length > 0 && <div className="source-chips" aria-label="Answer sources">{sources.slice(0, 3).map((source) =>
        <a key={source.path} href={source.url} target="_blank" rel="noopener noreferrer"><FileText size={13}/>{pageName(source.path)}<ExternalLink size={11}/></a>)}
        <button onClick={() => openPanel({type: 'evidence', meta})}><BookOpen size={13}/>{sources.length > 3 ? `All ${sources.length} sources` : 'View evidence'}</button>
      </div>}
      <div className="answer-actions"><CopyButton text={meta.answer || messageText(message)}/>
        <button className="text-button answer-info" onClick={() => openPanel({type: 'evidence', meta})}>
          CTXD{meta.cached ? ' · Saved answer' : ''} · {meta.revision?.slice(0, 7)}
        </button>
      </div>
    </>}
  </MessagePrimitive.Root>;
}

function ChatItem() {
  const {closeSidebar} = useContext(UI);
  return <ThreadListItemPrimitive.Root className="chat-item">
    <ThreadListItemPrimitive.Trigger className="chat-link" onClick={() => {if (window.innerWidth < 800) closeSidebar();}}><MessageCircle size={15}/><ThreadListItemPrimitive.Title fallback="New chat"/></ThreadListItemPrimitive.Trigger>
  </ThreadListItemPrimitive.Root>;
}

function Sidebar() {
  const {sidebar, closeSidebar, openPanel, theme, toggleTheme} = useContext(UI);
  return <>
    {sidebar && <button className="sidebar-scrim" aria-label="Close sidebar" onClick={closeSidebar}/>}
    <aside className={`sidebar ${sidebar ? 'is-open' : ''}`} aria-label="Chat navigation" inert={!sidebar}>
      <div className="sidebar-brand"><Mark/><IconButton title="Close sidebar" onClick={closeSidebar}><PanelLeft size={19}/></IconButton></div>
      <ThreadListPrimitive.Root className="thread-list">
        <ThreadListPrimitive.New className="nav-button" onClick={() => {if (window.innerWidth < 800) closeSidebar();}}><Plus size={19}/>New chat</ThreadListPrimitive.New>
        <button className="nav-button" onClick={() => openPanel({type: 'docs'})}><BookOpen size={18}/>Documentation</button>
        <p className="sidebar-heading">Your chats</p>
        <div className="chat-items"><ThreadListPrimitive.Items components={{ThreadListItem: ChatItem}}/></div>
      </ThreadListPrimitive.Root>
      <div className="sidebar-footer">
        <p>Chats stay in this tab until you reload.</p>
        <div><button className="nav-button" onClick={() => openPanel({type: 'settings'})}><Settings2 size={18}/>Settings</button>
          <IconButton title={theme === 'dark' ? 'Use light theme' : 'Use dark theme'} onClick={toggleTheme}>{theme === 'dark' ? <Sun size={18}/> : <Moon size={18}/>}</IconButton>
        </div>
      </div>
    </aside>
  </>;
}

function Welcome() {
  const {config} = useContext(UI);
  const icons = [Terminal, Unplug, Database];
  const titles = ['Get started', 'Fix a connection', 'Keep context current'];
  return <div className="welcome">
    <div className="welcome-mark"><Mark/></div>
    <h1>How can I help with CTXD?</h1>
    <p>Setup, integrations, and troubleshooting.<br/>Answers grounded in the official docs.</p>
    <div className="suggestions">{(config?.questions || []).map((question, i) => {
      const Icon = icons[i] || BookOpen;
      return <ThreadPrimitive.Suggestion key={question} prompt={question} send clearComposer disabled={!config?.model_ready} className="suggestion">
        <Icon size={20}/><strong>{titles[i] || 'Explore CTXD'}</strong><span>{question}</span>
      </ThreadPrimitive.Suggestion>;
    })}</div>
  </div>;
}

function Chat() {
  const {config, configError, sidebar, setSidebar, openPanel} = useContext(UI);
  const running = useAuiState((s) => s.thread.isRunning);
  const loading = useAuiState((s) => s.thread.isLoading);
  const threadId = useAuiState((s) => s.threads.mainThreadId);
  const empty = useAuiState((s) => s.thread.messages.length === 0);
  return <main className="main">
    <header className="topbar"><div>{!sidebar && <IconButton title="Open sidebar" onClick={() => setSidebar(true)}><PanelLeft size={20}/></IconButton>}
      <span className="app-title">CTXD <span>Support</span></span></div>
      <button className="text-button docs-button" onClick={() => openPanel({type: 'docs'})}><BookOpen size={16}/><span>Docs</span></button>
    </header>
    <ThreadPrimitive.Root key={threadId} className={`thread ${empty ? 'empty-thread' : ''}`} aria-busy={loading}>
      <ThreadPrimitive.Viewport className="viewport">
        {empty && <Welcome/>}
        <div className="messages"><ThreadPrimitive.Messages components={{UserMessage, AssistantMessage}}/></div>
      </ThreadPrimitive.Viewport>
      <div className="composer-area">
        <ThreadPrimitive.ScrollToBottom className="scroll-bottom" aria-label="Scroll to latest message"><ArrowDown size={18}/></ThreadPrimitive.ScrollToBottom>
        {(configError || config?.model_ready === false) && <p className="service-notice" role="status">{configError || 'AI answers are unavailable until the operator configures a Gemini key. You can browse the docs.'}</p>}
        <ComposerPrimitive.Root className="composer">
          <ComposerPrimitive.Input className="composer-input" aria-label="Message CTXD" placeholder="Ask anything about CTXD…" maxLength={1200} rows={1} maxRows={7} autoFocus disabled={loading || !config?.model_ready}/>
          <div className="composer-toolbar"><button className="context-button" type="button" onClick={() => openPanel({type: 'docs'})}><BookOpen size={15}/>Official docs</button>
            {running ? <ComposerPrimitive.Cancel className="stop-button" aria-label="Stop response"><Square size={12} fill="currentColor"/>Stop</ComposerPrimitive.Cancel>
              : <ComposerPrimitive.Send className="send-button" aria-label="Send message" disabled={loading || !config?.model_ready}><ArrowUp size={20}/></ComposerPrimitive.Send>}
          </div>
        </ComposerPrimitive.Root>
        <p className="composer-note">CTXD Support can make mistakes. Check the sources.</p>
      </div>
    </ThreadPrimitive.Root>
  </main>;
}

function Docs() {
  const [listing, setListing] = useState(null), [error, setError] = useState('');
  const [selected, setSelected] = useState(null), [start, setStart] = useState(1), [page, setPage] = useState(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getJSON('./api/sources', controller.signal).then(setListing).catch((e) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setError(''); setLoading(true); setPage(null);
    getJSON(`./api/source?${new URLSearchParams({path: selected.path, start_line: start})}`, controller.signal)
      .then(setPage).catch((e) => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selected, start]);
  return <>
    <p className="panel-description">CTXD · {listing ? `${listing.items.length} documents` : 'Loading documentation…'}</p>
    {listing && <p className="revision">Revision {listing.revision.slice(0, 7)}</p>}
    {selected ? <>
      <button className="text-button back-button" onClick={() => {setSelected(null); setError('');}}><ArrowLeft size={16}/>All documents</button>
      <h3>{pageName(selected.path)}</h3>
      {loading && <p className="progress"><LoaderCircle className="spin" size={16}/>Loading document…</p>}
      {page && <>
        <a className="text-button" href={page.url} target="_blank" rel="noopener noreferrer">Open on GitHub<ExternalLink size={14}/></a>
        <pre className="source-code">{page.lines.map((line, i) => <div key={i}><span>{page.start_line + i}</span>{line || ' '}</div>)}</pre>
        <div className="pagination"><button disabled={start <= 1} onClick={() => setStart(Math.max(1, start - 80))}><ChevronLeft size={16}/>Previous</button><span>Lines {page.start_line}–{page.end_line}</span><button disabled={page.lines.length < 80} onClick={() => setStart(start + 80)}>Next<ChevronRight size={16}/></button></div>
      </>}
    </> : <div className="doc-list">{listing?.items.map((item) => <button key={item.path} onClick={() => {setStart(1); setSelected(item);}}><FileText size={18}/><span><strong>{pageName(item.path)}</strong><small>{item.path}</small></span><ChevronRight size={16}/></button>)}</div>}
    {error && <p className="notice" role="alert">{error}</p>}
  </>;
}

function Evidence({meta}) {
  return <>
    <p className="panel-description">This answer used CTXD at revision <strong>{meta.revision?.slice(0, 7)}</strong>.</p>
    <div className="evidence-stats"><span>{meta.sources_read} documents read</span><span>{meta.elapsed_seconds?.toFixed(1)}s</span><span>{meta.cached ? 'Cached answer' : '1 model request'}</span></div>
    {(meta.citations || []).map((source, i) => <a className="citation-row" key={`${source.label}-${i}`} href={source.url} target="_blank" rel="noopener noreferrer"><span className="citation-number">{i + 1}</span><span><strong>{pageName(source.path)}</strong><small>Lines {source.start_line}–{source.end_line}</small></span><ExternalLink size={15}/></a>)}
    {meta.reads?.length > 0 && <><h3>Retrieved passages</h3>{meta.reads.map((read, i) => <details className="passage" key={i}><summary>{pageName(read.path)} <span>Lines {read.start_line}–{read.end_line}</span></summary><pre>{read.lines.join('\n')}</pre></details>)}</>}
    {meta.cached && <p className="muted">This saved answer includes its original citation links. It did not make another model request.</p>}
  </>;
}

function Settings() {
  const {config, theme, toggleTheme} = useContext(UI);
  const [status, setStatus] = useState(null);
  useEffect(() => { const c = new AbortController(); getJSON('./api/status', c.signal).then(setStatus).catch(() => {}); return () => c.abort(); }, []);
  return <>
    <h3>Appearance</h3><button className="setting-row" onClick={toggleTheme}><span>{theme === 'dark' ? 'Dark' : 'Light'} theme</span>{theme === 'dark' ? <Sun size={18}/> : <Moon size={18}/>}</button>
    {status?.sync_error && <p className="notice">{status.sync_error}</p>}
    <h3>Usage</h3><p className="panel-description">{config?.visitor_window ?? 5} questions per 10 minutes and {config?.visitor_day ?? 20} per day per IP address. Shared model capacity may impose additional waits.</p>
    <p className="muted">Conversations stay in this tab. Reloading clears chat history. The operator supplies the model key; no key is stored in your browser.</p>
    <h3>Open source</h3><p className="panel-description">Chat components by <a href="https://github.com/assistant-ui/assistant-ui" target="_blank" rel="noopener noreferrer">assistant-ui</a>. Icons by <a href="https://lucide.dev" target="_blank" rel="noopener noreferrer">Lucide</a>.</p>
    <a className="text-button" href="./third-party-notices.txt" target="_blank" rel="noopener noreferrer">Open-source licenses<ExternalLink size={14}/></a>
  </>;
}

function Panel() {
  const {panel, closePanel} = useContext(UI);
  const dialog = useRef(null);
  useEffect(() => { if (panel) dialog.current.showModal(); else dialog.current?.close(); }, [panel]);
  return <dialog ref={dialog} className="panel" aria-label={panel?.type === 'docs' ? 'Documentation' : panel?.type === 'evidence' ? 'Answer sources' : 'Settings'} onCancel={(e) => {e.preventDefault(); closePanel();}} onClick={(e) => {if (e.target === dialog.current) closePanel();}}>
    {panel && <div className="panel-inner"><header><h2>{panel.type === 'docs' ? 'Documentation' : panel.type === 'evidence' ? 'Answer sources' : 'Settings'}</h2><IconButton title="Close panel" onClick={closePanel}><X size={20}/></IconButton></header>
      <div className="panel-content">{panel.type === 'docs' ? <Docs/> : panel.type === 'evidence' ? <Evidence meta={panel.meta}/> : <Settings/>}</div>
    </div>}
  </dialog>;
}

function App() {
  const [config, setConfig] = useState(null), [configError, setConfigError] = useState('');
  const [sidebar, setSidebar] = useState(window.innerWidth >= 800);
  const [panel, setPanel] = useState(null);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('ctxd-support-theme') === 'light' ? 'light' : 'dark'; } catch { return 'dark'; } });
  const runtimeRef = useRef(null);
  const adapter = useMemo(() => chatAdapter(runtimeRef), []);
  const runtime = useLocalRuntime(adapter);
  runtimeRef.current = runtime;
  useEffect(() => { const c = new AbortController(); getJSON('./api/config', c.signal).then(setConfig).catch(() => {if (!c.signal.aborted) setConfigError('Cannot connect to the assistant. Reload to try again.');}); return () => c.abort(); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; try {localStorage.setItem('ctxd-support-theme', theme);} catch {} }, [theme]);
  const closeSidebar = () => setSidebar(false);
  const value = {config, configError, sidebar, setSidebar, closeSidebar, panel,
    openPanel: (next) => {setPanel(next); if (window.innerWidth < 800) closeSidebar();}, closePanel: () => setPanel(null),
    theme, toggleTheme: () => setTheme((value) => value === 'dark' ? 'light' : 'dark')};
  return <AssistantRuntimeProvider runtime={runtime}><UI.Provider value={value}><div className={`app ${sidebar ? 'sidebar-open' : ''}`}><Sidebar/><Chat/><Panel/></div></UI.Provider></AssistantRuntimeProvider>;
}

createRoot(document.getElementById('root')).render(<App/>);
