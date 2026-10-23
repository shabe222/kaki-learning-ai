import type { AdminRoute } from './adminRoutes';
import { OverviewPage } from './OverviewPage';
import { UsersPage } from './UsersPage';
import { QualityPage } from './QualityPage';
import { TasksPage } from './TasksPage';
import { AnnouncementsAdminPage } from './AnnouncementsAdminPage';
import { SystemPage } from './SystemPage';
export function AdminPage({route}: {route:AdminRoute}) {
  return <div className="admin-edition" data-testid={`admin-page-${route.module}`}>
    {route.module==='dashboard'&&<OverviewPage/>}{route.module==='users'&&<UsersPage/>}{route.module==='quality'&&<QualityPage/>}{route.module==='operations'&&<TasksPage/>}{route.module==='content'&&<AnnouncementsAdminPage/>}{route.module==='system'&&<SystemPage/>}
  </div>;
}
