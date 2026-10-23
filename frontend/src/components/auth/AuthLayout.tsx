import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';
import styles from './AuthLayout.module.css';
import { readDaryaTheme } from '../../features/tide/theme/daryaTheme';
import { readAdminTheme } from '../../admin/workbenchApi';

export type AuthMode = 'login' | 'register';

interface AuthLayoutProps {
  mode: AuthMode;
  children: ReactNode;
  product?: 'legacy' | 'darya';
  adminMode?: boolean;
}

const COPY = {
  login: {
    kicker: '欢迎回来',
    kickerLead: '登录后继续你的学习计划',
    heroTitle: (
      <>
        从今天开始，
        <br />
        学得更懂自己
      </>
    ),
    heroLead: '基于你的学习画像，整理知识路径、薄弱点，以及此刻最值得完成的下一步。',
    tags: ['画像驱动', '路径清晰', '持续成长'],
    mobileTagLine: '画像驱动 · 路径清晰 · 持续成长'
  },
  register: {
    kicker: '创建学习账号',
    kickerLead: '下一步将构建你的专属学习画像',
    heroTitle: (
      <>
        先认识自己，
        <br />
        再开始高效学习
      </>
    ),
    heroLead: '创建账号后，用几分钟构建专属学习画像，让每一次学习都有明确方向。',
    tags: ['画像驱动', '路径清晰', '持续成长'],
    mobileTagLine: '画像驱动 · 路径清晰 · 持续成长'
  }
} as const;

function BranchImage() {
  return (
    <>
      <img
        className={styles.branch}
        src="/src/assets/generated/persimmon-branch.png"
        alt=""
        aria-hidden="true"
        draggable={false}
      />
      <img
        className={`${styles.branch} ${styles.branchMobile}`}
        src="/src/assets/generated/persimmon-branch.png"
        alt=""
        aria-hidden="true"
        draggable={false}
      />
    </>
  );
}

export function AuthLayout({ mode, children, product = 'legacy', adminMode = false }: AuthLayoutProps) {
  const copy = COPY[mode];
  const isLogin = mode === 'login';
  const darya = product === 'darya';
  // 登录/注册页背景跟随当前皮肤（kaki 默认 / ocean），不再写死单一主题图。
  const theme = darya ? (adminMode ? readAdminTheme() || readDaryaTheme() : readDaryaTheme()) : 'kaki';
  const loginPath = darya ? '/darya/login' : '/login';
  const registerPath = darya ? '/darya/register' : '/register';

  return (
    <main className={`${styles.shell} ${styles.fadeIn} ${darya ? styles.daryaShell : ''}`} data-darya-gateway={darya ? 'true' : undefined} data-auth-theme={darya ? theme : undefined}>
      {!darya && <span className={styles.inkMountains} aria-hidden="true" />}
      {!darya && <BranchImage />}
      {darya && <span className={styles.daryaCoast} data-auth-theme={theme} aria-hidden="true" />}

      <section className={styles.brandColumn} aria-labelledby="auth-brand-title">
        <header className={styles.brandHeader}>
          <img
            src="/assets/brand/kaki-logo-mark.png"
            alt=""
            aria-hidden="true"
            draggable={false}
            className={styles.brandLogoImg}
          />
          <span className={styles.brandName}>
            <strong>柿子智学</strong>
            <span>{adminMode ? 'DARYA · 管理空间' : darya ? 'DARYA · 个性化学习伙伴' : '因材施教 · 循序成长'}</span>
          </span>
        </header>

        <div className={styles.hero}>
          <h1 id="auth-brand-title" className={styles.heroTitle}>
            {adminMode ? <>让每一次处理，<br />都有清楚的依据。</> : darya ? <>从此刻的问题开始，<br />慢慢走到清楚。</> : copy.heroTitle}
          </h1>
          <p className={styles.heroLead}>{adminMode ? '从用户支持到教学质量，定位真实对象、核对运行记录，并留下可追溯的处理结果。' : darya ? 'DARYA 会保留对话、理解你的选择，并在真正需要时把可用资料放到你面前。' : copy.heroLead}</p>
          <p className={styles.quietLine} aria-label={darya ? 'DARYA 产品说明' : '柿子智学能力'}>
            {adminMode ? '运营 · 支持 · 质量 · 运行' : darya ? '有问题就问，想练就练，DARYA 会陪你一步步走清楚。' : '因材施教 · 循序成长'}
          </p>
          {!darya && <p className={styles.tagsMobileLine} aria-hidden="true">{copy.mobileTagLine}</p>}
        </div>
      </section>

      <section className={styles.formColumn} aria-label={isLogin ? '登录表单' : '注册表单'}>
        <div className={styles.formCard}>
          {!adminMode && <div className={styles.switcher} role="tablist" aria-label="登录与注册切换">
            <Link
              to={loginPath}
              role="tab"
              aria-current={isLogin ? 'page' : undefined}
              aria-selected={isLogin}
              className={`${styles.switcherItem} ${isLogin ? styles.switcherItemActive : ''}`.trim()}
            >
              登录
            </Link>
            <Link
              to={registerPath}
              role="tab"
              aria-current={!isLogin ? 'page' : undefined}
              aria-selected={!isLogin}
              className={`${styles.switcherItem} ${!isLogin ? styles.switcherItemActive : ''}`.trim()}
            >
              注册
            </Link>
          </div>}

          <header className={styles.formHeader}>
            <h2>{adminMode ? '管理员登录' : darya ? (isLogin ? '欢迎回来' : '第一次来到这里') : copy.kicker}</h2>
            <p>{adminMode ? '使用独立管理权限进入柿子智学管理工作台。' : darya ? (isLogin ? '接回你与 DARYA 的对话。' : '创建账号后，可以先让 DARYA 认识你，也可以直接开始。') : copy.kickerLead}</p>
          </header>

          {children}
        </div>
      </section>
    </main>
  );
}
