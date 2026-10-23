import { Navigate } from 'react-router-dom';

/** Legacy import kept for old links; the formal AI workspace lives at /ai-learning. */
export function QaPage() {
  return <Navigate to="/ai-learning" replace />;
}

export default QaPage;
