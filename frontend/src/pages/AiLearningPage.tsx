import styles from './AiLearningPage.module.css';

/* injected once into <head> — scoped to /ai-learning only.
   Hides footer, debug, decorations; compacts composer. */
const AI_LEARNING_FOCUS_CSS = `
  [data-testid="ai-learning-page"] ~ footer { display: none !important; }
  #workspaceApp .composer > details { display: none !important; }
  #workspaceApp .composer { padding: 6px 12px 8px !important; }
  #workspaceApp .composer textarea { height: 40px !important; min-height: 40px !important; max-height: 180px !important; padding: 8px 12px 4px !important; font-size: 14px !important; }
  #workspaceApp .composer-bottom { padding: 3px 6px 5px !important; gap: 6px !important; }
  #workspaceApp .composer-tool { padding: 3px 6px !important; font-size: 12px !important; gap: 3px !important; }
  #workspaceApp .composer-tool-mark { width: 16px !important; height: 16px !important; font-size: 10px !important; }
  #workspaceApp .send-button { min-width: 54px !important; padding: 5px 10px !important; font-size: 13px !important; }
  #workspaceApp .status { min-height: 0 !important; height: auto !important; margin: 0 12px; padding: 4px 8px 0 !important; overflow: visible !important; color: #7b5f4b !important; font-size: 11px !important; line-height: 1.45 !important; }
  #workspaceApp .status:empty { display: none !important; }
  #workspaceApp .head > #meta { display: none !important; }
  #workspaceApp .head { min-height: 58px !important; border-bottom-color: rgba(224,205,185,.72) !important; }
  #workspaceApp .card { border-color: rgba(224,205,185,.74) !important; border-radius: 18px !important; box-shadow: 0 16px 42px rgba(91,58,33,.08) !important; }
  #workspaceApp .side { border-color: rgba(224,205,185,.76) !important; background: linear-gradient(180deg,rgba(255,253,249,.98),rgba(249,243,234,.96)) !important; }
  #workspaceApp .side-section { border-color: rgba(224,205,185,.68) !important; }
  #workspaceApp .artifact-card { border-color: rgba(224,205,185,.76) !important; border-radius: 15px !important; background: rgba(255,253,249,.96) !important; box-shadow: 0 9px 24px rgba(91,58,33,.07) !important; max-height: 52vh !important; overflow-y: auto !important; }
  #workspaceApp .adventure-entry-card { position: relative; isolation: isolate; width: calc(100% - 18px); min-height: 126px; margin: 0 9px 10px; padding: 0; overflow: hidden; border: 1px solid rgba(114,77,49,.3); border-radius: 16px; color: #fffaf3; text-align: left; background: #5d4634; box-shadow: 0 10px 26px rgba(71,49,31,.16); }
  #workspaceApp .adventure-entry-card:hover { border-color: rgba(185,111,62,.7); transform: translateY(-1px); box-shadow: 0 14px 30px rgba(71,49,31,.2); }
  #workspaceApp .adventure-entry-card:focus-visible { outline: 3px solid rgba(211,107,53,.42); outline-offset: 3px; }
  #workspaceApp .adventure-entry-scene { position: absolute; z-index: -2; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 54% 52%; transition: transform .5s ease; }
  #workspaceApp .adventure-entry-card:hover .adventure-entry-scene { transform: scale(1.025); }
  #workspaceApp .adventure-entry-shade { position: absolute; z-index: -1; inset: 0; background: linear-gradient(90deg,rgba(47,34,24,.9) 0%,rgba(47,34,24,.62) 58%,rgba(47,34,24,.22) 100%),linear-gradient(0deg,rgba(36,24,17,.58),transparent 55%); }
  #workspaceApp .adventure-entry-copy { position: relative; display: grid; gap: 4px; width: 82%; padding: 17px 15px 36px; text-shadow: 0 1px 8px rgba(27,17,11,.55); }
  #workspaceApp .adventure-entry-copy small { color: #f0c99f; font-size: 10px; font-weight: 650; letter-spacing: .12em; }
  #workspaceApp .adventure-entry-copy strong { font-family: var(--font-display,serif); font-size: 19px; letter-spacing: .06em; }
  #workspaceApp .adventure-entry-copy [data-adventure-topic] { display: -webkit-box; overflow: hidden; color: rgba(255,250,243,.84); font-size: 11px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  #workspaceApp .adventure-entry-action { position: absolute; right: 13px; bottom: 11px; padding-bottom: 2px; border-bottom: 1px solid rgba(255,235,210,.72); color: #fff1df; font-size: 11px; font-weight: 650; letter-spacing: .06em; }
  @media (max-width: 600px) {
    #workspaceApp .adventure-entry-card { flex: 1; width: auto; min-height: 90px; margin: 0; border-radius: 13px; }
    #workspaceApp .adventure-entry-copy { gap: 2px; padding: 10px 12px 28px; }
    #workspaceApp .adventure-entry-copy strong { font-size: 17px; }
    #workspaceApp .adventure-entry-copy [data-adventure-topic] { font-size: 10px; -webkit-line-clamp: 1; }
    #workspaceApp .adventure-entry-action { right: 11px; bottom: 8px; }
  }
  body.ai-learning-active::after { content: none !important; }
  body.ai-learning-active main::before { content: none !important; }
  body.ai-learning-active [class*="cornerBasket"] { display: none !important; }
`;

export function AiLearningPage() {
  return <section className={styles.page} data-testid="ai-learning-page" aria-label="AI 对话主舞台" />;
}

export default AiLearningPage;
