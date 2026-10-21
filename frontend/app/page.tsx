"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Answer } from "@/components/answer";
import { Brand, Icon } from "@/components/icons";
import { ApiError, request, SESSION_KEY, type ChatResponse, type Health, type LoginResponse, type Session } from "@/lib/api";
import { PROMPTS, ROLE_LABELS } from "@/lib/demo";

type Message = { id: string; kind: "user"; text: string } | { id: string; kind: "answer"; response: ChatResponse } | { id: string; kind: "error"; text: string; question: string };
type Auth = Session & { token: string };
const COLLECTIONS = ["general", "clinical", "nursing", "billing", "equipment"];

function initials(name: string) { return name.split(/[\s.]+/).filter(Boolean).slice(0, 2).map(x => x[0]).join("").toUpperCase(); }
function roleLabel(role: Session["user"]["role"]) { return ROLE_LABELS[role] || role.replaceAll("_", " "); }

function Readiness({ health, unreachable, refresh, compact = false }: { health: Health | null; unreachable: boolean; refresh: () => void; compact?: boolean }) {
  if (!unreachable && !health) return null;
  if (!unreachable && health?.checks.llm_configured && health?.checks.index_ready) return null;
  const text = unreachable ? "The server is currently unreachable. Start the backend to connect." : !health?.checks.llm_configured ? "Configure Bedrock credentials or Groq fallback in the backend environment, then restart the server." : "Document search is getting ready. Run document ingestion to enable knowledge answers.";
  return <div className={`readiness-notice ${compact ? "readiness-compact" : ""}`} role="status"><Icon name="alert" size={17} /><div><strong>{unreachable ? "Connection needed" : "Workspace setup"}</strong><p>{text}</p></div><button type="button" className="icon-button" onClick={refresh} title="Check connection" aria-label="Refresh service status"><Icon name="refresh" size={16} /></button></div>;
}

export default function Home() {
  const [auth, setAuth] = useState<Auth | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [health, setHealth] = useState<Health | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [authError, setAuthError] = useState("");
  const [username, setUsername] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [showPassword, setShowPassword] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const activeRequest = useRef<AbortController | null>(null);

  const refreshHealth = useCallback(async () => {
    try {
      const result = await request<Health>("/health", { signal: AbortSignal.timeout(8000) });
      setHealth(result);
      setUnreachable(false);
    } catch { setUnreachable(true); }
  }, []);

  useEffect(() => {
    void refreshHealth();
    const timer = window.setInterval(() => void refreshHealth(), 30000);
    const controller = new AbortController();
    const token = sessionStorage.getItem(SESSION_KEY);
    if (!token) { setRestoring(false); return () => { clearInterval(timer); controller.abort(); }; }
    request<Session>("/me", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]) }, token).then(session => {
      setAuth({ ...session, token });
    }).catch(error => {
      if (controller.signal.aborted) return;
      sessionStorage.removeItem(SESSION_KEY);
      if (error instanceof ApiError && error.status === 401) setAuthError("Your session has expired. Please sign in again.");
    }).finally(() => { if (!controller.signal.aborted) setRestoring(false); });
    return () => { clearInterval(timer); controller.abort(); activeRequest.current?.abort(); };
  }, [refreshHealth]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; }
  }, [question, auth]);

  function resetChat() {
    activeRequest.current?.abort();
    activeRequest.current = null;
    setMessages([]);
    setQuestion("");
    setSending(false);
    setSidebarOpen(false);
    inputRef.current?.focus();
  }

  function logout(message = "") {
    resetChat();
    sessionStorage.removeItem(SESSION_KEY);
    setAuth(null);
    setAuthError(message);
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    if (signingIn) return;
    setSigningIn(true);
    setAuthError("");
    try {
      const result = await request<LoginResponse>("/login", { method: "POST", body: JSON.stringify({ username: username.trim(), password }), signal: AbortSignal.timeout(20000) });
      sessionStorage.setItem(SESSION_KEY, result.access_token);
      setMessages([]);
      setAuth({ user: result.user, collections: result.collections, token: result.access_token });
      void refreshHealth();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Sign-in failed. Please try again.");
    } finally { setSigningIn(false); }
  }

  async function submit(text: string, retryId?: string) {
    const value = text.trim();
    if (!value || !auth || activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort("timeout"), 300000);
    setQuestion("");
    setSending(true);
    inputRef.current?.focus();
    if (retryId) setMessages(previous => previous.filter(message => message.id !== retryId));
    else setMessages(previous => [...previous, { id: crypto.randomUUID(), kind: "user", text: value }]);
    try {
      const response = await request<ChatResponse>("/chat", { method: "POST", body: JSON.stringify({ question: value }), signal: controller.signal }, auth.token);
      if (!controller.signal.aborted) setMessages(previous => [...previous, { id: crypto.randomUUID(), kind: "answer", response: { ...response, sources: response.sources || [] } }]);
    } catch (error) {
      if (controller.signal.aborted && controller.signal.reason !== "timeout") return;
      if (error instanceof ApiError && error.status === 401) { logout("Your session has expired. Please sign in again."); return; }
      const message = controller.signal.reason === "timeout" ? "This request took longer than expected. Please try again." : error instanceof Error ? error.message : "Something went wrong. Please try again.";
      setMessages(previous => [...previous, { id: crypto.randomUUID(), kind: "error", text: message, question: value }]);
      void refreshHealth();
    } finally {
      clearTimeout(timeout);
      if (activeRequest.current === controller) { activeRequest.current = null; setSending(false); }
    }
  }

  if (restoring) return <main className="loading-screen"><Brand /><span className="spinner" /><p>Opening your workspace…</p></main>;

  if (!auth) return <main className="login-page">
    <section className="login-story">
      <Brand light />
      <div className="story-content"><span className="eyebrow"><span className="status-dot" />THE HOSPITAL KNOWLEDGE WORKSPACE</span><h1>Better answers.<br />Connected care.</h1><p>Your hospital’s knowledge, in one conversation. Find the guidance you need and get back to what matters.</p><div className="knowledge-art" aria-hidden="true"><div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" /><div className="art-center"><Icon name="plus" size={43} /></div><div className="art-node node-top"><Icon name="book" size={21} /><span>Trusted knowledge</span></div><div className="art-node node-right"><Icon name="heart" size={22} /><span>Connected care</span></div><div className="art-node node-bottom"><Icon name="shield" size={21} /><span>Role-aware access</span></div><div className="art-tick tick-one" /><div className="art-tick tick-two" /><div className="art-tick tick-three" /></div></div>
      <div className="story-footer"><span><Icon name="shield" size={15} />Built around your role</span><span>Powered by {health?.provider || "AI"}</span></div>
    </section>
    <section className="login-panel"><div className="mobile-brand"><Brand /></div><div className="login-content"><span className="eyebrow text-teal">WELCOME TO MEDIBOT</span><h2>Your knowledge.<br />Your workspace.</h2><p className="login-intro">Enter your account username and password to access your workspace.</p><form onSubmit={login} className="login-form"><label htmlFor="username">Username</label><input id="username" name="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required disabled={signingIn} spellCheck={false} /><label htmlFor="password">Password</label><div className="password-field"><input id="password" name="password" autoComplete="current-password" type={showPassword ? "text" : "password"} value={password} onChange={event => setPassword(event.target.value)} required disabled={signingIn} /><button type="button" className="icon-button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(value => !value)}><Icon name="eye" size={18} /></button></div>{authError && <div className="form-error" role="alert"><Icon name="alert" size={17} /><span>{authError}</span></div>}<button type="submit" className="primary-button" disabled={signingIn || !username.trim() || !password}>{signingIn ? <><span className="spinner" />Signing in…</> : <>Enter workspace<Icon name="arrow" size={19} /></>}</button></form><div className="login-security"><Icon name="lock" size={14} /><span>Your signed-in role determines the knowledge you can access.</span></div><Readiness health={health} unreachable={unreachable} refresh={() => void refreshHealth()} compact /></div><div className="login-footnote">MediBot <span>Hospital knowledge, connected.</span></div></section>
  </main>;

  const prompts = PROMPTS[auth.user.role] || PROMPTS.doctor;
  const ready = !unreachable && Boolean(health?.checks.llm_configured && health?.checks.index_ready);
  return <main className="workspace">
    {sidebarOpen && <button className="sidebar-overlay" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`} aria-label="Workspace navigation"><div className="sidebar-brand"><Brand light /><button className="icon-button sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close navigation"><Icon name="close" /></button></div><button className="new-chat-button" onClick={resetChat}><Icon name="plus" size={19} />New conversation<span>↗</span></button><div className="sidebar-label">WORKSPACE</div><div className="nav-item active"><Icon name="spark" size={18} /><span>Knowledge assistant</span><span className="nav-indicator" /></div><div className="sidebar-collections"><div className="sidebar-label">YOUR KNOWLEDGE ACCESS<span>{auth.collections.length}</span></div><div className="collection-list">{COLLECTIONS.map(collection => { const allowed = auth.collections.map(value => value.toLowerCase()).includes(collection); return <div key={collection} className={`collection-item ${allowed ? "allowed" : "restricted"}`}><Icon name={allowed ? "book" : "lock"} size={16} /><span>{collection}</span>{allowed ? <span className="collection-dot" /> : <span className="restricted-label">Restricted</span>}</div>; })}</div></div><div className="sidebar-note"><span className="shield-circle"><Icon name="shield" size={20} /></span><strong>Knowledge with boundaries.</strong><p>Answers are grounded in the documents your role can access.</p></div><div className="account-panel"><span className="account-avatar">{initials(auth.user.name)}</span><div className="account-info"><strong>{auth.user.name}</strong><span>{roleLabel(auth.user.role)}</span></div><button className="icon-button" onClick={() => logout()} title="Sign out" aria-label="Sign out"><Icon name="logout" size={18} /></button></div></aside>
    <section className="main-panel"><header className="workspace-header"><div className="header-left"><button className="icon-button mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button><span className="breadcrumb">Workspace</span><Icon name="chevron" size={13} /><strong>Knowledge assistant</strong></div><div className="header-right"><span className={`connection-pill ${ready ? "connected" : "pending"}`}><span className="status-dot" />{ready ? "Workspace ready" : unreachable ? "Offline" : "Setup pending"}</span><span className="role-pill"><Icon name="shield" size={13} />{roleLabel(auth.user.role)}</span></div></header>
      <div className="conversation-scroll" ref={scrollRef}><div className="conversation-inner"><Readiness health={health} unreachable={unreachable} refresh={() => void refreshHealth()} />{messages.length === 0 ? <section className="welcome"><div className="welcome-heading"><span className="welcome-symbol"><Icon name="spark" size={29} /></span><span className="eyebrow text-teal">A LITTLE CLARITY GOES A LONG WAY</span><h1>What can we help you<br />find today?</h1><p>Ask about hospital guidance, explore protocols, or find an answer in your knowledge collections.</p></div><div className="prompt-section-label"><span>A place to start</span><span>TAILORED TO YOUR ROLE</span></div><div className="prompt-grid">{prompts.map(prompt => <button className="prompt-card" key={prompt.title} onClick={() => void submit(prompt.question)} disabled={sending}><span className="prompt-icon"><Icon name={prompt.icon} size={21} /></span><strong>{prompt.title}</strong><p>{prompt.question}</p><Icon name="arrow" size={18} className="prompt-arrow" /></button>)}</div><div className="welcome-trust"><Icon name="shield" size={16} /><span>{auth.collections.length} permitted collections</span><span className="trust-separator" /><span>Source-backed answers</span><span className="trust-separator" /><span>Powered by {health?.provider || "AI"}</span></div></section> : <div className="messages" aria-live="polite" aria-relevant="additions">{messages.map(message => message.kind === "user" ? <div className="message user-message" key={message.id}><div className="user-bubble">{message.text}</div><span className="message-avatar user-avatar">{initials(auth.user.name)}</span></div> : <div className="message assistant-message" key={message.id}><span className="message-avatar assistant-avatar"><Icon name="plus" size={19} /></span><div className="assistant-body"><div className="assistant-name">MediBot<span>Knowledge assistant</span></div>{message.kind === "answer" ? <Answer response={message.response} /> : <div className="chat-error" role="alert"><Icon name="alert" size={18} /><div><strong>We couldn’t complete that answer</strong><p>{message.text}</p><button className="retry-button" onClick={() => void submit(message.question, message.id)} disabled={sending}><Icon name="refresh" size={14} />Try again</button></div></div>}</div></div>)}{sending && <div className="message assistant-message"><span className="message-avatar assistant-avatar"><Icon name="plus" size={19} /></span><div className="assistant-body"><div className="assistant-name">MediBot<span>Knowledge assistant</span></div><div className="thinking" role="status"><span className="thinking-dots"><i /><i /><i /></span>Finding an answer within your access…</div></div></div>}</div>}</div></div>
      <div className="composer-area"><div className="composer-inner"><form className={`composer ${sending ? "composer-busy" : ""}`} onSubmit={event => { event.preventDefault(); void submit(question); }}><textarea ref={inputRef} aria-label="Ask MediBot a question" placeholder="Ask MediBot a question…" value={question} maxLength={3000} rows={1} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(question); } }} /><div className="composer-toolbar"><span><Icon name="shield" size={13} />{roleLabel(auth.user.role)} access<span className="composer-shortcut"> · Enter to send</span></span><button className="send-button" type="submit" disabled={!question.trim() || sending} aria-label="Send question">{sending ? <span className="spinner" /> : <Icon name="send" size={18} />}</button></div></form><p className="composer-footnote">MediBot can make mistakes. Verify guidance against the cited source before clinical use.</p></div></div>
    </section>
  </main>;
}
