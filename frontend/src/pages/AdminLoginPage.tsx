import { App as AntdApp, Button, Form, Input } from 'antd';
import { ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { adminApi } from '../api/admin';
import { isAdminAuthed, setAdminSession } from '../shared/auth';
import styles from './AdminLoginPage.module.css';

interface AdminLoginValues {
  account: string;
  password: string;
}

export function AdminLoginPage() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);

  if (isAdminAuthed()) {
    return <Navigate to="/admin/dashboard" replace />;
  }

  const submit = async (values: AdminLoginValues) => {
    setSubmitting(true);
    try {
      // 调用管理员专用登录接口
      const result = await adminApi.login({ username: values.account, password: values.password });
      // 先保存Token到localStorage，后续请求才能带上
      setAdminSession(result.admin.username, result.token, result.admin);
      // 二次验证：调用 /api/admin/me 确认Token有效且管理员状态正常
      const adminInfo = await adminApi.me();
      // 更新会话信息（用me接口返回的最新数据）
      setAdminSession(adminInfo.username, result.token, adminInfo);
      message.success('登录成功，欢迎回来');
      navigate('/admin/dashboard', { replace: true });
    } catch (error) {
      message.error(error instanceof Error ? error.message : '管理员登录失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className={styles.page}>
      <section className={styles.card}>
        <div className={styles.brand}><span />柿子智学 · 管理后台</div>
        <h1>管理员登录</h1>
        <p>进入运营、用户、内容与智能体管理工作台。</p>
        <Form layout="vertical" requiredMark={false} onFinish={submit} className={styles.form}>
          <Form.Item name="account" label="管理员账号" rules={[{ required: true, message: '请输入管理员账号' }]}>
            <Input data-testid="admin-account" placeholder="请输入管理员账号" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password data-testid="admin-password" placeholder="请输入管理员密码" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" data-testid="admin-login-submit" block loading={submitting} disabled={submitting} icon={<ShieldCheck size={16} />}>
            登录管理端
          </Button>
        </Form>
      </section>
    </main>
  );
}
