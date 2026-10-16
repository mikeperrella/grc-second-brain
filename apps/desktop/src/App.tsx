import "./styles.css";
import { useState } from "react";
import { Shell, type ScreenId } from "./components/Shell";
import { StatusBar } from "./components/StatusBar";
import { summarizeFolders } from "./foldersSummary";
import { useFolders } from "./hooks/useFolders";
import { useHealth } from "./hooks/useHealth";
import { useReadiness } from "./hooks/useReadiness";
import { Screens } from "./screens";

function App() {
  const health = useHealth();
  const healthy = health.state === "healthy";
  const readiness = useReadiness(healthy);
  const folders = useFolders(healthy);
  const [screen, setScreen] = useState<ScreenId>("folders");

  return (
    <Shell
      healthy={healthy}
      current={screen}
      onNavigate={setScreen}
      statusBar={
        <StatusBar
          health={health}
          readiness={readiness}
          folders={summarizeFolders(folders.call)}
        />
      }
    >
      <Screens
        screen={screen}
        healthy={healthy}
        readiness={readiness}
        folders={folders}
        onNavigate={setScreen}
      />
    </Shell>
  );
}

export default App;
