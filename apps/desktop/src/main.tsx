import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { requireRoot } from "./requireRoot";

ReactDOM.createRoot(requireRoot(document.getElementById("root"))).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
