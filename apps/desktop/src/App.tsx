import "./styles.css";
import { Shell } from "./components/Shell";
import { StatusBar } from "./components/StatusBar";
import { summarizeFolders } from "./foldersSummary";
import { useFolders } from "./hooks/useFolders";
import { useHealth } from "./hooks/useHealth";
import { useReadiness } from "./hooks/useReadiness";

function App() {
  const health = useHealth();
  const healthy = health.state === "healthy";
  const readiness = useReadiness(healthy);
  const folders = useFolders(healthy);

  return (
    <Shell
      healthy={healthy}
      statusBar={
        <StatusBar health={health} readiness={readiness} folders={summarizeFolders(folders)} />
      }
    />
  );
}

export default App;
