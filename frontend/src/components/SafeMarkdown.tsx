import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import { cjkFriendlyStrongEmphasis, normalizeLatexDelimiters } from '../utils/cjkEmphasis';
import styles from './SafeMarkdown.module.css';

export function SafeMarkdown({ source, className = '', testId }: { source: string; className?: string; testId?: string }) {
  return <div className={`${styles.markdown} ${className}`} data-testid={testId}>
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{normalizeLatexDelimiters(cjkFriendlyStrongEmphasis(String(source || '')))}</ReactMarkdown>
  </div>;
}

export default SafeMarkdown;
