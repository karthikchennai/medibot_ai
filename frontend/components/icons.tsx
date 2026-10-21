import type { CSSProperties } from "react";

export type IconName = "plus" | "arrow" | "send" | "book" | "heart" | "chart" | "tool" | "shield" | "lock" | "chevron" | "logout" | "spark" | "menu" | "close" | "check" | "alert" | "refresh" | "eye" | "code" | "copy";

const paths: Record<IconName, React.ReactNode> = {
  plus: <path d="M12 5v14M5 12h14" />,
  arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
  send: <><path d="m5 12 7-7 7 7M12 5v15" /></>,
  book: <><path d="M12 5v15M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z" /></>,
  heart: <><path d="M20.3 4.8a5 5 0 0 0-7.1 0L12 6l-1.2-1.2a5 5 0 0 0-7.1 7.1L12 20l8.3-8.1a5 5 0 0 0 0-7.1Z" /><path d="M3 12h5l2-4 3 8 2-4h6" /></>,
  chart: <><path d="M4 3v17h17M8 15v-4m5 4V6m5 9v-6" /></>,
  tool: <path d="M14 6a5 5 0 0 0-6 6L3 17a2.8 2.8 0 0 0 4 4l5-5a5 5 0 0 0 6-6l-3 3-4-4 3-3Z" />,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" /><path d="m8 12 3 3 5-6" /></>,
  lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v2" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  logout: <><path d="M9 4H4v16h5m6-13 5 5-5 5M8 12h12" /></>,
  spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  check: <path d="m5 12 4 4L19 6" />,
  alert: <><path d="m12 3 10 18H2L12 3Z" /><path d="M12 9v5m0 3h.01" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14-5L3 9m0-6v6h6m-5 4a8 8 0 0 0 14 5l3-3m0 6v-6h-6" /></>,
  eye: <><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>,
  code: <><path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18" /></>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V3H3v13h5" /></>,
};

export function Icon({ name, size = 20, className, style }: { name: IconName; size?: number; className?: string; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}>{paths[name]}</svg>;
}

export function Brand({ light = false }: { light?: boolean }) {
  return <div className={`brand ${light ? "brand-light" : ""}`}><span className="brand-mark"><Icon name="plus" size={25} /></span><span>Medi<span className="brand-bold">Bot</span><span className="brand-dot">.</span></span></div>;
}
