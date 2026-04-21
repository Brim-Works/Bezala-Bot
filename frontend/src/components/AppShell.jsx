import Sidebar from './Sidebar.jsx';
import TopBar from './TopBar.jsx';

export default function AppShell({ children }) {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <TopBar />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
