import { App as AntdApp, Form, Input } from 'antd';
import { AtSign, LockKeyhole, UserRound, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { authApi } from '../api/auth';
import { adminApi } from '../api/admin';
import { useAuthStore } from '../stores/authStore';
import { setAdminSession } from '../shared/auth';
import { AuthLayout } from '../components/auth/AuthLayout';
import { canonicalDaryaRedirect } from '../utils/daryaRedirect';
import styles from './AuthPage.module.css';

interface AuthPageProps {
  mode: 'login' | 'register';
  product?: 'legacy' | 'darya';
}

interface AuthFormValues {
  nickname?: string;
  phone: string;
  password: string;
}

function getRedirectTarget(search: string, darya = false) {
  const redirect = new URLSearchParams(search).get('redirect');
  if (!redirect || !redirect.startsWith('/') || redirect.startsWith('//')) return null;
  if (redirect.startsWith('/login') || redirect.startsWith('/register')) return null;
  return darya ? canonicalDaryaRedirect(redirect) : redirect;
}

export function AuthPage({ mode, product = 'legacy' }: AuthPageProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntdApp.useApp();
  const isRegister = mode === 'register';
  const darya = product === 'darya';
  const setSession = useAuthStore((state) => state.setSession);
  const [form] = Form.useForm<AuthFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const adminMode = !isRegister && new URLSearchParams(location.search).get('mode') === 'admin';

  useEffect(() => {
    document.body.classList.add('auth-route-active');
    return () => {
      document.body.classList.remove('auth-route-active');
    };
  }, []);

  const submit = async (values: AuthFormValues) => {
    setSubmitting(true);
    try {
      // 管理员登录模式
      if (adminMode && !isRegister) {
        const username = values.phone.trim();
        const result = await adminApi.login({ username, password: values.password });
        setAdminSession(result.admin.username, result.token, result.admin);
        // 二次验证管理员状态
        const adminInfo = await adminApi.me();
        setAdminSession(adminInfo.username, result.token, adminInfo);
        message.success('登录成功，欢迎回来');
        navigate('/admin/dashboard', { replace: true });
        return;
      }

      // 普通用户登录/注册
      const username = values.phone.trim();
      const email = values.nickname?.includes('@') ? values.nickname : undefined;
      const result = isRegister
        ? await authApi.register({ username, password: values.password, email })
        : await authApi.login({ username, password: values.password });

      setSession(result.token, result.user);
      message.success(isRegister ? '注册成功' : '登录成功');

      const redirect = getRedirectTarget(location.search, darya);
      // DARYA 注册/登录直接进入主对话：对你的了解由对话中的记忆与画像智能体完成，不再经过首用中转页。
      // 旧版 Kaki 仍按画像状态走 /onboarding。
      const target = darya
        ? (redirect || '/darya')
        : result.user.hasProfile
          ? (redirect || '/')
          : '/onboarding';
      navigate(target, { replace: true });
    } catch (error) {
      message.error(error instanceof Error ? error.message : (adminMode ? '管理员登录失败' : '登录失败'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout mode={mode} product={product} adminMode={adminMode}>
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={submit}
        className={styles.form}
        autoComplete="on"
      >
        {isRegister && (
          <Form.Item
            name="nickname"
            label="邮箱或昵称"
            tooltip="选填，若填入邮箱将作为当前用户的联系邮箱"
            validateTrigger={['onChange', 'onBlur']}
          >
            <Input
              data-testid="auth-nickname"
              placeholder="可选，用于当前用户资料"
              autoComplete="email"
              prefix={<AtSign size={16} aria-hidden="true" />}
            />
          </Form.Item>
        )}
        <Form.Item
          name="phone"
          label={adminMode ? "管理员账号" : "用户名"}
          rules={[{ required: true, message: adminMode ? '请输入管理员账号' : '请输入用户名' }]}
          validateTrigger={['onChange', 'onBlur']}
        >
          <Input
            data-testid="auth-phone"
            placeholder={adminMode ? "请输入管理员账号" : "请输入用户名"}
            autoComplete="username"
            prefix={adminMode ? <ShieldCheck size={16} aria-hidden="true" /> : <UserRound size={16} aria-hidden="true" />}
          />
        </Form.Item>
        <Form.Item
          name="password"
          label="密码"
          rules={[
            { required: true, message: '请输入密码' },
            { min: 6, message: '密码至少需要 6 个字符' }
          ]}
          validateTrigger={['onChange', 'onBlur']}
        >
          <Input.Password
            data-testid="auth-password"
            placeholder={isRegister ? '至少 6 个字符' : '请输入密码'}
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            prefix={<LockKeyhole size={16} aria-hidden="true" />}
          />
        </Form.Item>
        {isRegister && (
          <Form.Item
            name="confirmPassword"
            label="确认密码"
            dependencies={['password']}
            rules={[
              { required: true, message: '请再次输入密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('password') === value) return Promise.resolve();
                  return Promise.reject(new Error('两次输入的密码不一致'));
                }
              })
            ]}
            validateTrigger={['onChange', 'onBlur']}
          >
            <Input.Password
              data-testid="auth-confirm-password"
              placeholder="请再次输入密码"
              autoComplete="new-password"
              prefix={<LockKeyhole size={16} aria-hidden="true" />}
            />
          </Form.Item>
        )}
        <div className={styles.submitButtonWrapper}>
          <button
            data-testid="auth-submit"
            type="submit"
            className={styles.submitButton}
            disabled={submitting}
            aria-busy={submitting}
          >
            {submitting
              ? isRegister
                ? '创建中…'
                : adminMode
                  ? '登录中…'
                  : '登录中…'
              : isRegister
                ? '创建账号'
                : adminMode
                  ? '登录管理端'
                  : '登录并继续'}
          </button>
        </div>
        {!isRegister && adminMode && (
          <p className={styles.footnote}>
            <button
              type="button"
              onClick={() => navigate('/darya/login', { replace: true })}
              className={styles.adminToggle}
            >
              ← 返回用户登录
            </button>
          </p>
        )}
        {isRegister && (
          <p className={styles.footnote}>
            已有账号？
            <Link to={darya ? '/darya/login' : '/login'}>
              返回登录
            </Link>
          </p>
        )}
        {!isRegister && !adminMode && (
          <p className={styles.adminEntryFootnote}>
            <button
              type="button"
              onClick={() => navigate('/darya/login?mode=admin', { replace: true })}
              className={styles.adminEntryLink}
            >
              管理员入口 →
            </button>
          </p>
        )}
      </Form>
    </AuthLayout>
  );
}
