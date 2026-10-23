import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArtifactShell } from '../components/artifact/ArtifactShell';
import styles from './ArtifactDetailPage.module.css';

export function ArtifactDetailPage() {
  const { artifactId } = useParams<{ artifactId: string }>();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const numericId = Number(artifactId);
  if (!Number.isFinite(numericId) || numericId <= 0) return <div className={styles.state}>学习产物地址无效。</div>;
  return <div className={styles.page} data-testid="unified-artifact-detail">
    <button className={styles.back} onClick={() => navigate(-1)}>← 返回来源页面</button>
    <div className={styles.shellHost}>
      <ArtifactShell artifactId={numericId} initialVersionNumber={Number(search.get('version')) || undefined} context={{ mode: 'page' }} />
    </div>
  </div>;
}

export default ArtifactDetailPage;
