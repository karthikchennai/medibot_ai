import { Fragment, type ReactNode, useState } from "react";
import type { ChatResponse } from "@/lib/api";
import { Icon } from "./icons";

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[\d+\])/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    if (/^\[\d+\]$/.test(part)) return <span className="citation-ref" key={i}>{part}</span>;
    return part;
  });
}

/** Small, text-only Markdown renderer. Model output never becomes raw HTML. */
function AnswerText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    if (line.startsWith("```")) {
      const code: string[] = [];
      while (++i < lines.length && !lines[i].startsWith("```")) code.push(lines[i]);
      blocks.push(<pre key={i}><code>{code.join("\n")}</code></pre>);
    } else if (line.includes("|") && i + 1 < lines.length && /^[\s|:\-]+$/.test(lines[i + 1]) && lines[i + 1].includes("---")) {
      const cells = (value: string) => value.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(x => x.trim());
      const headers = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|")) rows.push(cells(lines[i++]));
      i--;
      blocks.push(<div className="answer-table" key={i}><table><thead><tr>{headers.map((header, j) => <th key={j}>{inline(header)}</th>)}</tr></thead><tbody>{rows.map((row, j) => <tr key={j}>{row.map((cell, k) => <td key={k}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>);
    } else if (/^\s*[-*]\s/.test(line)) {
      const items: string[] = [line.replace(/^\s*[-*]\s/, "")];
      while (i + 1 < lines.length && /^\s*[-*]\s/.test(lines[i + 1])) items.push(lines[++i].replace(/^\s*[-*]\s/, ""));
      blocks.push(<ul key={i}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</ul>);
    } else if (/^\s*\d+[.)]\s/.test(line)) {
      const items: string[] = [line.replace(/^\s*\d+[.)]\s/, "")];
      while (i + 1 < lines.length && /^\s*\d+[.)]\s/.test(lines[i + 1])) items.push(lines[++i].replace(/^\s*\d+[.)]\s/, ""));
      blocks.push(<ol key={i}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</ol>);
    } else if (/^#{1,6}\s/.test(line)) {
      blocks.push(<h3 key={i}>{inline(line.replace(/^#{1,6}\s/, ""))}</h3>);
    } else {
      const paragraph = [line];
      while (i + 1 < lines.length && lines[i + 1].trim() && !/^(?:\s*[-*]\s|\s*\d+[.)]\s|#{1,6}\s|```)/.test(lines[i + 1]) && !(lines[i + 1].includes("|") && i + 2 < lines.length && /^[\s|:\-]+$/.test(lines[i + 2]))) paragraph.push(lines[++i]);
      blocks.push(<p key={i}>{paragraph.map((part, j) => <Fragment key={j}>{j > 0 && <br />}{inline(part)}</Fragment>)}</p>);
    }
  }
  return <div className="answer-text">{blocks}</div>;
}

export function Answer({ response }: { response: ChatResponse }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(response.answer);
      setCopied(true);
      setCopyError(false);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { setCopyError(true); }
  }
  return <>
    <div className="answer-labels">
      <span className={`tag ${response.blocked ? "tag-restricted" : "tag-teal"}`}><Icon name={response.blocked ? "lock" : response.retrieval_type === "sql_rag" ? "chart" : "book"} size={13} />{response.blocked ? "Access restricted" : response.retrieval_type === "sql_rag" ? "SQL analytics" : "Hybrid search"}</span>
      {!response.blocked && response.sources.length > 0 && <span className="source-count">{response.sources.length} source{response.sources.length !== 1 ? "s" : ""} referenced</span>}
    </div>
    <AnswerText text={response.answer} />
    {response.sources.length > 0 && <details className="sources-details"><summary><Icon name="book" size={16} /><span>View supporting sources</span><span className="source-number">{response.sources.length}</span><Icon name="chevron" size={14} className="detail-chevron" /></summary><div className="source-list">{response.sources.map((source, index) => <div className="source-card" key={`${source.id || source.source_document}-${index}`}><span className="source-index">{source.id || index + 1}</span><div className="source-content"><strong>{source.source_document}</strong><p>{source.section_title || "Document passage"}</p><div className="source-meta"><span>{source.collection}</span>{source.page_number != null && <span>Page {source.page_number}</span>}</div></div><Icon name="book" size={17} /></div>)}</div></details>}
    {response.sql && <details className="sql-details"><summary><Icon name="code" size={15} />View executed query<Icon name="chevron" size={14} className="detail-chevron" /></summary><pre><code>{response.sql}</code></pre></details>}
    <div className="answer-actions"><button className="subtle-button" onClick={copy} aria-label="Copy answer"><Icon name={copied ? "check" : "copy"} size={14} />{copied ? "Copied" : "Copy answer"}</button>{copyError && <span className="copy-error">Select the text to copy it.</span>}</div>
  </>;
}
