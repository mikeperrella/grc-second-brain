import "./App.css";

// A static page: M2c has no live sidecar status in the window yet. The status display arrives in
// M2d together with the Zod schema that every `sidecar_health` payload must pass through, so this
// file deliberately calls no Tauri command.
function App() {
  return (
    <main className="container">
      <h1>GRC Second Brain</h1>
      <p>Local-first, air-gapped GRC workspace.</p>
      <p>Sidecar status appears here in the next milestone.</p>
    </main>
  );
}

export default App;
