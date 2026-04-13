import { useEffect, useState } from 'react';
import { ProjectPicker } from './components/ProjectPicker';
import { ProjectDashboard } from './components/ProjectDashboard';

const DEFAULT_SERVER_URL = 'http://localhost:4747';

interface SelectedProject {
  name: string;
  serverUrl: string;
}

const App = () => {
  const [selectedProject, setSelectedProject] = useState<SelectedProject | null>(null);
  const [serverUrl, setServerUrl] = useState<string | null>(null);

  // Detect server: ElectroBun, same-port, or probe localhost:4747
  useEffect(() => {
    const isElectrobun = typeof (window as any).__electrobun !== 'undefined';
    const isApiServer = window.location.port === '4747';

    if (isApiServer) {
      setServerUrl(window.location.origin);
      return;
    }

    if (isElectrobun) {
      setServerUrl(DEFAULT_SERVER_URL);
      return;
    }

    // Probe default API port
    fetch(`${DEFAULT_SERVER_URL}/api/repos`, { signal: AbortSignal.timeout(2000) })
      .then(res => {
        if (res.ok) setServerUrl(DEFAULT_SERVER_URL);
      })
      .catch(() => {
        // No server found -- still show picker (it will show an error)
        setServerUrl(DEFAULT_SERVER_URL);
      });
  }, []);

  // Waiting for server detection
  if (!serverUrl) {
    return (
      <div className="min-h-screen bg-void flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-purple-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!selectedProject) {
    return (
      <ProjectPicker
        onSelectProject={(name, url) => setSelectedProject({ name, serverUrl: url })}
        serverUrl={serverUrl}
      />
    );
  }

  return (
    <ProjectDashboard
      projectName={selectedProject.name}
      serverUrl={selectedProject.serverUrl}
      onBack={() => setSelectedProject(null)}
    />
  );
};

export default App;
